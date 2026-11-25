##########################################################################################
# host_voyager/__init__.pyi
##########################################################################################
"""Type stub for :mod:`host_voyager`.

The source types its methods in their docstrings rather than in their signatures, so the
type information for public symbols is published here instead. Only package stubs exist,
so a name is annotated when it is imported from the package that exports it and not when
it is imported from the module that defines it. The stub describes the shape of the API
exactly: every public name, its parameters, which of them are keyword-only, and which have
defaults. Types are given where they are unambiguous and are `Any` elsewhere.

The detectors and support methods that a Host subclass overrides or calls, such as
`_register` and `_detect_in_pds3`, are private and are not published here.
"""

import pathlib
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, overload

from filecache import FCPath
from pdsparser import Pds3Label
from vicar import VicarImage, VicarLabel
from oops import Body
from oops.cadence import Cadence
from oops.calibration import Calibration
from oops.fov import FOV
from oops.frame import Frame
from oops.host import Host
from oops.lightsource import LightSource
from oops.observation import Observation, Snapshot
from oops.path import Path

__all__ = ['VoyagerISS']

_Select = int | slice | str | tuple[int | slice | str, ...]

class VoyagerISS(Host):
    # Every VGISS supplement lists the images of its raw image index in the same order,
    # so the rows of the two tables are paired by position, by the VOLUME_NAME and
    # FILE_SPECIFICATION_NAME columns of both tables.
    MERGE_SUPPLEMENT_BY_FILE: bool
    INDEX_FILE_KEY: tuple[str, ...]
    SUPPLEMENT_FILE_KEY: tuple[str, ...]
    # The runtime names the first parameter `fileinfo`, where Host names it `filepath`,
    # and takes no `**kwargs`, so that Python refuses a keyword not listed with TypeError.
    # Without `select`, the result is the image's one Snapshot.
    @overload  # type: ignore[override]
    @staticmethod
    def from_file(
        fileinfo: str | pathlib.Path | FCPath | VicarLabel | VicarImage, *,
        return_all_planets: bool = False, pds3_options: dict[str, Any] | None = None,
        astrometry: bool = False, select: None = None,
        target: str | Body | None = None, lightsource: LightSource | None = None,
        path: str | Path | None = None, frame: str | Frame | None = None,
        cadence: Cadence | None = None, fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None) -> Snapshot: ...
    @overload
    @staticmethod
    def from_file(
        fileinfo: str | pathlib.Path | FCPath | VicarLabel | VicarImage, *,
        return_all_planets: bool = False, pds3_options: dict[str, Any] | None = None,
        astrometry: bool = False, select: _Select,
        target: str | Body | None = None, lightsource: LightSource | None = None,
        path: str | Path | None = None, frame: str | Frame | None = None,
        cadence: Cadence | None = None, fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None) -> Snapshot | tuple[Snapshot, ...]: ...
    # Every row is one Snapshot. The runtime takes no `**kwargs`, so that Python refuses a
    # keyword not listed with TypeError.
    @overload  # type: ignore[override]
    @staticmethod
    def from_index(
        filepath: str | pathlib.Path | FCPath, *,
        supplement: str | pathlib.Path | FCPath | object | None = ...,
        label: Pds3Label | None = None,
        row_dicts: Iterable[dict[str, Any]] | None = None,
        pds3_options: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None, return_all_planets: bool = False,
        select: None = None,
        target: str | Body | None = None, lightsource: LightSource | None = None,
        path: str | Path | None = None, frame: str | Frame | None = None,
        cadence: Cadence | None = None, fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None) -> Iterator[Snapshot]: ...
    @overload
    @staticmethod
    def from_index(
        filepath: str | pathlib.Path | FCPath, *,
        supplement: str | pathlib.Path | FCPath | object | None = ...,
        label: Pds3Label | None = None,
        row_dicts: Iterable[dict[str, Any]] | None = None,
        pds3_options: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None, return_all_planets: bool = False,
        select: _Select,
        target: str | Body | None = None, lightsource: LightSource | None = None,
        path: str | Path | None = None, frame: str | Frame | None = None,
        cadence: Cadence | None = None, fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None
    ) -> Iterator[Snapshot | tuple[Snapshot, ...]]: ...
    @staticmethod
    def initialize(*, planets: int | Sequence[int] | None = None,
        asof: str | None = None, irregulars: bool = True) -> None: ...

##########################################################################################
