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
observation geometry. It reads images from the Imaging Science Subsystem (ISS) cameras of
Voyager 1 and Voyager 2, as the Planetary Data System (PDS) archives them, and turns each
one into an OOPS `Snapshot`: the image array, together with the time, field of view,
pointing, target, and light source that OOPS needs to compute the image's geometry. It
is a product of the [PDS Ring-Moon Systems Node](https://pds-rings.seti.org).

# Features

- **Either file of a product**: an image can be opened from its VICAR image file or from
  its PDS3 label, and the pixels are identical either way.
- **Metadata from both labels**: when a PDS3 label is read together with its image
  file, the values in the label and in the VICAR header are merged under the PDS3 names,
  with one fixed choice for each value on which they disagree.
- **Whole volumes at once**: a raw image index and its supplemental index give one
  `Snapshot` per image, without opening the images.
- **Pointing from SPICE**: the pointing comes from the image's own record in the scan
  platform C kernels, or from a frame that the caller supplies.
- **Calibration to I/F**: a calibrated or geometrically corrected image carries the
  factor that converts its data to I/F.
- **Time from the spacecraft clock**: an image whose labels give no time takes it from
  its spacecraft clock count, through the SPICE clock kernel.

# Installation

`rms-host-voyager` needs Python 3.11 or later. Install it from PyPI with:

```sh
pip install rms-host-voyager
```

The host also needs the Voyager SPICE kernels. The
[setup chapter](https://rms-host-voyager.readthedocs.io/en/latest/user_guide/user_guide_setup.html)
of the user guide describes the kernels and the environment variables that locate them.

# Quick Start

Importing `host_voyager.oops` registers the host with `oops.Host`, which can then open
any Voyager ISS image:

```python
import oops
import host_voyager.oops

obs = oops.Host.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.IMG')
```

The host class can also be used directly, for one image or for every image in a volume:

```python
from host_voyager import VoyagerISS

snapshot = VoyagerISS.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.LBL')
snapshots = list(VoyagerISS.from_index('VGISS_6109_raw_image_index.lbl'))
```

Replace each path with the location of the file in your copy of the archive, or with
its URL. `from_index` also reads the supplemental index beside the index, which must
exist. It returns an iterator, which builds each image's Snapshot when the loop reaches
it; `list()` builds them all at once.

# Documentation

The [documentation](https://rms-host-voyager.readthedocs.io) holds the user guide and
the API reference. To build it locally, run `make html` in the `docs` directory after
installing the package with its `docs` extra.

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
