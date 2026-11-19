# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`rms-host-voyager` is the Voyager host plug-in for `oops` (rms-oops). It is a port of
`rms-oops/src/oops/hosts/voyager/` to the newer `oops.Host` / `oops.host` API, part of
splitting the mission hosts out of rms-oops (see the siblings `rms-host-galileo` and
`rms-host-cassini`). Galileo, a single VICAR imaging camera, is the closest model. ISS is
ported, as `VoyagerISS`.

## Standards

Tooling and conventions follow **rms-polymath**. `.claude/rules/*.md`, which were copied from
it, are the authoritative coding, testing, documentation, dependency and environment
standards. The repo layout, CI and scripts come from `rms-devenv/repo_template`, adapted as
rms-polymath adapted them, by way of `rms-host-cassini` and `rms-host-galileo`. Model
changes to packaging, CI or scripts on rms-polymath's `pyproject.toml`, `.flake8`,
`.github/workflows/` and `scripts/`, and keep them in step with rms-host-cassini and
rms-host-galileo. The rules' references to the two published `.pyi` stubs and `stubtest`
apply here to the stubs described below.

- Hand-aligned columns (assignments, dict colons, `from x     import`) are deliberate. Never
  run `ruff format` or any other auto-formatter.
- Keep the file banners: a line of 90 `#` characters at the top and bottom of each module,
  with a `# host_voyager/<file>.py` header (`.pyi` for a stub).

## Current state

- `scripts/run-all-checks.sh` is the single source of truth for the checks: ruff, flake8
  `E12`/`E13`, mypy on `tests/`, pytest, pyroma, stubtest, pip-audit, the Sphinx build
  (`-W -n`), codespell and PyMarkdown. `ruff format` is disabled.
  `.github/workflows/run-tests.yml` runs the same set. `scripts/setup-venv.sh` builds the
  `venv/` that the scripts expect; set `VENV` to use another.
- The `oops.Host` API arrived in rms-oops 0.4.0 and `oops.OopsSpiceError` arrives in
  0.5.0, the minimum version in `pyproject.toml`.
- The type information is published in two stubs: `host_voyager/__init__.pyi` declares
  `VoyagerISS` in full, and `host_voyager/oops/__init__.pyi` re-exports it. `stubtest`
  checks them against the runtime in the check script and in CI, so adding, renaming or
  re-signing a public member means updating `host_voyager/__init__.pyi` in the same
  change. The `[tool.mypy] exclude`, the override list in `pyproject.toml` and
  `.stubtest-allowlist` keep it off the stub-less modules. The two `type: ignore[override]`
  comments are real mismatches with `oops.Host`: `from_file` names its first parameter
  `fileinfo`, and `from_index` returns `list[Snapshot]`.
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
  furnishes the IK, FK and SCLK kernels of both spacecraft. The encounter SPKs cover only
  weeks around closest approach, so `initialize()` first furnishes NAIF's merged cruise
  trajectories (`CRUISE_SPKS`, by fixed path; they are not in `SPICE.db`). Furnished
  first, they yield to the planetary and encounter kernels, including for the planet
  barycenters NAIF merged into them. SPKs another host furnished earlier are furnished
  again after them (spicedb's `FURNISHED_ABSPATHS` and `FURNISHED_INFO`, `fast=False`),
  since the cruise files span 1977-2021. Only their spacecraft segments are recorded, so
  `used_kernels()` names them for the spacecraft alone. Galileo's month-by-month
  kernel tables are deliberately absent. `used_kernels()` converts the observation times
  to TAI, which is what `spicedb.used_basenames()` compares; Galileo passes TDB.
- `TIMELINE` assigns each time to an encounter with the boundaries the rms-oops host used
  for VICAR files (1980, 1983, 1987); they fall in cruise, so they serve both spacecraft.
- **Never look Voyager ISS pointing up by time.** The scan platform C kernels hold one
  record per image, stamped at the image's `SPACECRAFT_CLOCK_STOP_COUNT` (its FDS image
  number, `hhhhh:mm:001`), which is often whole FDS minutes (48 s each; 768.7 s for
  C3450201) after the true shutter close, `STOP_TIME`. A time lookup near `STOP_TIME`,
  whether `ckgp` at mid-exposure as in the rms-oops host or a `SpiceType1Frame`, returns a
  neighboring image's record. `docs/pointing.rst` gives the evidence: fits of real images
  show that the record at the image's own count holds its pointing, and that `STOP_TIME`,
  not the count, is the shutter time.
