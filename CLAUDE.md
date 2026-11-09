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
- The `oops.Host` API arrived in rms-oops 0.4.0, the minimum version in `pyproject.toml`.
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
  barycenters NAIF merged into them; only their spacecraft segments are recorded, so
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
  header gives no count; `_clock_count()` derives it from `IMAGE_NUMBER`, which matches
  for all but five VGISS images. A frame `timeshift` ("F") has no effect on the fixed
  matrix.
- An image with no time or (without a `frame` override) no record raises
  `_UnusableImageError`, a `HostError`, from `from_file()`, and `from_index()` leaves it
  out. Across VGISS that is 17 images with no time and 2,137 with no record, 1,090 of them
  in VGISS_8210.
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
  `EXPOSURE_DURATION` is in seconds in a label but milliseconds in the VICAR header. The
  index of VGISS_6201 gives the null exposure -99.9999 for one dark frame; the host uses 0.
- The product type decides the FOV: `GEOMETRICALLY_CORRECTED_IMAGE` is 1000x1000 and
  undistorted, every other product 800x800 (raw geometry). A VICAR header has no
  `PRODUCT_TYPE`; `_vicar_to_pds3()` infers it from the VICAR tasks (`GEOMA` means GEOMED).
- `_TARGET_NAME_REPAIRS`, `_PLANET_TARGETS` and `_TARGET_STARS` in `iss.py` cover every
  `TARGET_NAME` in the VGISS raw image indexes; the `*999` volumes are cumulative indexes
  that repeat every row, so leave them out of any survey.
- Running anything needs SPICE kernels through `spicedb` (`SPICE_PATH`, or
  `$OOPS_RESOURCES/SPICE`, plus `SPICE_SQLITE_DB_NAME`). `spicedb` currently ships inside
  rms-oops.

## Repo etiquette

- Commit subjects are a plain, capitalized, imperative sentence with no type prefix and no
  trailing period. PRs are squash-merged onto `main`.
- Branch names: `<initials>_<YYMMDD>_<topic>`.
- Versions come from `setuptools_scm`; never hand-write a version.
