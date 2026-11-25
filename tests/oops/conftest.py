##########################################################################################
# tests/oops/conftest.py
##########################################################################################
"""Shared fixtures that let the Voyager host run without SPICE kernels or spicedb.

Every spicedb function the package calls is replaced by :class:`FakeSpiceDB`, and the
oops and cspyce calls that would read SPICE kernels are replaced by kernel-free
equivalents. Everything else, including oops itself, runs for real. The helpers at the
end write small synthetic Voyager ISS products: a VICAR image file and its PDS3 label.
"""

import pathlib
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import cspyce
import julian
import numpy as np
import oops
import pytest
import spicedb
from vicar import VicarImage

from host_voyager.oops.iss import VoyagerISS

TEST_FILES = pathlib.Path(__file__).parents[2] / 'test_files'
RAW_LABEL = TEST_FILES / 'C3450201_RAW_edited.LBL'
GEOMED_VICAR = TEST_FILES / 'C3450201_GEOMED_edited.IMG'
INDEX = TEST_FILES / 'VGISS_6109_raw_image_index_edited.lbl'
SUPPLEMENT = TEST_FILES / 'VGISS_6109_supplemental_index_edited.lbl'
INDEX_ROWS = 30

# C3450201: its STOP_TIME and its spacecraft clock stop count
STOP_TIME = '1980-10-29T05:57:22.00'
CLOCK_COUNT = '34502:01:001'

# The fake FDS clock. Partition 1 refuses every count; partition 2 is placed so that
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

# The coverage of each spacecraft in its fake merged cruise SPK, in seconds TDB: whole
# days, from 1977-09-08 or 1977-08-23 to 2021-01-01
CRUISE_COVERAGE = {
    -31: (julian.tdb_from_tai(julian.tai_from_iso('1977-09-08')),
          julian.tdb_from_tai(julian.tai_from_iso('2021-01-01'))),
    -32: (julian.tdb_from_tai(julian.tai_from_iso('1977-08-23')),
          julian.tdb_from_tai(julian.tai_from_iso('2021-01-01'))),
}

# The fixed rotation from each scan platform to each camera
PLATFORM_TO_CAMERA_ANGLE = 0.1

