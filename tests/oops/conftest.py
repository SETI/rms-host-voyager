##########################################################################################
# tests/oops/conftest.py
##########################################################################################
"""Shared fixtures that let the Voyager host run without SPICE kernels or spicedb.

Every spicedb function the package calls is replaced by :class:`FakeSpiceDB`, and the
oops and cspyce calls that would read SPICE kernels are replaced by kernel-free
equivalents. Everything else, including oops itself, runs for real.
"""

import pathlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import cspyce
import julian
import numpy as np
import oops
import pytest
import spicedb

from host_voyager.oops.iss import VoyagerISS

TEST_FILES = pathlib.Path(__file__).parents[2] / 'test_files'
RAW_LABEL = TEST_FILES / 'C3450201_RAW_edited.LBL'
GEOMED_LABEL = TEST_FILES / 'C3450201_GEOMED_edited.LBL'
GEOMED_VICAR = TEST_FILES / 'C3450201_GEOMED_edited.IMG'
INDEX = TEST_FILES / 'VGISS_6109_raw_image_index_edited.lbl'
SUPPLEMENT = TEST_FILES / 'VGISS_6109_supplemental_index_edited.lbl'
INDEX_ROWS = 30

# C3450201: its STOP_TIME and its spacecraft clock stop count
STOP_TIME = '1980-10-29T05:57:22.00'
CLOCK_COUNT = '34502:01:001'

# The fake FDS clock. Partition 1 holds no count; partition 2 is placed so that
# CLOCK_COUNT falls exactly at STOP_TIME; partition 3 starts one clock cycle later.
FDS_HOUR = 2880.        # seconds
FDS_MINUTE = 48.
FDS_TICK = 0.06
PARTITION_TICKS = 10_000_000_000
CYCLE_SECONDS = 65536 * FDS_HOUR
PARTITION_EPOCHS = {
    2: julian.tdb_from_tai(julian.tai_from_iso(STOP_TIME)) - 34502 * FDS_HOUR - FDS_MINUTE,
}
PARTITION_EPOCHS[3] = PARTITION_EPOCHS[2] + CYCLE_SECONDS
N_PARTITIONS = 3

# The fixed rotation from each scan platform to each camera
PLATFORM_TO_CAMERA_ANGLE = 0.1


def fake_scencd(naif_id: int, count: str) -> float:
    """Encode "p/hhhhh:mm:ddd" as fake ticks; partition 1 and others are invalid."""

    assert naif_id in (-31, -32), f'unexpected spacecraft ID {naif_id}'
    (partition, fields) = count.split('/')
    if int(partition) not in PARTITION_EPOCHS:
        raise ValueError(f'SPICE(NOTINPART) -- {count}')
    (hours, minutes, ticks) = (int(field) for field in fields.split(':'))
    return float(int(partition) * PARTITION_TICKS + (hours * 60 + minutes) * 800 + ticks - 1)


def fake_sct2e(naif_id: int, ticks: float) -> float:
    """Decode fake ticks to seconds TDB."""

    (partition, within) = divmod(ticks, PARTITION_TICKS)
    return float(PARTITION_EPOCHS[int(partition)] + within * FDS_TICK)


def rotation(axis: int, angle: float) -> np.ndarray:
    """The 3x3 matrix rotating coordinates by `angle` about the given axis."""

    (i, j) = [k for k in range(3) if k != axis]
    matrix = np.eye(3)
    matrix[i, i] = matrix[j, j] = np.cos(angle)
    matrix[i, j] = np.sin(angle)
    matrix[j, i] = -np.sin(angle)
    return matrix


def record_matrix(ticks: float) -> np.ndarray:
    """The fake C-kernel record at `ticks`, a rotation unique to each count."""

    return rotation(2, (ticks % 1_000_000) * 1.e-6)


def camera_matrix(count: str, partition: int = 2) -> np.ndarray:
    """The expected J2000-to-camera matrix of the image with the given clock count."""

    ticks = fake_scencd(-31, f'{partition}/{count}')
    matrix: np.ndarray = rotation(0, PLATFORM_TO_CAMERA_ANGLE) @ record_matrix(ticks)
    return matrix


