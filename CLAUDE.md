# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`rms-host-voyager` is the Voyager host plug-in for `oops` (rms-oops). It is a port of
`rms-oops/src/oops/hosts/voyager/` to the newer `oops.Host` / `oops.host` API, part of
splitting the mission hosts out of rms-oops (see the siblings `rms-host-galileo` and
`rms-host-cassini`). Galileo, a single VICAR imaging camera, is the closest model. ISS is
not yet ported: the repository holds the packaging, CI, scripts and docs, and
`host_voyager` exports nothing.

## Standards

Tooling and conventions follow **rms-polymath**. `.claude/rules/*.md`, which were copied from
it, are the authoritative coding, testing, documentation, dependency and environment
standards. The repo layout, CI and scripts come from `rms-devenv/repo_template`, adapted as
rms-polymath adapted them, by way of `rms-host-cassini` and `rms-host-galileo`. Model
changes to packaging, CI or scripts on rms-polymath's `pyproject.toml`, `.flake8`,
`.github/workflows/` and `scripts/`, and keep them in step with rms-host-cassini and
rms-host-galileo. Some details in the rules are
polymath-specific, for example the two published `.pyi` stubs and `stubtest`. Ignore those
until this repo has the equivalent.

- Hand-aligned columns (assignments, dict colons, `from x     import`) are deliberate. Never
  run `ruff format` or any other auto-formatter.
- Keep the file banners: a line of 90 `#` characters at the top and bottom of each module,
  with a `# host_voyager/<file>.py` header.

## Current state

- `scripts/run-all-checks.sh` is the single source of truth for the checks: ruff, flake8
  `E12`/`E13`, mypy on `tests/`, pytest, pyroma, pip-audit, the Sphinx build (`-W -n`),
  codespell and PyMarkdown. `ruff format` is disabled. `.github/workflows/run-tests.yml`
  runs the same set. `scripts/setup-venv.sh` builds the `venv/` that the scripts expect;
  set `VENV` to use another.
- The `oops.Host` API is unreleased. CI, `.readthedocs.yaml` and `scripts/setup-venv.sh`
  install rms-oops from its `mrs_260925_host_reorg` branch, and `docs/conf.py` ignores the
  unresolvable `oops.Host` and `oops.host` references. Drop all four once a release has
  them.
- `docs/conf.py` resolves the short class names in docstrings (`FCPath`, `Observation`,
  ...) through intersphinx with `_SHORT_NAMES`. Add a name there when a nitpicky build
  reports a class from another RMS package.
- Coverage must stay at or above 65% (`fail_under` in `pyproject.toml`, and the project and
  patch targets in `codecov.yml`). The ubuntu / Python 3.13 CI job uploads
  to Codecov and fails if the upload fails. The upload authenticates through GitHub OIDC
  (`use_oidc`), so it needs no `CODECOV_TOKEN` secret.
- Tests never touch SPICE kernels or spicedb. The host tests belong in `tests/oops/`, whose
  `conftest.py` replaces them with fakes for every test, as rms-host-galileo's does.

## Architecture gotchas

- The oops support lives in the `host_voyager.oops` subpackage: `_Voyager` in
  `_voyager.py` and `VoyagerISS` in `iss.py`. `VoyagerISS` calls `_register()` at import
  time, and `oops/__init__.py` imports it. `Host.from_file()` dispatches through the
  registered hosts' `_detect_in_*` methods, so the host is invisible until
  `host_voyager.oops` (or its module) is imported.
- `host_voyager/__init__.py` imports the hosts lazily, through a PEP 562 `__getattr__`, so
  that importing `host_voyager` does not import oops. Keep any import of
  `host_voyager.oops` out of `host_voyager/__init__.py`.
- Kernels are furnished through `spicedb` (`furnish_inst`, `furnish_ck`, `furnish_spk`
  for spacecraft -31 and -32), as in Cassini and in the rms-oops host, not through a fixed
  list as in Galileo. Nothing is loaded at import time; `VoyagerISS.initialize()` runs on
  first use.
- `_Voyager` in `_voyager.py` is never instantiated; its state is in class attributes.
  `initialize()` furnishes the C kernels and the encounter SP kernels (by name pattern,
  `%JUP%` etc.) and defines the `VOYAGER1`/`VOYAGER2` Paths; `load_instruments()`
  furnishes the IK, FK and SCLK kernels of both spacecraft. Galileo's month-by-month
  kernel tables are deliberately absent. `used_kernels()` converts the observation times
  to TAI, which is what `spicedb.used_basenames()` compares; Galileo passes TDB.
- `TIMELINE` assigns each time to an encounter with the boundaries the rms-oops host used
  for VICAR files (1980, 1983, 1987); they fall in cruise, so they serve both spacecraft.
- The default frame is the registered chain that `initialize()` builds: a
  `SpiceType1Frame` on `VG{n}_SCAN_PLATFORM`, then a fixed `Cmatrix` to
  `VOYAGER{n}_ISS_{NAC|WAC}`. The rms-oops host instead sampled the C kernel once per
  image into an unregistered `Cmatrix`, with `action=`/`omit=` warnings when it was
  missing; neither the per-image matrix nor those options are carried over.
- `from_index()` accepts only a `VGISS_xxxx_raw_image_index.lbl`, one row per RAW image,
  never the full `VGISS_xxxx_index.lbl` (one row per label: RAW, CLEANED, CALIB, GEOMED
  and the reseau and tiepoint tables), because OPUS archives only the raw images. The
  preexisting `VGISS_xxxx_supplemental_index.lbl` matches the raw image index row for
  row, so the two join through `oops.Host._read_index_rows()`. The default
  `oops.Host.SUPPLEMENT` search would look for `..._raw_image_supplemental_index`, so the
  host names the supplement itself by replacing `_raw_image_index` with
  `_supplemental_index`. The supplement adds `START_TIME` and `STOP_TIME` with fractional
  seconds; the index's `IMAGE_TIME` has whole seconds.
- VGISS indexes name the volume in `VOLUME_NAME`, not `VOLUME_ID`, and the camera in
  `INSTRUMENT_NAME` ("NARROW ANGLE CAMERA" or "WIDE ANGLE CAMERA"). The index label itself
  identifies no host or instrument, so detection relies on `_detect_in_row()`.
- `test_files/` holds cut-down copies of real VGISS_6109 products from
  <https://pds-rings.seti.org/holdings/>: the first 30 rows of the raw image and
  supplemental indexes, which include C3450201 and both cameras; the C3450201 RAW and
  GEOMED labels; and the GEOMED image truncated after its VICAR header. Their `_edited`
  names carry through the labels' pointers.
- Labels name the spacecraft in `INSTRUMENT_HOST_NAME` ("VOYAGER 1") and the camera in
  `INSTRUMENT_ID` (`ISSN`/`ISSW`). `IMAGE_TIME` and `STOP_TIME` are the shutter close, and
  `EXPOSURE_DURATION` is in seconds in a label but milliseconds in the VICAR header.
- Running anything needs SPICE kernels through `spicedb` (`SPICE_PATH`, or
  `$OOPS_RESOURCES/SPICE`, plus `SPICE_SQLITE_DB_NAME`). `spicedb` currently ships inside
  rms-oops.

## Repo etiquette

- Commit subjects are a plain, capitalized, imperative sentence with no type prefix and no
  trailing period. PRs are squash-merged onto `main`.
- Branch names: `<initials>_<YYMMDD>_<topic>`.
- Versions come from `setuptools_scm`; never hand-write a version.