# The rotation of the stand-in for a camera's SpiceFrame
SPICE_FRAME_ANGLE = 0.5


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
    """The fake C-kernel record at `ticks`, a rotation unique to each count and partition.

    One count in two partitions differs by a multiple of PARTITION_TICKS, which the
    rotation about the third axis ignores, so the partition also turns the second axis.
    """

    (partition, within) = divmod(ticks, PARTITION_TICKS)
    matrix: np.ndarray = (rotation(1, partition * 0.01)
                          @ rotation(2, (within % 1_000_000) * 1.e-6))
    return matrix


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
        furnished_spks (list[str]): The local paths of the SP kernels already furnished,
            in load order, as `FURNISHED_ABSPATHS['SPK']`.
        furnished_info (dict[str, list]): The kernel records of each furnished file,
            keyed by its basename, as `FURNISHED_INFO`.
    """

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    used: list[str] = field(default_factory=lambda: ['vg1_issna_v02.ti', 'vg100019.tsc'])
    furnished_spks: list[str] = field(default_factory=list)
    furnished_info: dict[str, list[Any]] = field(default_factory=dict)

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

    def get_spice_filecache_prefix(self) -> 'FakePrefix':
        return FakePrefix()

    def furnish_kernels(self, kernels: list[Any], **kwargs: Any) -> list[str]:
        """Record each kernel as its (spice_id, filespec, start_time, stop_time)."""

        self._record('furnish_kernels', [(k.spice_id, k.filespec, k.start_time,
                                          k.stop_time) for k in kernels], **kwargs)
        return []


class FakePrefix:
    """The SPICE file cache prefix, retrieving each file to a fake local path."""

    ROOT = pathlib.Path('/fake/SPICE')

    def retrieve(self, filespec: str) -> pathlib.Path:
        return self.ROOT / filespec


_SPICEDB_FUNCTIONS = ('open_db', 'close_db', 'furnish_inst', 'furnish_ck', 'furnish_spk',
                      'used_basenames', 'get_spice_filecache_prefix',
                      'furnish_kernels')


@dataclass
class FakeSpice:
    """Records the oops and cspyce calls that would otherwise read SPICE kernels.

    Attributes:
        leap_seconds (int): The number of calls to `oops.spice.load_leap_seconds`.
        solar_system (list[tuple[tuple, dict]]): The (args, kwargs) of each call to
            `Body.define_solar_system`.
        spice_paths (list[tuple[str, str]]): The (SPICE name, path ID) of each SpicePath.
        ckgp (list[tuple[int, float, float, str]]): The arguments of each `cspyce.ckgp`.
        missing (set[float]): Ticks at which `cspyce.ckgp` lacks a record.
        pxform (list[tuple[str, str]]): The (from, to) frames of each `cspyce.pxform`.
        spkcov (list[tuple[str, int]]): The (file, body) of each `cspyce.spkcov`.
        spice_frames (list[str]): The name given to each `SpiceFrame.get`.
    """

    leap_seconds: int = 0
    solar_system: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list)
    spice_paths: list[tuple[str, str]] = field(default_factory=list)
    ckgp: list[tuple[int, float, float, str]] = field(default_factory=list)
    missing: set[float] = field(default_factory=set)
    pxform: list[tuple[str, str]] = field(default_factory=list)
    spkcov: list[tuple[str, int]] = field(default_factory=list)
    spice_frames: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def fake_spicedb(monkeypatch: pytest.MonkeyPatch) -> FakeSpiceDB:
    """Replace every spicedb function the package calls with a recording fake."""

    fake = FakeSpiceDB()
    for name in _SPICEDB_FUNCTIONS:
        monkeypatch.setattr(spicedb, name, getattr(fake, name))
    monkeypatch.setattr(spicedb, 'FURNISHED_ABSPATHS', {'SPK': fake.furnished_spks})
    monkeypatch.setattr(spicedb, 'FURNISHED_INFO', fake.furnished_info)
    return fake


@pytest.fixture(autouse=True)
def fake_spice(monkeypatch: pytest.MonkeyPatch) -> FakeSpice:
    """Keep SPICE kernels out of every test and isolate the oops registries.

    Loading the leap seconds and defining the Solar System only record the call, and a
    SpicePath becomes a FixedPath near the Solar System barycenter. The spacecraft clock
    follows the fake FDS clock above, every count has a C-kernel record given by
    :func:`record_matrix` unless its ticks are in `missing`, and each camera is rotated
    by PLATFORM_TO_CAMERA_ANGLE about the x-axis from its scan platform. A camera's
    SpiceFrame is a Cmatrix rotated by SPICE_FRAME_ANGLE about the z-axis. The Path and
    Frame registries, caches, and waypoint and wayframe pools are copied so that paths
    and frames made by one test never reach another.
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

    def spkcov(filespec: str, naif_id: int) -> types.SimpleNamespace:
        fake.spkcov.append((filespec, naif_id))
        return types.SimpleNamespace(as_intervals=lambda: [CRUISE_COVERAGE[naif_id]])

    def pxform(source: str, target: str, et: float) -> np.ndarray:
        fake.pxform.append((source, target))
        return rotation(0, PLATFORM_TO_CAMERA_ANGLE)

    def spice_frame(name: str) -> oops.Frame:
        fake.spice_frames.append(name)
        return oops.frame.Cmatrix(rotation(2, SPICE_FRAME_ANGLE))

    monkeypatch.setattr(oops.spice, 'load_leap_seconds', load_leap_seconds)
    monkeypatch.setattr(oops.Body, 'define_solar_system', staticmethod(define_solar_system))
    monkeypatch.setattr(oops.path, 'SpicePath', spice_path)
    monkeypatch.setattr(oops.frame.SpiceFrame, 'get', staticmethod(spice_frame))
    monkeypatch.setattr(cspyce, 'scencd', fake_scencd)
    monkeypatch.setattr(cspyce, 'sct2e', fake_sct2e)
    monkeypatch.setattr(cspyce, 'scpart', scpart)
    monkeypatch.setattr(cspyce, 'ckgp', ckgp)
    monkeypatch.setattr(cspyce, 'pxform', pxform)
    monkeypatch.setattr(cspyce, 'spkcov', spkcov)

    monkeypatch.setattr(oops.Path, '_PATH_REGISTRY', dict(oops.Path._PATH_REGISTRY))
    monkeypatch.setattr(oops.Path, '_PATH_CACHE', dict(oops.Path._PATH_CACHE))
    monkeypatch.setattr(oops.Frame, '_FRAME_REGISTRY', dict(oops.Frame._FRAME_REGISTRY))
    monkeypatch.setattr(oops.Frame, '_FRAME_CACHE', dict(oops.Frame._FRAME_CACHE))

    # A path or frame defined exactly as an earlier one shares its waypoint or wayframe,
    # and so its ID, so each class's pool is copied too; otherwise a path or frame made by
    # one test could decide the ID that an identical one gets in a later test. The pools
    # are WeakValueDictionary objects, which dict() would read as keys() and then
    # pool[key], raising KeyError if the collector freed an entry in between; items()
    # reads each key with its value. SETI/rms-oops#250
    for path_class in oops.Path._PATH_SUBCLASSES:
        if '_WAYPOINTS' in vars(path_class):
            monkeypatch.setattr(path_class, '_WAYPOINTS',
                                dict(path_class._WAYPOINTS.items()))
    for frame_class in oops.Frame._FRAME_SUBCLASSES:
        for pool in ('_WAYFRAMES', '_FOR_NAME', '_FRAME_LOOKUP'):
            if pool in vars(frame_class):
                monkeypatch.setattr(frame_class, pool,
                                    dict(getattr(frame_class, pool).items()))
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
# Synthetic products
##########################################################################################