- So the time is `STOP_TIME`, else `IMAGE_TIME`, and never the clock count. The default
  frame is an unregistered `Cmatrix` relative to J2000, from the record found by the clock
  count (`_camera_frame()`, tolerance 20 ticks). Labels and VICAR headers give no clock
  partition; `_Voyager.sclk_ticks()` picks the partition nearest `STOP_TIME`. A VICAR
  header gives the count as its FDS image number in `LAB02`, which `_header_items()`
  spells as a label does (`9096:16:001` for FDS `09096.16`); an index row without the
  supplement gives only `IMAGE_NUMBER`, from which `_clock_count()` derives it. A frame
  `timeshift` ("F") has no effect on the fixed matrix.
- Missing SPICE data for the pointing raises `oops.OopsSpiceError` (rms-oops 0.5.0),
  built with `OopsSpiceError.from_spice_error()`: "no Voyager 1 ISS NAC pointing at
  <midtime> for <image file>: SPICE(CKINSUFFDATA)", the midtime after any cadence or
  timeshift override, the SPICE exception as its cause. `_camera_frame()` catches exactly
  `(LookupError, OSError, RuntimeError, ValueError)`, what cspyce raises under either
  error action, around `sclk_ticks()`, `ckgp` and `pxform`, and then returns a
  `_NoPointingFrame`, which raises that error whenever it is evaluated. `from_file()`
  evaluates the frame at the midtime, so it raises at load time; `from_index()` does not,
  so it keeps the row. Neither checks anything when a `frame` override is given. An image
  for which no source read gives the time, exposure, camera, spacecraft, mission phase
  or (without a `frame` override) clock count raises `oops.host.HostFormatError`, naming
  the item and the file, from both methods. Across the VGISS labels that is 17 images
  with no time and 2,137 with no record, 1,090 of them in VGISS_8210; the index of
  VGISS_6201 gives no exposure for C3940530, so `from_index()` raises for that volume.
- `from_index()` accepts only a `VGISS_xxxx_raw_image_index.lbl`, one row per RAW image,
  never the full `VGISS_xxxx_index.lbl` (one row per label: RAW, CLEANED, CALIB, GEOMED
  and the reseau and tiepoint tables), because OPUS archives only the raw images. The
  preexisting `VGISS_xxxx_supplemental_index.lbl` matches the raw image index row for
  row, so the two join through `oops.Host._read_index_rows()`. The default
  `oops.Host.SUPPLEMENT` search would look for `..._raw_image_supplemental_index`, so the
  host names the supplement itself by replacing `_raw_image_index` with
  `_supplemental_index`. It does so even for given `row_dicts`, which rejects the rows of
  a full index from `oops.Host.from_index()`, and joins the supplement by `VOLUME_NAME`
  and `FILE_SPECIFICATION_NAME` into rows that lack `STOP_TIME`, as that dispatch passes
  them. The supplement adds `START_TIME` and `STOP_TIME` with fractional
  seconds; the index's `IMAGE_TIME` has whole seconds. Each Snapshot's `filepath` names
  the raw image file (`PRODUCT_ID`), never the label in `FILE_SPECIFICATION_NAME`.
- VGISS indexes name the volume in `VOLUME_NAME`, not `VOLUME_ID`, and the camera in
  `INSTRUMENT_NAME` ("NARROW ANGLE CAMERA" or "WIDE ANGLE CAMERA"). The index label itself
  identifies no host or instrument, so detection relies on `_detect_in_row()`.
- `test_files/` holds cut-down copies of real VGISS_6109 products from
  <https://pds-rings.seti.org/holdings/>: the first 30 rows of the raw image and
  supplemental indexes, which include C3450201 and both cameras; the C3450201 RAW and
  GEOMED labels; and the RAW and GEOMED images truncated after their VICAR headers. Their
  `_edited` names carry through the labels' pointers.
