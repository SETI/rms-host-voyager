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

The package does not yet contain the ISS host; it provides the packaging, tests,
documentation, and continuous integration that the host is built on.

# Installation

The `host_voyager` module is available via the `rms-host-voyager` package on PyPI and can
be installed with:

```sh
pip install rms-host-voyager
```

The host needs the `oops.Host` interface, which is not yet in a released version of
`rms-oops`. Until it is, install `rms-oops` from its development branch:

```sh
pip install "rms-oops @ git+https://github.com/SETI/rms-oops@mrs_260925_host_reorg"
```

Geometry calculations need the Voyager SPICE kernels, which are read through the kernel
database of `spicedb`, located by the `SPICE_PATH` environment variable (or
`$OOPS_RESOURCES/SPICE`) and `SPICE_SQLITE_DB_NAME`.

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