@dataclass
class FakeSpiceDB:
    """Records every spicedb call and returns configurable results.

    Attributes:
        calls (list[tuple[str, tuple, dict]]): Each call as (name, args, kwargs).
        used (list[str]): Returned by `used_basenames`.
    """

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    used: list[str] = field(default_factory=lambda: ['vg1_issna_v02.ti', 'vg100019.tsc'])

    def _record(self, func: str, /, *args: Any, **kwargs: Any) -> None:
        self.calls.append((func, args, kwargs))

    def named(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        """The (args, kwargs) of every recorded call to the named function."""

        return [(args, kwargs) for (n, args, kwargs) in self.calls if n == name]

    def open_db(self, *args: Any, **kwargs: Any) -> None:
        self._record('open_db', *args, **kwargs)

    def close_db(self, *args: Any, **kwargs: Any) -> None:
        self._record('close_db', *args, **kwargs)

    def furnish_inst(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_inst', *args, **kwargs)
        return []

    def furnish_ck(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_ck', *args, **kwargs)
        return []

    def furnish_spk(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_spk', *args, **kwargs)
        return []

    def used_basenames(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('used_basenames', *args, **kwargs)
        return self.used


_SPICEDB_FUNCTIONS = ('open_db', 'close_db', 'furnish_inst', 'furnish_ck', 'furnish_spk',
                      'used_basenames')


@dataclass
class FakeSpice:
    """Records the oops and cspyce calls that would otherwise read SPICE kernels.

    Attributes:
        leap_seconds (int): The number of calls to `oops.spice.load_leap_seconds`.
        solar_system (list[tuple[tuple, dict]]): The (args, kwargs) of each call to
            `Body.define_solar_system`.
        spice_paths (list[tuple[str, str]]): The (SPICE name, path ID) of each SpicePath.
        ckgp (list[tuple[int, float, float, str]]): The arguments of each `cspyce.ckgp`.
        missing (set[float]): Ticks at which `cspyce.ckgp` finds no record.
        pxform (list[tuple[str, str]]): The (from, to) frames of each `cspyce.pxform`.
    """

    leap_seconds: int = 0
    solar_system: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list)
    spice_paths: list[tuple[str, str]] = field(default_factory=list)
    ckgp: list[tuple[int, float, float, str]] = field(default_factory=list)
    missing: set[float] = field(default_factory=set)
    pxform: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def fake_spicedb(monkeypatch: pytest.MonkeyPatch) -> FakeSpiceDB:
    """Replace every spicedb function the package calls with a recording fake."""

    fake = FakeSpiceDB()
    for name in _SPICEDB_FUNCTIONS:
        monkeypatch.setattr(spicedb, name, getattr(fake, name))
    return fake


@pytest.fixture(autouse=True)
def fake_spice(monkeypatch: pytest.MonkeyPatch) -> FakeSpice:
    """Keep SPICE kernels out of every test and isolate the oops registries.

    Loading the leap seconds and defining the Solar System only record the call, and a
    SpicePath becomes a FixedPath near the Solar System barycenter. The spacecraft clock
    follows the fake FDS clock above, every count has a C-kernel record given by
    :func:`record_matrix` unless its ticks are in `missing`, and each camera is rotated
    by PLATFORM_TO_CAMERA_ANGLE about the x-axis from its scan platform. The Path and
    Frame registries are copied so that anything registered by one test never reaches
    another.
    """

    fake = FakeSpice()

    def load_leap_seconds() -> None:
        fake.leap_seconds += 1

    def define_solar_system(*args: Any, **kwargs: Any) -> None:
        fake.solar_system.append((args, kwargs))

    def spice_path(spice_name: str, *, path_id: str) -> oops.Path:
        fake.spice_paths.append((spice_name, path_id))
        # Distinct positions, so that oops never treats the two paths as one
        return oops.path.FixedPath((float(len(fake.spice_paths)), 0., 0.), oops.Path.SSB,
                                   oops.Frame.J2000, path_id=path_id)

    def scpart(naif_id: int) -> tuple[np.ndarray, np.ndarray]:
        return (np.zeros(N_PARTITIONS), np.zeros(N_PARTITIONS))

    def ckgp(inst: int, ticks: float, tol: float, ref: str) -> tuple[np.ndarray, float]:
        fake.ckgp.append((inst, ticks, tol, ref))
        if ticks in fake.missing:
            raise OSError('SPICE(CKINSUFFDATA) -- ckgp -- insufficient C kernel data')
        return (record_matrix(ticks), ticks)

    def pxform(source: str, target: str, et: float) -> np.ndarray:
        fake.pxform.append((source, target))
        return rotation(0, PLATFORM_TO_CAMERA_ANGLE)

    monkeypatch.setattr(oops.spice, 'load_leap_seconds', load_leap_seconds)
    monkeypatch.setattr(oops.Body, 'define_solar_system', staticmethod(define_solar_system))
    monkeypatch.setattr(oops.path, 'SpicePath', spice_path)
    monkeypatch.setattr(cspyce, 'scencd', fake_scencd)
    monkeypatch.setattr(cspyce, 'sct2e', fake_sct2e)
    monkeypatch.setattr(cspyce, 'scpart', scpart)
    monkeypatch.setattr(cspyce, 'ckgp', ckgp)
    monkeypatch.setattr(cspyce, 'pxform', pxform)

    monkeypatch.setattr(oops.Path, '_PATH_REGISTRY', dict(oops.Path._PATH_REGISTRY))
    monkeypatch.setattr(oops.Path, '_PATH_CACHE', dict(oops.Path._PATH_CACHE))
    monkeypatch.setattr(oops.Frame, '_FRAME_REGISTRY', dict(oops.Frame._FRAME_REGISTRY))
    monkeypatch.setattr(oops.Frame, '_FRAME_CACHE', dict(oops.Frame._FRAME_CACHE))
    return fake


@pytest.fixture(autouse=True)
def reset_host() -> Iterator[None]:
    """Start and end every test with the Voyager and ISS class states reset."""

    VoyagerISS._reset()
    yield
    VoyagerISS._reset()


@pytest.fixture
def body_path(monkeypatch: pytest.MonkeyPatch) -> oops.Path:
    """A fixed path registered as TITAN, with a Body of that name that a TrackerFrame can
    track; the Body registry is restored afterward."""

    monkeypatch.setattr(oops.Body, 'BODY_REGISTRY', dict(oops.Body.BODY_REGISTRY))
    path = oops.path.FixedPath((1.e9, 0., 0.), oops.Path.SSB, oops.Frame.J2000,
                               path_id='TITAN')
    oops.Body('TITAN', path, oops.Frame.J2000)
    return path

##########################################################################################
