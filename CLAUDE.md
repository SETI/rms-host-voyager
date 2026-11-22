# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`rms-host-voyager` is the Voyager host plug-in for `oops` (rms-oops). It is a port of
`rms-oops/src/oops/hosts/voyager/` to the newer `oops.Host` / `oops.host` API, part of
splitting the mission hosts out of rms-oops (see the siblings `rms-host-galileo` and
`rms-host-cassini`). Galileo, a single VICAR imaging camera, is the closest model. ISS is
ported, as `VoyagerISS`.

## Standards

Tooling and conventions follow **rms-polymath**. `.claude/rules/*.md`, which were copied
from it, are the authoritative coding, testing, documentation, dependency and environment
standards. The repo layout, CI and scripts come from `rms-devenv/repo_template`, adapted
as rms-polymath adapted them, by way of `rms-host-cassini` and `rms-host-galileo`. Model
changes to packaging, CI or scripts on rms-polymath's `pyproject.toml`, `.flake8`,
`.github/workflows/` and `scripts/`, and keep them in step with rms-host-cassini and
rms-host-galileo. The rules' references to the two published `.pyi` stubs and `stubtest`
apply here to the stubs described below.

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
- The `oops.Host` API arrived in rms-oops 0.4.0, and `oops.OopsSpiceError` and the
  streaming index reader arrive in 0.5.0. Until 0.5.0 is released, `pyproject.toml`
  installs the rms-oops branch `rf_261118_oops_spice_error` (SETI/rms-oops#242); it
  goes back to `rms-oops>=0.5.0` after that merges. The frame and path memory fix,
  SETI/rms-oops#252, is a separate change that CI does not install. rms-pdstable
  1.2.0 is the first that streams a table, and rms-filecache 3.0.0 the first through
  which rms-vicar reads images.
- The type information is published in two stubs: `host_voyager/__init__.pyi` declares
  `VoyagerISS` in full, and `host_voyager/oops/__init__.pyi` re-exports it. `stubtest`
  checks them against the runtime in the check script and in CI, so adding, renaming or
  re-signing a public member means updating `host_voyager/__init__.pyi` in the same
  change. The `[tool.mypy] exclude`, the override list in `pyproject.toml` and
  `.stubtest-allowlist` keep it off the stub-less modules. The two
  `type: ignore[override]` comments are real mismatches with `oops.Host`: `from_file`
  names its first parameter `fileinfo`, neither method takes `**kwargs`, and both type
  `select` as `None`, the only value they accept.
- `docs/conf.py` resolves the short class names in docstrings (`FCPath`, `Observation`,
  ...) through intersphinx with `_SHORT_NAMES`. Add a name there when a nitpicky build
  reports a class from another RMS package.
- Coverage must stay at or above 65% (`fail_under` in `pyproject.toml`, and the project
  and patch targets in `codecov.yml`). The ubuntu / Python 3.13 CI job uploads
  to Codecov and fails if the upload fails. The upload authenticates through GitHub OIDC
  (`use_oidc`) in place of a `CODECOV_TOKEN` secret.
- Tests never touch SPICE kernels or spicedb. The host tests belong in `tests/oops/`,
  whose `conftest.py` replaces them with fakes for every test, as rms-host-galileo's
  does. It also copies the oops Path and Frame registries, caches, and per-class
  waypoint and wayframe pools for each test (`_WAYPOINTS`, `_WAYFRAMES`, `_FOR_NAME`,
  `_FRAME_LOOKUP`), because oops gives a path or frame the ID of an earlier one defined
  in exactly the same way. Keep any new oops global state in that fixture until oops
  offers one helper for all of it (the fixture's comment cites the oops issue).

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
  whether `ckgp` at mid-exposure as in the rms-oops host or a `SpiceType1Frame`, returns
  the record nearest that time within its tolerance: often a neighboring image's, at
  times none, and the image's own only when no other record is nearer.
  `docs/user_guide/user_guide_appendix_pointing.rst` gives
  the evidence: fits of real images show that the record at the image's own count holds
  its pointing, and that `STOP_TIME`, not the count, is the shutter time.
- So the time is `STOP_TIME`, else `IMAGE_TIME`. Only when no source read gives either
  does `_tstop()` take it from the clock count through the clock kernel (`sct2e`), and
  the `time_from_sclk` subfield is then True (operator, 2026-11-19). The count's time
  is a median of 0.9 s after `STOP_TIME` (79% within 3 s) on the 76,635 raw images
  that give both, whole FDS minutes later for most of the rest, and more than a second
  earlier for 12 (up to 7,625 s, C4201035), so such a time can be off by minutes. The
  default frame is an unregistered `Cmatrix` relative to J2000, from the record found by
  the clock count (`_camera_frame()`, tolerance 20 ticks). Only the supplement gives the
  clock partition, which labels and VICAR headers lack;
  `_Voyager.sclk_ticks()` picks the partition nearest `STOP_TIME`, or, without a time,
  nearest `EARTH_RECEIVED_TIME` (label, index, or the header's `LAB04` ERT), or, without
  that either, the one partition that puts the count in one of
  `_Voyager.IMAGING_PERIODS` (the archive's STOP_TIME span per spacecraft and encounter,
  widened by 30 days; unique for all 76,635 timed raw images). The count recurs about
  every six years, so the partition always needs a hint. A VICAR header gives the count
  as its FDS image number in `LAB02`, which `_header_items()` spells as a label does
  (`9096:16:001` for FDS `09096.16`); an index row without the supplement gives only
  `IMAGE_NUMBER`, from which `_clock_count()` derives it, and failing both the count is
  read from the image file's name (`C3450201` is FDS 34502.01), the only source for the
  11 headers with FDS 00000.00. A frame `timeshift` ("F") has no effect on the fixed
  matrix.
- Missing SPICE data for the pointing raises `oops.OopsSpiceError` (rms-oops 0.5.0),
  built with `OopsSpiceError.from_spice_error()`: "no Voyager 1 ISS NAC pointing at
  <time> for <image file>: SPICE(CKINSUFFDATA)", and the SPICE exception is its cause.
  The host has one rule for cspyce failures, oops's: around `sclk_ticks()`, `ckgp` and
  `pxform` (`_camera_frame()`) and around the clock conversion (`_tstop()`), it catches
  `oops.OopsSpiceError.CSPYCE_ERRORS` and treats an exception as missing SPICE data
  only if `oops.OopsSpiceError.is_spice_error()` finds a SPICE code in its text, or if it
  is the `OopsSpiceError` that `sclk_ticks()` raises for a count it cannot place in one
  partition; anything else propagates. `_camera_frame()` then returns an
  `oops.frame.SpiceErrorFrame` (rms-oops's shared form, used by all four hosts), which
  raises that error whenever it is evaluated. `from_file()` evaluates the observation's
  frame after `oops.Host._apply_overrides()`, relative to J2000 at the cadence's
  midtime, so it raises at load time; `from_index()` does not, so it keeps the row.
  Neither checks anything when a `frame` override is given. The time in the message is
  where that evaluation reaches the `SpiceErrorFrame`: the midtime after any cadence
  override, moved by any `timeshift`, once for "T" and once more for "F" (the
  `SpiceErrorFrame` is relative to J2000, so the `FrameShift` around it moves its
  evaluation). A `tracker` cannot be built on that frame, so oops leaves the frame
  unwrapped and the time stays the midtime. The host does not call
  `oops.Host._check_spice_frame()`, which is for a frame that reads the C kernels when
  it is evaluated: Voyager's frame reads them when it is built, at the image's own clock
  count, and given the `SpiceErrorFrame` the check would wrap that frame's
  `OopsSpiceError` in another, with a different time. An image for which no source read gives the exposure, camera,
  spacecraft, a time or a clock count, or (without a `frame` override) a clock stop
  count raises `oops.host.HostFormatError`, naming the items and the file, from both
  methods; a missing `MISSION_PHASE_NAME` is computed from the time. Across the
  VGISS labels 2,137 images lack a record, 1,090 of them in VGISS_8210. The index and
  the label of C3940530 (VGISS_6201) lack an exposure (only its VICAR header gives one,
  5 ms), so iterating `from_index()` over that volume raises at that row and ends there
  (a `filter` can leave the row out). Both questions, that row and whether an iteration
  should go on past a row error, are open; the comment in `from_index()` cites the
  issues.
- `from_index()` accepts only a `VGISS_xxxx_raw_image_index.lbl` of the Ring-Moon
  Systems Node metadata, one row per RAW image, never the full `VGISS_xxxx_index.lbl`
  (one row per label: RAW, CLEANED, CALIB, GEOMED and the reseau and tiepoint tables),
  because OPUS archives only the raw images. The `VGISS_xxxx_supplemental_index.lbl`
  matches the raw image index row for row. The default `oops.Host.SUPPLEMENT` search
  would look for `..._raw_image_supplemental_index`, so the host overrides
  `_supplement_path()` to replace `_raw_image_index` with `_supplemental_index`, and to
  raise `HostIndexError` for an index whose name lacks it; `from_index()` calls it with
  `row_dicts` too, so with the default supplement the full index is refused when the call
  is made, with or without `row_dicts`. Given a supplement path, the name is not checked, and the full
  index raises `HostIndexError` at row 0, whose file differs from the supplement's. The
  supplement is required: `supplement=None` or `''` (which oops would take to mean "read
  the index alone") raises `HostIndexError` unless `row_dicts` is given. `select` other
  than None raises `OopsValueError`, through `oops.Host._check_select()`; an unknown
  keyword raises Python's own `TypeError`, since neither method takes `**kwargs`.
  Every index label gives
  `EXPOSURE_DURATION` `BYTES = 7` where the field and its `FORMAT` "F9.4" are 9 wide, so
  `_index_label()` widens a column to its FORMAT width where the label gives fewer bytes
  (1,597 of 76,652 rows lose digits otherwise, and the null -99.9999 reads -99.99). It is
  decided from each label, so a corrected label is read as it is. One issue of
  SETI/rms-data-projects lists every index problem the host works around until the
  index files are corrected, and each workaround's `#` comment cites its item. The
  widened label stays a dictionary passed to `_index_rows()` as `label`. The host keeps
  oops's default `_correct_label_text()`, since every VGISS label parses as written.
  Parsing is not the same as reading every value right: the default fast parser reads
  the quoted `EDIT_MODE_ID = "1:32"` (472 raw images in VGISS_8xxx, four labels each)
  as the time 01:32, and text repair cannot prevent it. `_plain_label_dict()` rebuilds
  the text "1:32" from that time, so `label_dict` and `dict` agree with the label and
  the index; its `#` comment cites the parser issue. A fast-versus-strict comparison
  of every label in four whole volumes found no other difference on an image label.
- `oops.Host.from_index()` identifies the host from the index label's `DATA_SET_ID`
  (`_detect_in_index()`; every VGISS index label, metadata or volume, gives one such as
  "VG1/VG2-S-ISS-2/3/4/6-PROCESSED-V1.0") and passes the call, every argument included,
  to `VoyagerISS.from_index()`. That reads nothing itself: `oops.Host._index_rows()`
  reads the labels at call time (and the first row too, to check the host, if the label
  lacks `DATA_SET_ID`, also on a direct call) and streams the rows of both tables with
  `PdsTable(defer_load=True).iter_dicts`, 100 at a time, pairing them by position because
  `MERGE_SUPPLEMENT_BY_FILE` is False. A survey of all 91 raw image indexes in
  the metadata tree found every supplement equal in length and
  in agreement on `VOLUME_NAME`, `FILE_SPECIFICATION_NAME` and `PRODUCT_ID` row for row.
  A row whose files differ raises `HostIndexError` at that row. The default validation
  compares `PRODUCT_ID` and `VOLUME_NAME`, the columns the tables share beyond the file
  key. Given `row_dicts` are used as they are and filtered lazily; nothing reads a
  whole table.
  The table reader's `_mask` entries are dropped; the only declared null constants
  (exposure -99.9999, supplement `UNK` times), the `UNKNOWN` that the indexes give for
  `EARTH_RECEIVED_TIME` (1,524 rows) and `IMAGE_ID` (250), the empty `NOTE` (36,095),
  and the placeholder `IMAGE_ID` `0000Z1+000`/`0000Z2+000` (34 rows) never stand for
  real values. The supplement adds `START_TIME` and `STOP_TIME` with fractional seconds;
  the index's `IMAGE_TIME` has whole seconds. Each Snapshot's `filepath`, `basename` and
  `image_url` name the raw image file (`PRODUCT_ID`), never the label in
  `FILE_SPECIFICATION_NAME`, as a full path or URL: `_index_image_path()` takes the
  label's path from `oops.Host._index_file_path(..., volume_column='VOLUME_NAME')` and
  gives it the name `PRODUCT_ID`. The volume is in `oops.Host._volumes_directory()` of
  the index, which maps
  `<holdings>/metadata/VGISS_6xxx/VGISS_6109/` to `<holdings>/volumes/VGISS_6xxx/` and
  any other index to its own directory. `oops.Host._apply_overrides()` names the file
  and never creates a directory; an index row never has `abspath`, since its image file
  is not retrieved. Each row's camera frame is freed with its Snapshot only with
  SETI/rms-oops#252; without it, memory grows by a few KB per row.
- `from_index()` returns an iterator. It checks its arguments and gets the row iterator
  from `_index_rows()`, then returns a generator expression that builds each row's
  Snapshot when the iteration reaches it. It must not be a generator function, which
  would defer every call-time error to the first `next()`.
- VGISS indexes name the volume in `VOLUME_NAME`, not `VOLUME_ID`, and the camera in
  `INSTRUMENT_NAME` ("NARROW ANGLE CAMERA" or "WIDE ANGLE CAMERA"). The index label
  names the host and instrument only in `DATA_SET_ID`, which `_detect_in_index()`
  reads; without one it returns None, and `_detect_in_row()` decides.
- `test_files/` holds cut-down copies of real VGISS_6109 products from
  <https://pds-rings.seti.org/holdings/>: the first 30 rows of the raw image and
  supplemental indexes, which include C3450201 and both cameras; the C3450201 RAW and
  GEOMED labels; and the RAW and GEOMED images truncated after their VICAR headers. Their
  `_edited` names carry through the labels' pointers.
- `from_file()` follows the operator's rules of 2026-11-18, shared by the four host
  packages: an image input reads only the image file (no `data` with `astrometry=True`);
  a label input reads the label and, unless `astrometry=True`, the image file its
  `^IMAGE` names, found case-tolerantly. With `astrometry=True` a label input reads the
  label alone, and the image file need not exist. The metadata come from whatever was
  read. `_header_items()` converts the VICAR header's `LAB02`, `LAB03` and `LAB05`
  fields, the ERT of `LAB04` (which, holding more, also stays in `dict` under its own
  name) and its tasks to the label's names and forms, leaving out any field filled with
  asterisks or garbled (an hour of 24 or more, a year outside 77-99, PICNO bytes, FDS
  00000.00), and respells the target fields that no label uses through
  `_HEADER_TARGET_SPELLINGS` (`ENCELADU`, `1989N1`, ...).
  `_merge_items()` takes an item from whichever source gives it and, where both do,
  the source that `_RESOLUTION` names for that item, "label" or "header", the same for
  every image; never a per-image test. Every item is "label" now. The comment above
  `_RESOLUTION` gives the evidence for each choice and cites the issue that lists the
  images on which the winner is wrong; keep the two in step. A null (what
  `oops.host.is_pds3_null()` accepts: `UNK`, `UNKNOWN`, `N/A`, `NULL`,
  `NOT APPLICABLE`, `---` and empty text in any case, None, and a list of these; plus
  -99.9999 in the index and the `IMAGE_ID` placeholder `0000Zn+000`) counts as missing,
  also inside a label OBJECT (`dict['IMAGE']['REFLECTANCE_SCALING_FACTOR']` 'UNK' on
  some products); `label_dict` and `header_dict` keep them. No VGISS index table, in
  the metadata tree or in a volume's `INDEX`, gives `NOT APPLICABLE`. A subfield is
  absent when no source gives it, never None (`if_scale` included). `calibrations` is
  `[FlatCalib('I/F', if_scale)]` when `if_scale` is present, else empty (it matches the
  label's REFLECTANCE_SCALING_FACTOR, which the rms-oops host read, except for the 3.345
  factor on Voyager 1 Saturn).
  `dict` is the merged metadata plus every other key of each source (`LABEL3`, ...), but
  never `LAB02`, `LAB03` or `LAB05`, whose fields it holds under PDS3 names.
  `label_dict` is the label's own keywords (`from_file()` always parses with
  `sources=False`, and `_plain_label_dict()` drops every lower-case parser entry, such as
  `_fmt`, `_unit`, `^IMAGE_offset`, `objects` and the `_source` keys of a label that
  `oops.Host.from_file()` parsed; times as the parser's ISO strings, sets as lists, END
  left out) and `header_dict` the header (`as_dict(unique=True)`, a repeated parameter
  keyed `TASK[0]`, `TASK[1]`, ...), each only when read; all three are JSON. A
  `VicarImage` rewrites `HOST` (and `BHOST`) with the reading machine when it reads the
  array, so the header of any `VicarImage` with a file path, read by the host or given
  by the caller, in a full or an astrometry read, comes from the file's `VicarLabel`
  (`_header_as_written()`), and `header_dict` is the same from every input that reads
  the header. `from_file()` raises `TypeError` for an unknown keyword and
  `OopsValueError` for a `select` other than None, as `from_index()` does;
  `oops.Host.from_file()` passes the path and the caller's arguments unchanged, so both
  entry points give the same Snapshot. `spacecraft`, the int 1 or 2, is how a consumer
  tells Voyager 1 from Voyager 2. Consumers read the documented subfields, including
  `spacecraft_clock_start_count` and `spacecraft_clock_stop_count`, rather than parsing
  `dict`.
- `filepath`, `basename`, `abspath` and `image_url` name the image file on every input.
  They are absent for parsed content without a file path (a `Pds3Label` made from text, a
  `VicarImage` made from an array). A full read of a `Pds3Label` or `VicarLabel` without
  a path raises `HostFormatError` ("no file path is known"), since it cannot find the
  image file; a `VicarImage` holds its array, which a full read takes from
  `image.data_2d`. `abspath` is given only by a read that
  retrieved the image file in this call to read its data (operator, 2026-11-20: a file
  that exists remotely need not exist locally until it is retrieved): the path that
  `FCPath.retrieve()` returns, passed to `oops.Host._apply_overrides(abspath=...)`.
  `_read_vicar_image()` retrieves before it reads; an image file given as a path, which
  oops has read through the cache, is retrieved again, a no-op. An astrometry read, a
  `VicarImage` the caller gives, and `from_index()` never give it. Tests of cache state
  use the `file_cache` fixture, a private `FileCache`, never the shared one.
- The data are read where the VICAR header places them, not where the label does: the
  label of C2673426_GEOMED in VGISS_7205 points a record early, into its two-record
  header, and that of C3489611_GEOMED in VGISS_6112 a record late. The header supplies
  `if_scale`, the I/F factor of a CALIB or GEOMED product ("FOR (I/F)*10000., MULTIPLY DN
  VALUE BY x", which FICOR77 writes as `LABEL3`, divided by 10000), which only the
  header gives: the label's `REFLECTANCE_SCALING_FACTOR` is 3.345 times larger for
  Voyager 1 images of Saturn.
- A LAB03 line pads the filter name and writes `CH4/JS` and `CH4/U`; `_filter_name()`
  respells it as the label does (`CLEAR`, `CH4_JS`) on every path.
- pdsparser's default "fast" method rejects some valid VGISS labels, such as
  `C2673426_GEOMED.LBL` of VGISS_7205, whose quoted `NOTE` value starts on the line after
  the keyword. `from_file()` reads labels through `_read_fileinfo(...,
  pds3_fallback=True)`, so oops parses the label again with
  "strict", then "loose", also after a `ValueError` (an hour of 24), and raises
  `PdsHostError` naming the label and each method's error if all fail. The host wraps
  only the VICAR errors, so that their messages name the file. `oops.Host.from_file()`
  identifies the host with the same fallback and then passes the path itself, so it
  reads those labels too.
- The labels of the RESLOC and GEOMA tables have no `^IMAGE` and raise `PdsHostError`;
  their VICAR files (`TYPE='TABULAR'`) raise `VicarHostError`, as does an image file cut
  short. Parsed content of another host raises `WrongHostError`.
- Labels name the spacecraft in `INSTRUMENT_HOST_NAME` ("VOYAGER 1") and the camera in
  `INSTRUMENT_ID` (`ISSN`/`ISSW`). `IMAGE_TIME` and `STOP_TIME` are the shutter close, and
  `EXPOSURE_DURATION` is in seconds in a label but milliseconds in the VICAR header. The
  index of VGISS_6201 gives the null exposure -99.9999 for one dark frame, C3940530, whose
  label gives "UNK" for its exposure and time; 17 raw images (9 in VGISS_6101, 8 in
  VGISS_6201) have no time in either file and take it from the clock.
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

- Commit subjects are a plain, capitalized, imperative sentence, without a type prefix
  or a trailing period. PRs are squash-merged onto `main`.
- Branch names: `<initials>_<YYMMDD>_<topic>`.
- Versions come from `setuptools_scm`; never hand-write a version.