- `from_file()` follows the operator's rules of 2026-11-18, shared by the four host
  packages: an image input reads only the image file (no `data` with `astrometry=True`);
  a label input reads the label and, unless `astrometry=True`, the image file its
  `^IMAGE` names, found case-tolerantly. With `astrometry=True` a label input needs no
  image file. The metadata come from whatever was read. `_header_items()` converts the
  VICAR header's `LAB02`, `LAB03` and `LAB05` fields and its tasks to the label's names
  and forms, leaving out any field filled with asterisks or garbled (an hour of 24 or
  more, a year outside 77-99, PICNO bytes, FDS 00000.00), and respells the target fields
  that no label uses through `_HEADER_TARGET_SPELLINGS` (`ENCELADU`, `1989N1`, ...; #15).
  `_merge_items()` takes an item from whichever source gives it and, where both do,
  the source that `_RESOLUTION` names for that item, "label" or "header", the same for
  every image; never a per-image test. Every item is "label" now. The comment above
  `_RESOLUTION` gives the evidence for each choice, and #14 lists the images on which
  the winner is wrong; keep them in step. A null (`UNK`, `N/A`, `NULL` in any case,
  None, a list of nulls, and -99.9999 in the index) counts as missing. A subfield no
  source gives is absent, never None (`if_scale` included). `dict` is the merged metadata
  plus every other key of each source (`LABEL3`, ...), but never `LAB02`, `LAB03` or
  `LAB05`, whose fields it holds under PDS3 names. `label_dict` is the label (parsed by
  `from_file()` with `sources=False` unless `pds3_options` says otherwise, though a label
  that `oops.Host.from_file()` parsed arrives with `sources=True` and keeps its
  `*_source` keys; times as the parser's ISO strings, sets as lists, no END) and
  `header_dict` the header (`as_dict(unique=True)`, a repeated parameter keyed
  `TASK[0]`, `TASK[1]`, ...), each only when read; all three are JSON. `spacecraft`, the
  int 1 or 2, is how a consumer tells Voyager 1 from Voyager 2. Consumers read the
  documented subfields, including `spacecraft_clock_start_count` and
  `spacecraft_clock_stop_count`, rather than parsing `dict`.
- `filepath`, `basename`, `abspath` and `image_url` name the image file on every input.
  They are absent for parsed content with no file path (a `Pds3Label` made from text, a
  `VicarImage` made from an array), and `abspath` is absent for a remote image file that a
  label read with `astrometry=True` did not retrieve.
- The data are read where the VICAR header places them, not where the label does: the
  label of C2673426_GEOMED in VGISS_7205 points a record early, into its two-record
  header, and that of C3489611_GEOMED in VGISS_6112 a record late. The header supplies
  `if_scale`, the I/F factor of a CALIB or GEOMED product ("FOR (I/F)*10000., MULTIPLY DN
  VALUE BY x", which FICOR77 writes as `LABEL3`, divided by 10000), which no label keyword
  gives: the label's `REFLECTANCE_SCALING_FACTOR` is 3.345 times larger for Voyager 1
  images of Saturn.
- A LAB03 line pads the filter name and writes `CH4/JS` and `CH4/U`; `_filter_name()`
  respells it as the label does (`CLEAR`, `CH4_JS`) on every path.
- pdsparser's default "fast" method rejects some valid VGISS labels, such as
  `C2673426_GEOMED.LBL` of VGISS_7205, whose quoted `NOTE` value starts on the line after
  the keyword; `from_file()` then parses the label again with "strict", then "loose",
  also after a `ValueError` (an hour of 24), and raises `PdsHostError` naming the label
  if all fail. The generic `oops.Host.from_file()` parses the label itself before it
  reaches the host, so it still fails on those labels, and its default `sources=True`
  reaches the host's `label_dict`; those fixes belong in rms-oops or pdsparser.
- The labels of the RESLOC and GEOMA tables have no `^IMAGE` and raise `PdsHostError`;
  their VICAR files (`TYPE='TABULAR'`) raise `VicarHostError`, as does an image file cut
  short. Parsed content of another host raises `WrongHostError` (#17).
- Labels name the spacecraft in `INSTRUMENT_HOST_NAME` ("VOYAGER 1") and the camera in
  `INSTRUMENT_ID` (`ISSN`/`ISSW`). `IMAGE_TIME` and `STOP_TIME` are the shutter close, and
  `EXPOSURE_DURATION` is in seconds in a label but milliseconds in the VICAR header. The
  index of VGISS_6201 gives the null exposure -99.9999 for one dark frame, C3940530, whose
  label gives "UNK" for its exposure and time.
- The product type decides the FOV: `GEOMETRICALLY_CORRECTED_IMAGE` is 1000x1000 and
  undistorted, every other product 800x800 (raw geometry). A VICAR header has no
  `PRODUCT_TYPE`; `_header_items()` infers it from the VICAR tasks (`GEOMA` means GEOMED).
- `_TARGET_NAME_REPAIRS`, `_PLANET_TARGETS` and `_TARGET_STARS` in `iss.py` cover every
  `TARGET_NAME` in the VGISS raw image indexes, and, after `_HEADER_TARGET_SPELLINGS`,
  every target field of their VICAR headers; the `*999` volumes are cumulative indexes
  that repeat every row, so leave them out of any survey.
- Running anything needs SPICE kernels through `spicedb` (`SPICE_PATH`, or
  `$OOPS_RESOURCES/SPICE`, plus `SPICE_SQLITE_DB_NAME`). `spicedb` currently ships inside
  rms-oops.

## Repo etiquette

- Commit subjects are a plain, capitalized, imperative sentence with no type prefix and no
  trailing period. PRs are squash-merged onto `main`.
- Branch names: `<initials>_<YYMMDD>_<topic>`.
- Versions come from `setuptools_scm`; never hand-write a version.
