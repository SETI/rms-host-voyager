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
from typing import Any

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

class VoyagerISS(Host):
    # The runtime names the first parameter `fileinfo`, where Host names it `filepath`.
    # Without a `frame`, an image whose pointing the SPICE kernels cannot supply raises
    # oops.OopsSpiceError, "no Voyager 1 ISS NAC pointing at <midtime> for <image
    # file>: SPICE(CKINSUFFDATA)", whose cause is the SPICE exception. An image for which
    # no source read gives a time takes it from its clock count (time_from_sclk), and
    # raises oops.OopsSpiceError if the clock kernel cannot convert the count. An image
    # for which no source read gives the exposure, camera, spacecraft, a time or a clock
    # count, or, unless `frame` is given, a clock count, raises
    # oops.host.HostFormatError; a label
    # that no parser method reads, or that names no IMAGE file, raises
    # oops.host.PdsHostError; a VICAR file that cannot be read as an image raises
    # oops.host.VicarHostError; another mission's file or parsed content raises
    # oops.host.WrongHostError. Given the image file, only it is read; given a PDS3
    # label, the label and its image file are read and their metadata merged, except
    # that with astrometry=True the label is read alone and the image file need not
    # exist. A subfield that no source read gives, such as if_scale, is absent. The
    # `spacecraft` subfield, the int 1 or 2, tells Voyager 1 from Voyager 2 on every
    # input.
    @staticmethod
    def from_file(  # type: ignore[override]
        fileinfo: str | pathlib.Path | FCPath | Pds3Label | VicarLabel
        | VicarImage, *,
        return_all_planets: bool = False, pds3_options: dict[str, Any] | None = None,
        astrometry: bool = False, target: str | Body | None = None,
        lightsource: LightSource | None = None, path: str | Path | None = None,
        frame: str | Frame | None = None, cadence: Cadence | None = None,
        fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None, **kwargs: Any) -> Snapshot: ...
    # Every row is one Snapshot, so the list is typed more narrowly than Host's, which a
    # list's invariance does not allow. The supplement is required: None raises
    # oops.host.HostIndexError unless row_dicts is given. select must be None
    # (oops.OopsValueError), and any other keyword raises oops.OopsTypeError.
    @staticmethod
    def from_index(  # type: ignore[override]
        filepath: str | pathlib.Path | FCPath, *,
        supplement: str | pathlib.Path | FCPath | object | None = ...,
        label: Pds3Label | None = None,
        row_dicts: list[dict[str, Any]] | None = None,
        pds3_options: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None, return_all_planets: bool = False,
        select: int | slice | str | tuple[int | slice | str, ...] | None = None,
        target: str | Body | None = None, lightsource: LightSource | None = None,
        path: str | Path | None = None, frame: str | Frame | None = None,
        cadence: Cadence | None = None, fov: FOV | None = None,
        calibrations: Calibration | list[Calibration] | None = None,
        timeshift: tuple[float, str] | None = None,
        navigation: tuple[float, ...] | None = None, tracker: str | None = None,
        parallel: Observation | None = None, **kwargs: Any) -> list[Snapshot]: ...
    @staticmethod
    def initialize(*, planets: list[Any] | None = None, asof: str | None = None,
        irregulars: bool = True) -> None: ...

##########################################################################################
