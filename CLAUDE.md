# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`rms-host-voyager` is the Voyager host plug-in for `oops` (rms-oops), built on the
`oops.Host` contract, as are its siblings `rms-host-galileo`, `rms-host-cassini` and
`rms-host-new-horizons`. The one host is ISS, as `VoyagerISS`.

## Standards

Tooling and conventions follow **rms-polymath**. `.claude/rules/*.md`, which were copied
from it, are the authoritative coding, testing, documentation, dependency and environment
standards. The repo layout, CI and scripts come from `rms-devenv/repo_template`, adapted
as rms-polymath adapted them, by way of `rms-host-cassini` and `rms-host-galileo`. Model
changes to packaging, CI or scripts on rms-polymath's `pyproject.toml`, `.flake8`,
`.github/workflows/` and `scripts/`, and keep them in step with the sibling hosts. The
rules' references to the two published `.pyi` stubs and `stubtest` apply here to the
stubs described below.

- Hand-aligned columns (assignments, dict colons, `from x     import`) are deliberate.
  Never run `ruff format` or any other auto-formatter.
- Keep the file banners: a line of 90 `#` characters at the top and bottom of each module,
  with a `# host_voyager/<file>.py` header (`.pyi` for a stub).

## Current state

- `scripts/run-all-checks.sh` is the single source of truth for the checks: ruff, flake8
  `E12`/`E13`, mypy on `tests/`, pytest, pyroma, stubtest, pip-audit, the Sphinx build
  (`-W -n`), codespell and PyMarkdown. `ruff format` is disabled.
  `.github/workflows/run-tests.yml` runs the same set. `scripts/setup-venv.sh` builds the
  `venv/` that the scripts expect; set `VENV` to use another.
- Until rms-oops is released with the host contract, `pyproject.toml` installs rms-oops
  from its PR branch through a git URL.
- The type information is published in two stubs: `host_voyager/__init__.pyi` declares
  `VoyagerISS` in full, and `host_voyager/oops/__init__.pyi` re-exports it. `stubtest`
  checks them against the runtime, so adding, renaming or re-signing a public member
  means updating `host_voyager/__init__.pyi` in the same change. The `[tool.mypy]
  exclude`, the override list in `pyproject.toml` and `.stubtest-allowlist` keep it off
  the stub-less modules. The two `type: ignore[override]` comments are real mismatches
  with `oops.Host`: `from_file` names its first parameter `fileinfo`, and neither method
  takes `**kwargs`, so an unknown keyword raises `TypeError`.
- `docs/conf.py` resolves the short class names in docstrings (`FCPath`, `Observation`,
  ...) through intersphinx with `_SHORT_NAMES`. Add a name there when a nitpicky build
  reports a class from another RMS package.
- Tests never touch SPICE kernels, the spicedb database or the archive. The host tests are in
  `tests/oops/`, whose `conftest.py` replaces SPICE with fakes for every test (a fake
  FDS clock, C-kernel records and a stand-in for the camera's `SpiceFrame`; spicedb's own
  `furnish_kernels` runs over recording fakes of `cspyce.furnsh` and `cspyce.unload`) and
  writes small synthetic products: a VICAR image file of the raw 800x800 FOV and its
  PDS3 label. It also copies the
  oops Path and Frame registries, caches, and per-class waypoint and wayframe pools for
  each test, because oops gives a path or frame the ID of an earlier one defined in
  exactly the same way. `test_files/` holds cut-down copies of real VGISS_6109 files:
  the first 30 rows of the raw image and supplemental indexes, two labels, and a GEOMED
  image file cut after its VICAR header.

## Architecture

- The oops support lives in `host_voyager.oops`: `_Voyager` in `_voyager.py` (kernels,
  paths, the encounter timeline, the clock) and `VoyagerISS` in `iss.py`. `VoyagerISS`
  calls `_register()` at import time, and `oops/__init__.py` imports it.
  `host_voyager/__init__.py` imports the host lazily, through a PEP 562 `__getattr__`, so
  that importing `host_voyager` does not import oops.
- Nothing loads at import. `VoyagerISS.initialize()` is public, keyword-only and
  idempotent, and `from_file()` and `from_index()` call it. Kernels are furnished
  through `spicedb`; NAIF's merged cruise trajectories (`CRUISE_SPKS`) are furnished
  first, by fixed path, so that the encounter and planetary kernels take precedence.
  When a cruise file is new, every SPK file furnished before them is reloaded file by
  file in `spicedb.FURNISHED_ABSPATHS` order, never through its spicedb records, which
  can name several files.
- Every label and image file is read through `VoyagerISS._read_files()`. A label is
  parsed with the strict method (`_PDS3_DEFAULTS`), which reads every current VGISS
  label, so the host keeps oops's default `_correct_label_text()`. The array always
  comes from the VICAR file. A failure to read or interpret a file becomes a host error
  naming the file: a header that cannot be parsed or data cut short, an image of several
  bands, an array whose shape differs from its label, and an array whose shape differs
  from the host's FOV (checked in `_make_snapshot()`). Index and supplement rows get no
  checks.
- `_merge()` merges the two labels: the PDS3 label wins every item, and the header fills
  in only where the label lacks an item or gives a PDS3 null text. Its docstring gives
  the evidence and cites the label-differences issue. Null texts are dropped from `dict`
  (recursively), never from `label_dict` or `header_dict`, which are what the parsers
  give.
- Never look Voyager ISS pointing up by time. The scan platform C kernels hold one record
  per image, stamped at its `SPACECRAFT_CLOCK_STOP_COUNT`, which can be minutes after
  `STOP_TIME`. `_camera_frame()` calls `ckgp` at the count's ticks (tolerance 20); a
  record gives a fixed `Cmatrix` (`spice_frame_is_frozen` True), and no record gives the
  camera's `SpiceFrame` (False), which raises the cspyce error when it is used. Nothing
  checks pointing at load.
- The clock partition of a count is always the one `_Voyager.IMAGING_PERIODS` gives
  (`sclk_ticks()` returns None otherwise). The time is `STOP_TIME`, else `IMAGE_TIME`,
  else the count's time through `sct2e`. The count is `SPACECRAFT_CLOCK_STOP_COUNT`, else
  `IMAGE_NUMBER`, else the C-number of the file name.
- `from_index()` returns a generator expression over `Host._index_rows()` (pairing by
  position, `INDEX_FILE_KEY` and `SUPPLEMENT_FILE_KEY` on `VOLUME_NAME`), so call-time
  errors surface at the call. The supplement is optional (`require_supplement` only for
  an explicit path), and `_supplement_path()` maps `_raw_image_index` to
  `_supplemental_index`. `dict` is the row as the table reader gives it, masks included;
  the observation is built from the values whose mask is False and that are not null
  texts. `filepath` is the relative `VOLUME_NAME/FILE_SPECIFICATION_NAME`.
- `if_scale` is the label's `REFLECTANCE_SCALING_FACTOR`, else the header's I/F line
  divided by 10000 with the Voyager 1 corrections (Saturn x3.345, SODIUM x40, CH4_U
  x12.5); a `FlatCalib('I/F', if_scale)` is attached when it exists.
- Running anything for real needs SPICE kernels through `spicedb` (`SPICE_PATH`, or
  `$OOPS_RESOURCES/SPICE`, plus `SPICE_SQLITE_DB_NAME`).

## Repo etiquette

- Commit subjects are a plain, capitalized, imperative sentence, without a type prefix
  or a trailing period. PRs are squash-merged onto `main`.
- Branch names: `<initials>_<YYMMDD>_<topic>`.
- Versions come from `setuptools_scm`; never hand-write a version.