# The array of a synthetic image, LINES x LINE_SAMPLES, the raw field of view, unless a
# test gives another shape
LINES = 800
LINE_SAMPLES = 800

# The VICAR header lines of C3450201
HEADER_LINES = {
    'LAB02': 'VGR-1   FDS 34502.01   PICNO 0469S1-015   SCET 80.303 05:57:22         C',
    'LAB03': 'NA CAMERA  EXP    1920.0 MSEC FILT 1(VIOLET)  LO GAIN  SCAN RATE  5:1  C',
    'LAB04': 'ERT 80.303 07:35:06   1/ 1 FULL    RES   VIDICON TEMP  -80.00 DEG C    C',
    'LAB05': 'IN/112380/ 9 OUT/xxxxxx/xx     SATURN      DSS #63   BIT SNR    8.297  C',
}

# The I/F line of a calibrated product's header, for a factor of 1.25
IF_LINE = 'FOR (I/F)*10000., MULTIPLY DN VALUE BY               1.25000'

# The items of the label of C3450201_RAW that the host reads, as PDS3 text
LABEL_ITEMS = {
    'PRODUCT_ID'                  : '"C3450201_RAW.IMG"',
    'PRODUCT_TYPE'                : 'DECOMPRESSED_RAW_IMAGE',
    'INSTRUMENT_HOST_NAME'        : '"VOYAGER 1"',
    'INSTRUMENT_ID'               : '"ISSN"',
    'MISSION_PHASE_NAME'          : '"SATURN ENCOUNTER"',
    'TARGET_NAME'                 : '"SATURN"',
    'IMAGE_ID'                    : '"0469S1-015"',
    'IMAGE_NUMBER'                : '"34502.01"',
    'IMAGE_TIME'                  : '1980-10-29T05:57:22.00',
    'FILTER_NAME'                 : '"VIOLET"',
    'FILTER_NUMBER'               : '"1"',
    'EXPOSURE_DURATION'           : '1.920 <SECOND>',
    'START_TIME'                  : '1980-10-29T05:57:20.08',
    'STOP_TIME'                   : '1980-10-29T05:57:22.00',
    'SPACECRAFT_CLOCK_START_COUNT': '"34502:00:768"',
    'SPACECRAFT_CLOCK_STOP_COUNT' : '"34502:01:001"',
}


