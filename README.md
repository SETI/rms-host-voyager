<!-- pyml disable MD025 -->
# rms-host-voyager

[![GitHub release; latest by date](https://img.shields.io/github/v/release/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/releases)
[![GitHub Release Date](https://img.shields.io/github/release-date/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/releases)
[![Test Status](https://img.shields.io/github/actions/workflow/status/SETI/rms-host-voyager/run-tests.yml?branch=main)](https://github.com/SETI/rms-host-voyager/actions)
[![Documentation Status](https://readthedocs.org/projects/rms-host-voyager/badge/?version=latest)](https://rms-host-voyager.readthedocs.io/en/latest/?badge=latest)
[![Code coverage](https://img.shields.io/codecov/c/github/SETI/rms-host-voyager/main?logo=codecov)](https://codecov.io/gh/SETI/rms-host-voyager)
<br />
[![PyPI - Version](https://img.shields.io/pypi/v/rms-host-voyager)](https://pypi.org/project/rms-host-voyager)
[![PyPI - Format](https://img.shields.io/pypi/format/rms-host-voyager)](https://pypi.org/project/rms-host-voyager)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/rms-host-voyager)](https://pypi.org/project/rms-host-voyager)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/rms-host-voyager)](https://pypi.org/project/rms-host-voyager)
<br />
[![GitHub commits since latest release](https://img.shields.io/github/commits-since/SETI/rms-host-voyager/latest)](https://github.com/SETI/rms-host-voyager/commits/main/)
[![GitHub commit activity](https://img.shields.io/github/commit-activity/m/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/commits/main/)
[![GitHub last commit](https://img.shields.io/github/last-commit/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/commits/main/)
<br />
[![Number of GitHub open issues](https://img.shields.io/github/issues-raw/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/issues)
[![Number of GitHub closed issues](https://img.shields.io/github/issues-closed-raw/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/issues)
[![Number of GitHub open pull requests](https://img.shields.io/github/issues-pr-raw/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/pulls)
[![Number of GitHub closed pull requests](https://img.shields.io/github/issues-pr-closed-raw/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/pulls)
<br />
![GitHub License](https://img.shields.io/github/license/SETI/rms-host-voyager)
[![Number of GitHub stars](https://img.shields.io/github/stars/SETI/rms-host-voyager)](https://github.com/SETI/rms-host-voyager/stargazers)
![GitHub forks](https://img.shields.io/github/forks/SETI/rms-host-voyager)
<!-- start-after-point -->

# Introduction

`rms-host-voyager` is the Voyager mission host for
[OOPS](https://github.com/SETI/rms-oops), the Object-Oriented Python SPICE toolkit for
observation geometry. It turns Voyager 1 and Voyager 2 Imaging Science Subsystem (ISS)
images into OOPS `Snapshot` objects, with the frame, field of view, timing, target, and
light source that each image requires. It is a product of the
[PDS Ring-Moon Systems Node](https://pds-rings.seti.org).

# Installation

The `host_voyager` module is available via the `rms-host-voyager` package on PyPI and can
be installed with:

```sh
pip install rms-host-voyager
```

Geometry calculations need the Voyager SPICE kernels, which are read through the kernel
database of `spicedb`, located by the `SPICE_PATH` environment variable (or
`$OOPS_RESOURCES/SPICE`) and `SPICE_SQLITE_DB_NAME`.

# Getting Started

Importing `host_voyager.oops` registers the Voyager ISS host with `oops.Host`, which can
then identify and read a VGISS image, from either its PDS3 label or its VICAR file. An
image needs a time and, unless a `frame` override is given, a record in the scan platform
C kernels; see [Features](#features).

```python
import oops
import host_voyager.oops

obs = oops.Host.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.LBL')
```

The host class can also be used directly. It reads a raw image index together with the
supplemental index beside it (here `VGISS_6109_supplemental_index.lbl`), which must
exist:

```python
from host_voyager import VoyagerISS

snapshot = VoyagerISS.from_file('VGISS_6109/DATA/C34502XX/C3450201_RAW.IMG')
snapshots = VoyagerISS.from_index('VGISS_6109_raw_image_index.lbl')
```

# Features

- `VoyagerISS.from_file()` makes a `Snapshot` from a RAW, CLEANED, CALIB or GEOMED image.
  A GEOMED image has a 1000x1000 field of view free of the camera's distortion, and every
  other product an 800x800 one. `astrometry=True` reads no image data.
- `VoyagerISS.from_index()` makes one `Snapshot` for each row of a raw image index,
  merging in the columns of the supplemental index. The volume index, which lists every
  product, is not accepted.
- The time of an image is its STOP_TIME. Its pointing comes from its own record in the
  scan platform C kernels, found by its spacecraft clock count rather than by its time,
  because the records are stamped at the count and the count can fall minutes after the
  shutter closed. The
  [timing and pointing page](https://rms-host-voyager.readthedocs.io/en/latest/pointing.html)
  gives the evidence. An image with no time, or no record, cannot be read; `from_index()`
  leaves it out.
- A target named in the label is repaired where it names no body that oops knows, such as
  the ring planes, and an image of a star uses that star as its light source.

Details are available in the
[module documentation](https://rms-host-voyager.readthedocs.io/en/latest/module.html).

# Contributing

Information on contributing to this package can be found in the
[Contributing Guide](https://github.com/SETI/rms-host-voyager/blob/main/CONTRIBUTING.md).

# Links

- [Documentation](https://rms-host-voyager.readthedocs.io)
- [Repository](https://github.com/SETI/rms-host-voyager)
- [Issue tracker](https://github.com/SETI/rms-host-voyager/issues)
- [PyPi](https://pypi.org/project/rms-host-voyager)

# Licensing

This code is licensed under the [Apache License v2.0](https://github.com/SETI/rms-host-voyager/blob/main/LICENSE).