def image_array(shape: tuple[int, int] = (LINES, LINE_SAMPLES),
                bands: int = 1) -> np.ndarray:
    """The array of a synthetic image: unsigned bytes in each of `bands` bands of the
    given shape, counting up from 0 and wrapping at 256."""

    array: np.ndarray = (np.arange(bands * shape[0] * shape[1]) % 256).astype('uint8')
    return array.reshape(bands, *shape)


def write_image(path: pathlib.Path, *, header: dict[str, str | None] | None = None,
                tasks: tuple[str, ...] = (), if_line: str | None = None,
                bands: int = 1,
                shape: tuple[int, int] = (LINES, LINE_SAMPLES)) -> pathlib.Path:
    """Write a synthetic Voyager ISS VICAR image file.

    The header has the lines of HEADER_LINES, changed by `header`, in which None removes
    a line; then a history step for each of `tasks`, the last of which also gives the
    I/F line `if_line` as LABEL3. The array is :func:`image_array` of `shape` and
    `bands`.
    """

    image = VicarImage.from_array(image_array(shape, bands), strict=False)
    for (key, value) in (HEADER_LINES | (header or {})).items():
        if value is not None:
            image[key] = value
    for task in tasks:
        image.label.append(f"TASK='{task}' USER='TEST' DAT_TIM='Tue Nov 24 2026'")
    if if_line is not None:
        image.label.append(f"LABEL3='{if_line}'")
    image.write_file(path)
    return path


def write_label(path: pathlib.Path, image_name: str | None, *, lines: int = LINES,
                scale: str | None = '3.3786E-03', **items: str | None) -> pathlib.Path:
    """Write a synthetic PDS3 label of a Voyager ISS image.

    The items are those of LABEL_ITEMS, changed by `items`, in which None removes an
    item. `image_name` is the file of the IMAGE pointer, or None for no pointer. The
    IMAGE object gives `lines` LINES, LINE_SAMPLES samples, and the
    REFLECTANCE_SCALING_FACTOR `scale`, or none if `scale` is None.
    """

    text = ['PDS_VERSION_ID = PDS3', 'RECORD_TYPE = FIXED_LENGTH', 'RECORD_BYTES = 6']
    if image_name is not None:
        text.append(f'^IMAGE = ("{image_name}", 1)')
    text += [f'{key} = {value}' for (key, value) in (LABEL_ITEMS | items).items()
             if value is not None]
    text += ['OBJECT = IMAGE', f'  LINES = {lines}', f'  LINE_SAMPLES = {LINE_SAMPLES}',
             '  SAMPLE_TYPE = UNSIGNED_INTEGER', '  SAMPLE_BITS = 8']
    if scale is not None:
        text.append(f'  REFLECTANCE_SCALING_FACTOR = {scale}')
    text += ['END_OBJECT = IMAGE', 'END', '']
    path.write_text('\r\n'.join(text))
    return path


def write_product(directory: pathlib.Path, name: str = 'C3450201_RAW', *,
                  label: dict[str, Any] | None = None,
                  image: dict[str, Any] | None = None) -> tuple[pathlib.Path,
                                                                pathlib.Path]:
    """Write a synthetic product, an image file and its label, in a directory.

    Parameters:
        directory (pathlib.Path): The directory.
        name (str): The name of both files, without the extensions ".LBL" and ".IMG".
        label (dict[str, Any], optional): Keywords of :func:`write_label`.
        image (dict[str, Any], optional): Keywords of :func:`write_image`.

    Returns:
        tuple[pathlib.Path, pathlib.Path]: The paths of the label and the image file.
    """

    image_path = write_image(directory / f'{name}.IMG', **(image or {}))
    label_path = write_label(directory / f'{name}.LBL', image_path.name, **(label or {}))
    return (label_path, image_path)

##########################################################################################
