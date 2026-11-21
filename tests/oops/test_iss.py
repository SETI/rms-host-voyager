##########################################################################################
# tests/oops/test_iss.py
##########################################################################################
"""Tests for the Voyager ISS host, host_voyager.oops.iss."""

from collections.abc import Iterator
import itertools
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import julian
import numpy as np
import oops
import pdsparser
import pdstable
import pytest
import vicar
from filecache import FCPath, FileCache
from conftest import (CLOCK_COUNT, GEOMED_LABEL, GEOMED_VICAR, INDEX, INDEX_ROWS,
                      PLATFORM_TO_CAMERA_ANGLE, RAW_LABEL, STOP_TIME, SUPPLEMENT,
                      FakeSpice, FakeSpiceDB, camera_matrix, fake_scencd, rotation)

import host_voyager
import host_voyager.oops
import host_voyager.oops.iss as iss
from host_voyager.oops.iss import VoyagerISS

EXPOSURE = 1.92     # seconds

# The label of C3450201_RAW, reduced to the keywords the host reads
_LABEL = {
    'INSTRUMENT_HOST_NAME'       : 'VOYAGER 1',
    'INSTRUMENT_ID'              : 'ISSN',
    'PRODUCT_ID'                 : 'C3450201_RAW.IMG',
    'PRODUCT_TYPE'               : 'DECOMPRESSED_RAW_IMAGE',
    'MISSION_PHASE_NAME'         : 'SATURN ENCOUNTER',
    'TARGET_NAME'                : 'SATURN',
    'IMAGE_NUMBER'               : '34502.01',
    'IMAGE_TIME'                 : '1980-10-29T05:57:22',
    'STOP_TIME'                  : STOP_TIME,
    'SPACECRAFT_CLOCK_STOP_COUNT': CLOCK_COUNT,
    'FILTER_NAME'                : 'VIOLET',
    'EXPOSURE_DURATION'          : EXPOSURE,
}

OVERRIDE_NAMES = ('target', 'lightsource', 'path', 'frame', 'cadence', 'fov',
                  'calibrations', 'timeshift', 'navigation', 'tracker', 'parallel')


def label_dict(**changes: Any) -> dict[str, Any]:
    """The test label as a dictionary; a value of None removes the keyword."""

    dict_ = {**_LABEL, **changes}
    return {key: value for (key, value) in dict_.items() if value is not None}


def snapshot(*, return_all_planets: bool = False, check_pointing: bool = True,
             filepath: str = 'VGISS_6109/X.IMG', **changes: Any) -> Any:
    """A Snapshot from `_make_snapshot` for the image file `filepath`, which checks the
    pointing as `from_file` does unless `check_pointing` is False; keywords named for
    overrides are overrides and every other keyword changes the label, whose null values
    are then left out, as `from_file` and `from_index` leave them out."""

    overrides = {name: changes.pop(name, None) for name in OVERRIDE_NAMES}
    VoyagerISS.initialize()
    return VoyagerISS._make_snapshot(iss._without_nulls(label_dict(**changes)),
                                     filepath=filepath,
                                     return_all_planets=return_all_planets,
                                     check_pointing=check_pointing,
                                     overrides=overrides)


def tdb(iso: str) -> float:
    """The time in seconds TDB of an ISO UTC time."""

    return float(julian.tdb_from_tai(julian.tai_from_iso(iso)))


def frame_matrix(obs: Any) -> np.ndarray:
    """The J2000-to-camera matrix of an observation's frame."""

    return np.asarray(obs.frame.wrt(oops.Frame.J2000).transform_at_time(0.).matrix.vals)

##########################################################################################
# Registration and package layout
##########################################################################################


def test_iss_is_registered_with_oops() -> None:
    assert oops.Host._LOOKUP['Voyager ISS'] is VoyagerISS


def test_module_exports() -> None:
    assert iss.__all__ == ['VoyagerISS']


@pytest.mark.parametrize('module', [host_voyager, host_voyager.oops],
                         ids=['host_voyager', 'oops'])
def test_package_exposes_host(module: Any) -> None:
    assert module.VoyagerISS is VoyagerISS


# Prints whether oops and host_voyager.oops.iss are loaded after importing host_voyager,
# and again after its VoyagerISS is used.
_LAZY_IMPORT_SCRIPT = """
import sys
names = ('oops', 'host_voyager.oops.iss')
import host_voyager
print(*(name in sys.modules for name in names))
from host_voyager import VoyagerISS
print(*(name in sys.modules for name in names))
print(sys.modules['oops'].Host._LOOKUP['Voyager ISS'] is VoyagerISS)
"""


def test_host_is_imported_lazily() -> None:
    src = pathlib.Path(__file__).parents[2] / 'src'
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(src), env.get('PYTHONPATH', '')])
    result = subprocess.run([sys.executable, '-c', _LAZY_IMPORT_SCRIPT], env=env,
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ['False False', 'True True', 'True']

##########################################################################################
# Detectors
##########################################################################################


@pytest.mark.parametrize(('label', 'expected'), [
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_ID': 'ISSN'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 2', 'INSTRUMENT_ID': 'ISSW'}, True),
    ({'SPACECRAFT_NAME': 'VOYAGER 2', 'INSTRUMENT_ID': 'ISSW'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_ID': 'UVS'}, False),
    ({'SPACECRAFT_NAME': 'GALILEO ORBITER', 'INSTRUMENT_ID': 'SSI'}, False),
    ({}, False),
], ids=['vg1-nac', 'vg2-wac', 'spacecraft-name', 'other-instrument', 'galileo', 'empty'])
def test_detect_in_pds3(label: dict[str, str], expected: bool) -> None:
    assert VoyagerISS._detect_in_pds3(label) is expected


_LAB02 = 'VGR-1   FDS 34502.01   PICNO 0469S1-015   SCET 80.303 05:57:22         C'
_LAB03 = 'NA CAMERA  EXP    1920.0 MSEC FILT 1(VIOLET)  LO GAIN  SCAN RATE  5:1  C'
_LAB04 = 'ERT 80.303 07:35:06   1/ 1 FULL    RES   VIDICON TEMP  -80.00 DEG C    C'
_LAB05 = 'IN/112380/ 9 OUT/xxxxxx/xx     SATURN      DSS #63   BIT SNR    8.297  C'
_LABEL3 = 'FOR (I/F)*10000., MULTIPLY DN VALUE BY               0.10000'

# The HOST of the VGISS image files, the machine that wrote them, which is not the
# machine that runs the tests
_VICAR_HOST = 'AXP-VMS'


@pytest.mark.parametrize(('label', 'expected'), [
    ({'LAB02': _LAB02, 'LAB03': _LAB03}, True),
    ({'LAB02': _LAB02}, False),
    ({'LAB02': 'GLL', 'LAB03': _LAB03}, False),
    ({'MISSION': 'GALILEO', 'SENSOR': 'SSI'}, False),
], ids=['voyager', 'no-lab03', 'bad-lab02', 'galileo'])
def test_detect_in_vicar(label: dict[str, str], expected: bool) -> None:
    assert VoyagerISS._detect_in_vicar(label) is expected


@pytest.mark.parametrize(('label', 'expected'), [
    ({'DATA_SET_ID': 'VG1/VG2-J-ISS-2/3/4/6-PROCESSED-V1.0'}, True),
    ({'DATA_SET_ID': 'VG1/VG2-S-ISS-2/3/4/6-PROCESSED-V1.0'}, True),
    ({'DATA_SET_ID': 'VG2-U-ISS-2/3/4/6-PROCESSED-V1.0'}, True),
    ({'DATA_SET_ID': 'VG2-N-ISS-2/3/4/6-PROCESSED-V1.0'}, True),
    ({'DATA_SET_ID': 'VG1/VG2-SR-ISS-4-PROFILES-V1.0'}, False),
    ({'DATA_SET_ID': 'VG2/VG1-S-ISS-2/3/4/6-PROCESSED-V1.0'}, False),
    ({'DATA_SET_ID': 'VGG-S-ISS-2/3/4/6-PROCESSED-V1.0'}, False),
    ({'DATA_SET_ID': 'CO-S-ISSNA/ISSWA-2-EDR-V1.0'}, False),
    ({'VOLUME_ID': 'VGISS_6109'}, None),
], ids=['jupiter', 'saturn', 'uranus', 'neptune', 'ring-profiles', 'reversed-pair',
        'no-spacecraft-number', 'cassini', 'no-data-set'])
def test_detect_in_index(label: dict[str, str], expected: bool | None) -> None:
    assert VoyagerISS._detect_in_index(label) is expected


@pytest.mark.parametrize(('row', 'expected'), [
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'NARROW ANGLE CAMERA'}, True),
    ({'SPACECRAFT_NAME': 'VOYAGER 2', 'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'ULTRAVIOLET'}, False),
    ({'MISSION_NAME': 'GALILEO', 'INSTRUMENT_ID': 'SSI'}, False),
], ids=['raw-image-index', 'full-index', 'other-instrument', 'galileo'])
def test_detect_in_row(row: dict[str, str], expected: bool) -> None:
    assert VoyagerISS._detect_in_row(row) is expected

##########################################################################################
# initialize() and reset()
##########################################################################################


def test_initialize_loads_the_mission(fake_spice: FakeSpice,
                                      fake_spicedb: FakeSpiceDB) -> None:
    VoyagerISS.initialize(planets=[6], asof='2000-01-01', irregulars=False)
    assert fake_spice.solar_system[0][1]['planets'] == [6]
    assert fake_spicedb.named('furnish_inst')[0][1]['inst'] == ['ISS']


def test_initialize_runs_once(fake_spice: FakeSpice) -> None:
    VoyagerISS.initialize()
    VoyagerISS.initialize()
    assert len(fake_spice.solar_system) == 1


def test_initialize_can_repeat_after_reset(fake_spice: FakeSpice) -> None:
    VoyagerISS.initialize()
    VoyagerISS._reset()
    VoyagerISS.initialize()
    assert len(fake_spice.solar_system) == 2


def test_reset_clears_state() -> None:
    VoyagerISS.initialize()
    VoyagerISS._reset()
    assert VoyagerISS._FOVS == {}
    assert not VoyagerISS._initialized


@pytest.mark.parametrize(('camera', 'geomed', 'half_fov', 'pixels'), [
    ('NAC', False, 0.003700098, 800),
    ('NAC', True, np.radians(0.4493) / 2., 1000),
    ('WAC', False, 0.02765, 800),
    ('WAC', True, np.radians(3.364) / 2., 1000),
], ids=['nac-raw', 'nac-geomed', 'wac-raw', 'wac-geomed'])
def test_fovs(camera: str, geomed: bool, half_fov: float, pixels: int) -> None:
    VoyagerISS.initialize()
    fov = VoyagerISS._FOVS[camera, geomed]
    assert fov.uv_shape.vals.tolist() == [pixels, pixels]
    assert fov.uv_scale.vals.tolist() == pytest.approx(
        [np.arctan(np.tan(half_fov) / (pixels / 2.))] * 2)

##########################################################################################
# Timing
##########################################################################################


def test_time_ends_at_stop_time() -> None:
    # IMAGE_TIME, a second earlier here, is used only without STOP_TIME
    obs = snapshot(IMAGE_TIME='1980-10-29T05:57:21')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.texp == pytest.approx(EXPOSURE)


def test_time_falls_back_to_image_time() -> None:
    obs = snapshot(STOP_TIME=None, IMAGE_TIME='1980-10-29T05:57:23')
    assert obs.time[1] == pytest.approx(tdb('1980-10-29T05:57:23'), abs=1.e-6)


def test_time_is_not_from_the_clock_when_a_time_is_given() -> None:
    # The clock count is one FDS minute later than STOP_TIME here
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT='34502:02:001')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.time_from_sclk is False


# The fake clock places CLOCK_COUNT exactly at STOP_TIME in partition 2, and partition 3
# starts one clock cycle, about 6 years, later
_CYCLE = 65536 * 2880.


@pytest.mark.parametrize('changes', [{'STOP_TIME': None, 'IMAGE_TIME': None},
                                     {'STOP_TIME': 'UNK', 'IMAGE_TIME': 'UNK'}],
                         ids=['absent', 'unknown'])
def test_time_from_the_clock_count_without_a_time(changes: dict[str, Any]) -> None:
    obs = snapshot(EARTH_RECEIVED_TIME='1980-10-29T07:35:06', **changes)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.time_from_sclk is True


def test_time_from_the_clock_count_with_an_unknown_reception_time() -> None:
    # An index gives EARTH_RECEIVED_TIME "UNKNOWN" for 1,524 rows, which is no time
    obs = snapshot(STOP_TIME=None, IMAGE_TIME=None, EARTH_RECEIVED_TIME='UNKNOWN')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


def test_time_from_the_clock_count_in_a_given_partition() -> None:
    obs = snapshot(STOP_TIME=None, IMAGE_TIME=None, SPACECRAFT_CLOCK_PARTITION_NUMBER=3)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME) + _CYCLE, abs=1.e-6)


@pytest.mark.parametrize(('cycles', 'partition'), [(0.1, 2), (0.9, 3)])
def test_time_from_the_clock_count_in_the_partition_nearest_its_reception(
        cycles: float, partition: int) -> None:
    ert = julian.iso_from_tai(julian.tai_from_tdb(tdb(STOP_TIME) + cycles * _CYCLE))
    obs = snapshot(STOP_TIME=None, IMAGE_TIME=None, EARTH_RECEIVED_TIME=str(ert))
    expected = tdb(STOP_TIME) + (partition - 2) * _CYCLE
    assert obs.time[1] == pytest.approx(expected, abs=1.e-6)


def test_time_from_the_count_in_the_image_name() -> None:
    obs = snapshot(STOP_TIME=None, IMAGE_TIME=None, SPACECRAFT_CLOCK_STOP_COUNT=None,
                   IMAGE_NUMBER=None, SPACECRAFT_CLOCK_PARTITION_NUMBER=2,
                   filepath='VGISS_6109/C3450201_RAW.IMG')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


# Without a partition or an earth received time, the partition is the one that puts the
# count in one of Voyager 1's imaging periods: partition 2's time, STOP_TIME, falls in
# the Saturn encounter, and partition 3's in 1986
def test_time_from_the_clock_count_in_the_imaging_periods() -> None:
    obs = snapshot(STOP_TIME=None, IMAGE_TIME=None)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


def test_clock_count_outside_the_imaging_periods_is_a_spice_error() -> None:
    # About 150 days before STOP_TIME, in partition 2, and 6 years later in partition 3
    with pytest.raises(oops.OopsSpiceError,
                       match=r'^no Voyager 1 clock time for FDS count 30000:00:001 for '
                             r'VGISS_6109/X\.IMG$'):
        snapshot(STOP_TIME=None, IMAGE_TIME=None,
                 SPACECRAFT_CLOCK_STOP_COUNT='30000:00:001')


def test_no_time_and_no_clock_count_is_a_format_error() -> None:
    with pytest.raises(oops.host.HostFormatError,
                       match=r'VGISS_6109/X\.IMG has no STOP_TIME, IMAGE_TIME, '
                             r'SPACECRAFT_CLOCK_STOP_COUNT or IMAGE_NUMBER$'):
        snapshot(STOP_TIME=None, IMAGE_TIME=None, SPACECRAFT_CLOCK_STOP_COUNT=None,
                 IMAGE_NUMBER=None, SPACECRAFT_CLOCK_PARTITION_NUMBER=2)


def test_clock_count_the_clock_kernel_cannot_place_is_a_spice_error() -> None:
    # Partition 1 of the fake clock refuses every count
    with pytest.raises(oops.OopsSpiceError,
                       match=r'^no Voyager 1 clock time for FDS count 34502:01:001 for '
                             r'VGISS_6109/X\.IMG: SPICE\(NOTINPART\)$'):
        snapshot(STOP_TIME=None, IMAGE_TIME=None, SPACECRAFT_CLOCK_PARTITION_NUMBER=1)


def test_planet_from_the_time_without_a_mission_phase() -> None:
    assert snapshot(MISSION_PHASE_NAME=None).planet == 'SATURN'


@pytest.mark.parametrize(('changes', 'item'), [
    ({'EXPOSURE_DURATION': -99.9999}, 'EXPOSURE_DURATION'),
    ({'EXPOSURE_DURATION': 'UNK'}, 'EXPOSURE_DURATION'),
    ({'INSTRUMENT_ID': None}, 'INSTRUMENT_NAME'),
    ({'INSTRUMENT_HOST_NAME': None}, 'INSTRUMENT_HOST_NAME or SPACECRAFT_NAME'),
    ({'SPACECRAFT_CLOCK_STOP_COUNT': None, 'IMAGE_NUMBER': None},
     'SPACECRAFT_CLOCK_STOP_COUNT or IMAGE_NUMBER'),
], ids=['index-null-exposure', 'unknown-exposure', 'camera', 'spacecraft',
        'clock-count'])
def test_missing_required_item_is_a_format_error(changes: dict[str, Any],
                                                 item: str) -> None:
    with pytest.raises(oops.host.HostFormatError,
                       match=rf'VGISS_6109/X\.IMG has no {item}$'):
        snapshot(**changes)


def test_clock_count_is_not_needed_with_a_custom_frame() -> None:
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT=None, IMAGE_NUMBER=None,
                   frame=oops.Frame.J2000)
    assert obs.frame is oops.Frame.J2000

##########################################################################################
# Pointing
##########################################################################################


def test_pointing_comes_from_the_images_own_record(fake_spice: FakeSpice) -> None:
    obs = snapshot()
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))
    (inst, ticks, tol, ref) = fake_spice.ckgp[-1]
    assert inst == -31100
    assert ticks == fake_scencd(-31, f'2/{CLOCK_COUNT}')
    assert tol == 20
    assert ref == 'J2000'


def test_pointing_is_found_by_count_not_time(fake_spice: FakeSpice) -> None:
    # A count 16 FDS minutes after STOP_TIME, as for the real C3450201
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT='34502:17:001')
    assert frame_matrix(obs) == pytest.approx(camera_matrix('34502:17:001'))


def test_pointing_uses_a_given_partition() -> None:
    obs = snapshot(SPACECRAFT_CLOCK_PARTITION_NUMBER=3)
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT, partition=3))


def test_pointing_partition_nearest_stop_time() -> None:
    # Partition 3 starts one clock cycle (about 6 years) after partition 2
    late = julian.iso_from_tai(julian.tai_from_tdb(tdb(STOP_TIME) + 65536 * 2880.))
    obs = snapshot(STOP_TIME=str(late))
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT, partition=3))


def test_pointing_count_from_image_number() -> None:
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT=None, IMAGE_NUMBER=34502.17)
    assert frame_matrix(obs) == pytest.approx(camera_matrix('34502:17:001'))


def test_pointing_platform_to_camera(fake_spice: FakeSpice) -> None:
    snapshot(INSTRUMENT_HOST_NAME='VOYAGER 2', INSTRUMENT_ID='ISSW')
    assert fake_spice.pxform[-1] == ('VG2_SCAN_PLATFORM', 'VG2_ISSWA')
    assert fake_spice.ckgp[-1][0] == -32100


def test_pointing_matrix_order() -> None:
    expected = rotation(0, PLATFORM_TO_CAMERA_ANGLE) @ rotation(2, 0.)
    assert not np.allclose(camera_matrix(CLOCK_COUNT), expected)
    assert frame_matrix(snapshot()) == pytest.approx(camera_matrix(CLOCK_COUNT))


# The midtime of the test image, STOP_TIME less half the exposure, as an error names it
MIDTIME = '1980-10-29T05:57:21.040'


def test_missing_record_is_a_spice_error(fake_spice: FakeSpice) -> None:
    """The message names the camera, the midtime and the image file and ends with the
    SPICE short error code; the SPICE exception is kept as the cause."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.OopsSpiceError) as info:
        snapshot()
    assert str(info.value) == (f'no Voyager 1 ISS NAC pointing at {MIDTIME} for '
                               'VGISS_6109/X.IMG: SPICE(CKINSUFFDATA)')
    assert info.value.spice_code == 'SPICE(CKINSUFFDATA)'
    assert info.value.filepath == 'VGISS_6109/X.IMG'
    assert isinstance(info.value.__cause__, OSError)


def test_missing_record_names_the_spacecraft_and_camera(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-32, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.OopsSpiceError, match=r'^no Voyager 2 ISS WAC pointing at '):
        snapshot(INSTRUMENT_HOST_NAME='VOYAGER 2', INSTRUMENT_ID='ISSW')


def test_missing_record_at_the_overridden_midtime(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    cadence = oops.cadence.SnapCadence(tdb(STOP_TIME) - 10., 4.)
    with pytest.raises(oops.OopsSpiceError,
                       match=r' at 1980-10-29T05:57:14\.000 for VGISS_6109/X\.IMG'):
        snapshot(cadence=cadence)


def test_missing_record_at_the_frame_shifted_time(fake_spice: FakeSpice) -> None:
    """A frame time shift evaluates the frame that many seconds after the midtime."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.OopsSpiceError,
                       match=r' at 1980-10-29T05:57:26\.040 for VGISS_6109/X\.IMG'):
        snapshot(timeshift=(5., 'F'))


@pytest.mark.usefixtures('body_path')
def test_missing_record_with_a_tracker_at_the_midtime(fake_spice: FakeSpice) -> None:
    """A tracking frame cannot be built on a frame without pointing, so the observation
    keeps that frame and the error gives the midtime, not the tracker's epoch."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.OopsSpiceError,
                       match=r' at 1980-10-29T05:57:21\.040 for VGISS_6109/X\.IMG'):
        snapshot(target='TITAN', tracker='start')


def test_count_in_no_partition_is_a_spice_error() -> None:
    with pytest.raises(oops.OopsSpiceError) as info:
        snapshot(SPACECRAFT_CLOCK_PARTITION_NUMBER=1)
    assert str(info.value) == (f'no Voyager 1 ISS NAC pointing at {MIDTIME} for '
                               'VGISS_6109/X.IMG: SPICE(NOTINPART)')


def test_missing_frame_connection_is_a_spice_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def pxform(*args: Any) -> None:
        raise ValueError('SPICE(FRAMEDATANOTFOUND) -- pxform -- no frame data')

    monkeypatch.setattr(iss.cspyce, 'pxform', pxform)
    with pytest.raises(oops.OopsSpiceError, match=r'SPICE\(FRAMEDATANOTFOUND\)$'):
        snapshot()


def test_pointing_failure_without_a_spice_code_propagates(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an exception whose text holds a SPICE error code is missing SPICE data."""

    def ckgp(*args: Any) -> None:
        raise LookupError('not a SPICE failure')

    monkeypatch.setattr(iss.cspyce, 'ckgp', ckgp)
    with pytest.raises(LookupError, match=r'^not a SPICE failure$'):
        snapshot()


def test_clock_time_failure_without_a_spice_code_propagates(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def sct2e(*args: Any) -> None:
        raise ValueError('not a SPICE failure')

    monkeypatch.setattr(iss.cspyce, 'sct2e', sct2e)
    with pytest.raises(ValueError, match=r'^not a SPICE failure$'):
        snapshot(STOP_TIME=None, IMAGE_TIME=None, SPACECRAFT_CLOCK_PARTITION_NUMBER=2)


def test_missing_record_under_the_runtime_error_action(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Under cspyce's "RUNTIME" error action, SPICE raises RuntimeError instead."""

    def ckgp(*args: Any) -> None:
        raise RuntimeError('SPICE(CKINSUFFDATA) -- ckgp -- insufficient C kernel data')

    monkeypatch.setattr(iss.cspyce, 'ckgp', ckgp)
    with pytest.raises(oops.OopsSpiceError, match=r'SPICE\(CKINSUFFDATA\)$'):
        snapshot()


def test_other_failure_is_not_a_spice_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def ckgp(*args: Any) -> None:
        raise TypeError('not a SPICE failure')

    monkeypatch.setattr(iss.cspyce, 'ckgp', ckgp)
    with pytest.raises(TypeError, match='not a SPICE failure'):
        snapshot()


def test_unchecked_pointing_fails_when_used(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    obs = snapshot(check_pointing=False)
    with pytest.raises(oops.OopsSpiceError) as info:
        obs.frame.transform_at_time(obs.midtime)
    assert str(info.value) == (f'no Voyager 1 ISS NAC pointing at {MIDTIME} for '
                               'VGISS_6109/X.IMG: SPICE(CKINSUFFDATA)')
    assert isinstance(info.value.__cause__, OSError)


def test_unchecked_pointing_names_no_time_for_several(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    obs = snapshot(check_pointing=False)
    with pytest.raises(oops.OopsSpiceError) as info:
        obs.frame.transform_at_time(oops.Scalar([0., 1.]))
    assert str(info.value) == ('no Voyager 1 ISS NAC pointing for VGISS_6109/X.IMG: '
                               'SPICE(CKINSUFFDATA)')


def test_custom_frame_skips_the_c_kernels(fake_spice: FakeSpice,
                                          fake_spicedb: FakeSpiceDB) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    obs = snapshot(frame=oops.Frame.J2000)
    assert obs.frame is oops.Frame.J2000
    assert fake_spice.ckgp == []
    assert 'CK' not in fake_spicedb.named('used_basenames')[-1][1]['types']


def test_default_frame_reports_cks(fake_spicedb: FakeSpiceDB) -> None:
    snapshot()
    assert fake_spicedb.named('used_basenames')[-1][1]['types'] is None

##########################################################################################
# Snapshot content
##########################################################################################


def test_snapshot_subfields(fake_spicedb: FakeSpiceDB) -> None:
    obs = snapshot()
    assert obs.host == 'Voyager'
    assert obs.instrument == 'ISS'
    assert obs.detector == 'NAC'
    assert obs.filter == 'VIOLET'
    assert obs.planet == 'SATURN'
    assert obs.label_target == 'SATURN'
    assert obs.dict == label_dict()
    assert obs.spice_kernels == fake_spicedb.used
    assert obs.basename == 'X.IMG'
    assert obs.path.path_id == 'VOYAGER1'


@pytest.mark.parametrize(('changes', 'name', 'frame_id', 'inst'), [
    ({}, 'VG1_ISSNA', -31101, 'issna'),
    ({'INSTRUMENT_ID': 'ISSW'}, 'VG1_ISSWA', -31102, 'isswa'),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 2'}, 'VG2_ISSNA', -32101, 'issna'),
], ids=['vg1-nac', 'vg1-wac', 'vg2-nac'])
def test_snapshot_spice_subfields(fake_spicedb: FakeSpiceDB, changes: dict[str, Any],
                                  name: str, frame_id: int, inst: str) -> None:
    obs = snapshot(**changes)
    assert obs.spice_frame_name == name
    assert obs.spice_frame_id == frame_id
    assert obs.spice_to_frame == oops.Matrix3.IDENTITY
    assert fake_spicedb.named('used_basenames')[-1][1]['inst'] == inst


def test_snapshot_clock_counts() -> None:
    obs = snapshot(SPACECRAFT_CLOCK_START_COUNT='34502:00:769')
    assert obs.spacecraft_clock_start_count == '34502:00:769'
    assert obs.spacecraft_clock_stop_count == CLOCK_COUNT


@pytest.mark.parametrize(('key', 'subfields'), [
    ('SPACECRAFT_CLOCK_START_COUNT', ['spacecraft_clock_start_count']),
    ('FILTER_NAME', ['filter']),
    ('TARGET_NAME', ['label_target', 'target']),
], ids=['start-count', 'filter', 'target'])
def test_snapshot_leaves_out_what_no_source_gives(key: str, subfields: list[str]) -> None:
    changes: dict[str, Any] = {key: 'UNK'}
    obs = snapshot(**changes)
    assert [name for name in subfields if hasattr(obs, name)] == []
    assert key not in obs.dict


def test_snapshot_without_a_target_is_lit_by_the_sun() -> None:
    assert snapshot(TARGET_NAME=None).lightsource == 'SUN'


def test_snapshot_camera_from_instrument_name() -> None:
    obs = snapshot(INSTRUMENT_ID=None, INSTRUMENT_NAME='WIDE ANGLE CAMERA')
    assert obs.detector == 'WAC'


def test_snapshot_spacecraft_from_spacecraft_name() -> None:
    obs = snapshot(INSTRUMENT_HOST_NAME=None, SPACECRAFT_NAME='VOYAGER 2')
    assert obs.path.path_id == 'VOYAGER2'


@pytest.mark.parametrize(('product', 'pixels'), [
    ('GEOMETRICALLY_CORRECTED_IMAGE', 1000), ('CALIBRATED_IMAGE', 800), (None, 800)],
    ids=['geomed', 'calib', 'index-row'])
def test_snapshot_fov_follows_the_product(product: str | None, pixels: int) -> None:
    obs = snapshot(PRODUCT_TYPE=product)
    assert obs.fov.uv_shape.vals.tolist() == [pixels, pixels]


def test_snapshot_return_all_planets(fake_spicedb: FakeSpiceDB) -> None:
    snapshot(return_all_planets=True)
    assert len(fake_spicedb.named('used_basenames')[-1][1]['bodies']) > 2


@pytest.mark.parametrize(('label_target', 'target'), [
    ('SATURN', 'SATURN'),
    ('TITAN', 'TITAN'),
    ('S RINGS', 'SATURN_RING_PLANE'),
    ('J RINGS', 'JUPITER_RING_PLANE'),
    ('U RINGS', 'URANUS_RING_PLANE'),
    ('N RINGS', 'NEPTUNE_RING_PLANE'),
    ('DARK', 'NONE'),
    ('SKY', 'NONE'),
    ('CAL LAMPS', 'NONE'),
    ('PLAQUE', 'NONE'),
    ('STAR', 'NONE'),
    ('ORION', 'NONE'),
    ('SYSTEM', 'SATURN'),
    ('UNK SAT', 'SATURN'),
])
def test_snapshot_target(label_target: str, target: str) -> None:
    obs = snapshot(TARGET_NAME=label_target)
    assert obs.target == target
    assert obs.lightsource == 'SUN'
    assert obs.label_target == label_target


@pytest.mark.parametrize(('label_target', 'star'), [
    ('VEGA', 'alf Lyr'), ('ARCTURUS', 'alf Boo'), ('SIGMA SGR', 'sig Sgr'),
    ('THETA CAR', 'tet Car'), ('BETA CMA', 'bet CMa'), ('PLEIADES', 'M45'),
])
def test_snapshot_star_target(label_target: str, star: str) -> None:
    obs = snapshot(TARGET_NAME=label_target)
    assert obs.target == 'NONE'
    assert obs.lightsource.name == star


def test_snapshot_overrides() -> None:
    path = oops.path.FixedPath((1., 2., 3.), oops.Path.SSB, oops.Frame.J2000)
    obs = snapshot(target='TITAN', path=path, calibrations=['cal'])
    assert obs.target == 'TITAN'
    assert obs.path is path
    assert obs.calibrations == ['cal']


def test_snapshot_cadence_override() -> None:
    cadence = oops.cadence.SnapCadence(100., 5.)
    assert snapshot(cadence=cadence).time == (100., 105.)


def test_snapshot_navigation() -> None:
    obs = snapshot(navigation=(1.e-3, 0.))
    assert isinstance(obs.frame, oops.frame.Navigation)


@pytest.mark.usefixtures('body_path')
def test_snapshot_tracker() -> None:
    obs = snapshot(target='TITAN', tracker='midtime')
    assert isinstance(obs.frame, oops.frame.TrackerFrame)

##########################################################################################
# VICAR headers
##########################################################################################


def test_header_items() -> None:
    items = VoyagerISS._header_items(vicar.VicarLabel.from_file(GEOMED_VICAR))
    assert items == {
        'INSTRUMENT_HOST_NAME'       : 'VOYAGER 1',
        'INSTRUMENT_ID'              : 'ISSN',
        'PRODUCT_TYPE'               : 'GEOMETRICALLY_CORRECTED_IMAGE',
        'IMAGE_NUMBER'               : '34502.01',
        'SPACECRAFT_CLOCK_STOP_COUNT': CLOCK_COUNT,
        'IMAGE_ID'                   : '0469S1-015',
        'IMAGE_TIME'                 : '1980-10-29T05:57:22.000',
        'STOP_TIME'                  : '1980-10-29T05:57:22.000',
        'EXPOSURE_DURATION'          : EXPOSURE,
        'FILTER_NAME'                : 'VIOLET',
        'FILTER_NUMBER'              : '1',
        'TARGET_NAME'                : 'SATURN',
        'MISSION_PHASE_NAME'         : 'SATURN ENCOUNTER',
        'EARTH_RECEIVED_TIME'        : '1980-10-29T07:35:06',
    }


def _vicar_header(**keywords: Any) -> vicar.VicarImage:
    """A small VICAR image whose header holds the given keywords."""

    vic = vicar.VicarImage.from_array(np.zeros((8, 8), dtype='uint8'))
    for (key, value) in keywords.items():
        vic[key] = value
    return vic


def _header_items(**lines: str) -> dict[str, Any]:
    """The items of a VICAR header holding the test LAB02, LAB03, LAB04 and LAB05 lines,
    any of which a keyword replaces."""

    keywords = {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB04': _LAB04, 'LAB05': _LAB05} | lines
    items: dict[str, Any] = VoyagerISS._header_items(_vicar_header(**keywords))
    return items


class _TaskHeader:
    """A stand-in VICAR header with the Voyager lines and the given processing tasks."""

    def __init__(self, tasks: list[str]) -> None:
        self._tasks = ['TASK', *tasks]

    def as_dict(self, unique: bool = True) -> dict[str, str]:
        return {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB04': _LAB04, 'LAB05': _LAB05}

    def get(self, name: str, default: Any) -> Any:
        return self._tasks if name == 'TASK+' else default


@pytest.mark.parametrize(('tasks', 'product'), [
    ([], 'DECOMPRESSED_RAW_IMAGE'),
    (['VGRFILLI', 'RESSAR77'], 'CLEANED_IMAGE'),
    (['VGRFILLI', 'RESSAR77', 'FICOR77'], 'CALIBRATED_IMAGE'),
    (['VGRFILLI', 'RESSAR77', 'FICOR77', 'GEOMA'], 'GEOMETRICALLY_CORRECTED_IMAGE'),
], ids=['raw', 'cleaned', 'calib', 'geomed'])
def test_header_product_type(tasks: list[str], product: str) -> None:
    assert VoyagerISS._header_items(_TaskHeader(tasks))['PRODUCT_TYPE'] == product


def test_header_target_underscores_and_wide_angle() -> None:
    items = _header_items(LAB03=_LAB03.replace('NA CAMERA', 'WA CAMERA'),
                          LAB05=_LAB05.replace('SATURN     ', 'S_RINGS    '))
    assert items['TARGET_NAME'] == 'S RINGS'
    assert items['INSTRUMENT_ID'] == 'ISSW'


# Every target spelling of the VICAR headers of the VGISS raw images that no label uses,
# and the label's name for the same target
@pytest.mark.parametrize(('field', 'target'), [
    ('CALLAMPS', 'CAL LAMPS'),
    ('ENCELADU', 'ENCELADUS'),
    ('EPIMETHE', 'EPIMETHEUS'),
    ('PROMETHE', 'PROMETHEUS'),
    ('1985U1', 'PUCK'),
    ('1989N1', 'PROTEUS'),
    ('1989N2', 'LARISSA'),
    ('SIGMASGR', 'SIGMA SGR'),
    ('BETACMA', 'BETA CMA'),
])
def test_header_target_is_spelled_as_the_label_spells_it(field: str, target: str) -> None:
    items = _header_items(LAB05=_LAB05.replace('SATURN     ', f'{field:11}'))
    assert items['TARGET_NAME'] == target

@pytest.mark.parametrize(('filter_text', 'filter_name'), [
    ('CLEAR ', 'CLEAR'),
    ('UV    ', 'UV'),
    ('CH4/JS', 'CH4_JS'),
    ('CH4/U ', 'CH4_U'),
], ids=['padded', 'short', 'slash', 'slash-padded'])
def test_header_filter_is_spelled_as_the_label_spells_it(filter_text: str,
                                                         filter_name: str) -> None:
    items = _header_items(LAB03=_LAB03.replace('(VIOLET)', f'({filter_text})'))
    assert items['FILTER_NAME'] == filter_name


def test_header_clock_count_is_spelled_as_the_label_spells_it() -> None:
    items = _header_items(LAB02=_LAB02.replace('FDS 34502.01', 'FDS 09096.16'))
    assert items['IMAGE_NUMBER'] == '09096.16'
    assert items['SPACECRAFT_CLOCK_STOP_COUNT'] == '9096:16:001'


# Fields of LAB02 and LAB03 as the ground system garbled them in real VGISS headers
@pytest.mark.parametrize(('line', 'old', 'new', 'items'), [
    ('LAB02', 'SCET 80.303 05:57:22', 'SCET **.*** **:**:**',
     ['IMAGE_TIME', 'STOP_TIME', 'MISSION_PHASE_NAME']),
    ('LAB02', 'SCET 80.303 05:57:22', 'SCET 79.170 34:07:00',
     ['IMAGE_TIME', 'STOP_TIME', 'MISSION_PHASE_NAME']),
    ('LAB02', 'SCET 80.303 05:57:22', 'SCET 08.272 07:05:04',
     ['IMAGE_TIME', 'STOP_TIME', 'MISSION_PHASE_NAME']),
    ('LAB02', 'SCET 80.303 05:57:22', 'SCET 81.366 05:57:22',
     ['IMAGE_TIME', 'STOP_TIME', 'MISSION_PHASE_NAME']),
    ('LAB02', 'PICNO 0469S1-015', 'PICNO **********', ['IMAGE_ID']),
    ('LAB02', 'PICNO 0469S1-015', 'PICNO 0000Z1+000', ['IMAGE_ID']),
    ('LAB02', 'FDS 34502.01', 'FDS 0.00    ',
     ['IMAGE_NUMBER', 'SPACECRAFT_CLOCK_STOP_COUNT']),
    ('LAB02', 'FDS 34502.01', 'FDS 00000.00',
     ['IMAGE_NUMBER', 'SPACECRAFT_CLOCK_STOP_COUNT']),
    ('LAB03', 'EXP    1920.0 MSEC', 'EXP    ****** MSEC', ['EXPOSURE_DURATION']),
    ('LAB03', 'FILT 1(VIOLET)', 'FILT *(******)', ['FILTER_NAME', 'FILTER_NUMBER']),
    ('LAB04', 'ERT 80.303 07:35:06', 'ERT **.*** **:**:**', ['EARTH_RECEIVED_TIME']),
    ('LAB04', 'ERT 80.303 07:35:06', 'ERT 80.303 24:35:06', ['EARTH_RECEIVED_TIME']),
    ('LAB05', 'SATURN     ', '           ', ['TARGET_NAME']),
], ids=['scet-asterisks', 'scet-hour-34', 'scet-year-08', 'scet-day-366-of-1981',
        'picno-asterisks', 'picno-placeholder', 'fds-short', 'fds-zero', 'exposure',
        'filter', 'ert-asterisks', 'ert-hour-24', 'target-blank'])
def test_header_leaves_out_a_garbled_field(line: str, old: str, new: str,
                                           items: list[str]) -> None:
    lines = {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB04': _LAB04, 'LAB05': _LAB05}
    assert old in lines[line]
    found = _header_items(**{line: lines[line].replace(old, new)})
    assert [item for item in items if item in found] == []
    assert len(found) == 14 - len(items)


def test_header_keeps_day_366_of_a_leap_year() -> None:
    items = _header_items(LAB02=_LAB02.replace('SCET 80.303', 'SCET 80.366'))
    assert items['STOP_TIME'] == '1980-12-31T05:57:22.000'


def test_header_without_voyager_lines() -> None:
    assert VoyagerISS._header_items(_vicar_header()) == {
        'PRODUCT_TYPE': 'DECOMPRESSED_RAW_IMAGE'}

##########################################################################################
# Merging the label and the VICAR header
##########################################################################################


@pytest.mark.parametrize(('value', 'null'), [
    (None, True),
    ('UNK', True),
    (' n/a ', True),
    ('NULL', True),
    ('UNKNOWN', True),
    ('', True),
    ('---', True),
    ('NOT APPLICABLE', True),
    (-99.9999, True),
    (['UNK', 'N/A'], True),
    (['UNK', 1.], False),
    ([], False),
    ('NONE', False),
    (0., False),
], ids=['none', 'unk', 'lower-case-padded', 'null', 'unknown', 'empty', 'dashes',
        'not-applicable', 'index-null', 'list-of-nulls',
        'list-with-a-value', 'empty-list', 'none-text', 'zero'])
def test_is_null(value: Any, null: bool) -> None:
    assert iss._is_null(value) is null


def test_without_nulls_leaves_out_a_placeholder_picture_number() -> None:
    dict_ = {'IMAGE_ID': '0000Z2+000', 'NOTE': '0000Z2+000', 'TARGET_NAME': 'UNK'}
    assert iss._without_nulls(dict_) == {'NOTE': '0000Z2+000'}


def test_without_nulls_leaves_out_a_null_inside_an_object() -> None:
    dict_ = {'IMAGE': {'REFLECTANCE_SCALING_FACTOR': 'UNK', 'LINES': 800}}
    assert iss._without_nulls(dict_) == {'IMAGE': {'LINES': 800}}


def test_merge_takes_an_item_from_the_source_that_gives_it() -> None:
    merged = VoyagerISS._merge_items({'STOP_TIME': 'label', 'NOTE': 'NONE'},
                                     {'EXPOSURE_DURATION': 0.005, 'IMAGE_ID': 'x'})
    assert merged == {'STOP_TIME': 'label', 'NOTE': 'NONE', 'EXPOSURE_DURATION': 0.005,
                      'IMAGE_ID': 'x'}


# For each item, a label value and a differing header value, as in the named VGISS raw
# image, and the value the item's resolution rule chooses
@pytest.mark.parametrize(('item', 'label_value', 'header_value', 'chosen'), [
    ('INSTRUMENT_HOST_NAME', 'VOYAGER 1', 'VOYAGER 2', 'VOYAGER 1'),
    ('INSTRUMENT_ID', 'ISSN', 'ISSW', 'ISSN'),                                  # C1570449
    ('PRODUCT_TYPE', 'CLEANED_IMAGE', 'CALIBRATED_IMAGE', 'CLEANED_IMAGE'),
    ('IMAGE_NUMBER', '34502.01', '34502.02', '34502.01'),
    ('SPACECRAFT_CLOCK_STOP_COUNT', '34502:01:001', '34502:02:001', '34502:01:001'),
    ('IMAGE_ID', '0469S1-015', '0469S1-016', '0469S1-015'),
    ('IMAGE_TIME', '1979-05-29T09:01:38.000',                                  # C1941239
     '1979-05-29T09:00:50.000', '1979-05-29T09:01:38.000'),
    ('STOP_TIME', '1979-05-29T09:01:38.500', '1979-05-29T09:01:38.000',
     '1979-05-29T09:01:38.500'),
    ('EXPOSURE_DURATION', 0.04, 0.045, 0.04),                                   # C2037439
    ('EXPOSURE_DURATION', 0.0, 0.0075, 0.0),                                    # C1565612
    ('EXPOSURE_DURATION', 0.48, 0.0125, 0.48),                                  # C1549450
    ('EXPOSURE_DURATION', 96.0, 0.005, 96.0),                                   # C2049737
    ('FILTER_NAME', 'ORANGE', 'VIOLET', 'ORANGE'),                              # C1984746
    ('FILTER_NUMBER', '3', '1', '3'),
    ('TARGET_NAME', 'S RINGS', 'SATURN', 'S RINGS'),                            # C4364518
    ('MISSION_PHASE_NAME', 'SATURN ENCOUNTER', 'JUPITER ENCOUNTER',
     'SATURN ENCOUNTER'),
    ('EARTH_RECEIVED_TIME', '1979-07-10T03:00:37',                              # C2066403
     '1979-07-10T03:01:00', '1979-07-10T03:00:37'),
], ids=['spacecraft', 'camera', 'product', 'image-number', 'clock-count', 'picno',
        'image-time', 'stop-time', 'exposure-truncated', 'exposure-truncated-to-zero',
        'exposure-different', 'exposure-long', 'filter-name', 'filter-number',
        'target-different', 'phase', 'earth-received-time'])
def test_merge_resolution_rule(item: str, label_value: Any, header_value: Any,
                               chosen: Any) -> None:
    merged = VoyagerISS._merge_items({item: label_value}, {item: header_value})
    assert merged == {item: chosen}


def test_every_header_item_has_a_resolution_rule() -> None:
    items = VoyagerISS._header_items(vicar.VicarLabel.from_file(GEOMED_VICAR))
    assert set(items) == set(iss._RESOLUTION)


def test_every_resolution_rule_names_one_source() -> None:
    assert set(iss._RESOLUTION.values()) <= {'label', 'header'}


def test_merge_follows_a_header_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(iss._RESOLUTION, 'IMAGE_ID', 'header')
    merged = VoyagerISS._merge_items({'IMAGE_ID': '0469S1-015'},
                                     {'IMAGE_ID': '0469S1-016'})
    assert merged == {'IMAGE_ID': '0469S1-016'}

##########################################################################################
# from_file()
##########################################################################################


def test_from_file_raw_label() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [800, 800]
    assert not hasattr(obs, 'data')


@pytest.mark.parametrize('label', [RAW_LABEL, GEOMED_LABEL], ids=['raw', 'geomed'])
def test_from_file_label_names_the_image_file(label: pathlib.Path) -> None:
    obs = VoyagerISS.from_file(label, astrometry=True)
    image = label.with_suffix('.IMG')
    assert obs.basename == image.name
    assert obs.filepath.name == image.name
    assert obs.image_url.endswith(f'/test_files/{image.name}')


def test_from_file_label_for_astrometry_reads_only_the_label(
        tmp_path: pathlib.Path) -> None:
    label = tmp_path / GEOMED_LABEL.name
    shutil.copy(GEOMED_LABEL, label)
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert [name for name in ('if_scale', 'header_dict', 'data') if hasattr(obs, name)] \
        == []
    assert set(obs.label_dict) == _label_keywords(label)
    assert obs.spacecraft == 1
    assert obs.basename == GEOMED_VICAR.name


def _label_keywords(label: pathlib.Path) -> set[str]:
    """The keywords of a PDS3 label at its top level, without END and without the entries
    that the parser adds, whose names hold lower-case letters, such as "STOP_TIME_fmt"
    and "objects"."""

    return {key for key in pdsparser.Pds3Label(label).as_dict()
            if key == key.upper()} - {'END'}


def _header_dict(image: pathlib.Path | FCPath) -> dict[str, Any]:
    """The VICAR header of an image file as `header_dict` gives it: every parameter, a
    repeated one keyed "NAME[n]" for its nth occurrence."""

    params: dict[Any, Any] = vicar.VicarLabel.from_file(image).as_dict(unique=True)
    return {(key if isinstance(key, str) else f'{key[0]}[{key[1]}]'): value
            for (key, value) in params.items()}


def test_from_file_label_reads_the_label_and_the_image(tmp_path: pathlib.Path) -> None:
    (label, image) = _write_label_and_image(tmp_path, label3=_LABEL3)
    obs = VoyagerISS.from_file(label)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert set(obs.label_dict) == _label_keywords(label)
    assert obs.header_dict == _header_dict(image)


def test_from_file_label_merges_both_sources(tmp_path: pathlib.Path) -> None:
    (label, _image) = _write_label_and_image(tmp_path, label3=_LABEL3)
    obs = VoyagerISS.from_file(label)
    # Every key of either source that has a value, with the label's value for an item
    # both give, and no header line converted to items; the header's BLTYPE is empty
    keys = set(obs.label_dict) | set(obs.header_dict) | {'IMAGE_ID'}
    assert set(obs.dict) == keys - {'LAB02', 'LAB03', 'LAB05', 'BLTYPE'}
    assert obs.dict['LABEL3'] == _LABEL3
    assert obs.dict['STOP_TIME'] == obs.label_dict['STOP_TIME']
    assert obs.dict['IMAGE_ID'] == '0469S1-015'


def test_from_file_image_reads_only_the_image(tmp_path: pathlib.Path) -> None:
    image = _write_vicar(tmp_path, label3=_LABEL3)
    # A label beside the image is never read
    (tmp_path / 'C3450201.LBL').write_text('NOT A LABEL')
    obs = VoyagerISS.from_file(image)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert obs.header_dict == _header_dict(image)
    assert not hasattr(obs, 'label_dict')
    assert obs.dict['LABEL3'] == _LABEL3


def test_from_file_image_for_astrometry_reads_no_data() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert [name for name in ('data', 'label_dict') if hasattr(obs, name)] == []
    assert obs.header_dict['LAB02'] == _LAB02


def test_from_file_header_dict_keeps_every_repeated_parameter() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    tasks = vicar.VicarLabel.from_file(GEOMED_VICAR).get('TASK+', [])
    assert isinstance(tasks, list)
    assert len(tasks) > 1
    assert [obs.header_dict[f'TASK[{n}]'] for n in range(len(tasks))] == tasks


@pytest.mark.parametrize(('key', 'value'), [
    ('TASK[2]', 'RESSAR77'),
    ('USER[4]', 'SHOWALTER'),
    ('DAT_TIM[0]', 'Fri Sep  2 12:18:00 2005'),
    ('LABEL3', 'FOR (I/F)*10000., MULTIPLY DN VALUE BY               1.00000'),
])
def test_from_file_header_dict_keys(key: str, value: str) -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.header_dict[key] == value


# The four inputs: the label or the image file, read in full or for astrometry
INPUTS = ['label', 'label-astrometry', 'image', 'image-astrometry']


def _read_input(directory: pathlib.Path, input_: str, **kwargs: Any) -> Any:
    """The Snapshot from one of the four inputs of a label and image written by
    :func:`_write_label_and_image`, given `kwargs`."""

    (label, image) = _write_label_and_image(directory, label3=_LABEL3, **kwargs)
    return VoyagerISS.from_file(label if input_.startswith('label') else image,
                                astrometry=input_.endswith('astrometry'))


# The metadata dictionaries that each input gives
_DICTS = {
    'label'           : ['dict', 'label_dict', 'header_dict'],
    'label-astrometry': ['dict', 'label_dict'],
    'image'           : ['dict', 'header_dict'],
    'image-astrometry': ['dict', 'header_dict'],
}


@pytest.mark.parametrize(('input_', 'name'), [(input_, name)
                                              for (input_, names) in _DICTS.items()
                                              for name in names])
def test_from_file_metadata_is_json(tmp_path: pathlib.Path, input_: str,
                                    name: str) -> None:
    obs = _read_input(tmp_path, input_)
    value = getattr(obs, name)
    assert json.loads(json.dumps(value)) == value


@pytest.mark.parametrize(('input_', 'name'), [(input_, name)
                                              for input_ in ('label', 'image')
                                              for name in ('dict', 'header_dict')])
def test_from_file_full_read_keeps_the_header_as_written(tmp_path: pathlib.Path,
                                                         input_: str, name: str) -> None:
    """Reading the image array does not replace the header's HOST, the machine that
    wrote the file, with the reading machine."""

    obs = _read_input(tmp_path, input_)
    assert getattr(obs, name)['HOST'] == _VICAR_HOST


@pytest.mark.parametrize('astrometry', [False, True], ids=['full', 'astrometry'])
def test_from_file_vicar_image_keeps_the_header_as_written(tmp_path: pathlib.Path,
                                                           astrometry: bool) -> None:
    """A VicarImage given has already replaced its header's HOST with the reading
    machine, so the header is read again from its file, whichever read is asked for."""

    (_label, image) = _write_label_and_image(tmp_path, label3=_LABEL3)
    vic = vicar.VicarImage(image, strict=False)
    assert vic['HOST'] != _VICAR_HOST
    obs = VoyagerISS.from_file(vic, astrometry=astrometry)
    assert obs.header_dict['HOST'] == _VICAR_HOST


@pytest.mark.parametrize('input_', INPUTS)
def test_from_file_gives_the_dictionaries_of_the_sources_read(tmp_path: pathlib.Path,
                                                              input_: str) -> None:
    obs = _read_input(tmp_path, input_)
    names = ['dict', 'label_dict', 'header_dict']
    assert [name for name in names if hasattr(obs, name)] == _DICTS[input_]


def test_from_file_label_parsed_with_details_is_json(tmp_path: pathlib.Path) -> None:
    """The parser's debugging "_detail" entries, which JSON cannot hold, are left out."""

    (label, _image) = _write_label_and_image(tmp_path)
    obs = VoyagerISS.from_file(label, astrometry=True,
                               pds3_options={'method': 'strict', '_details': True})
    assert json.loads(json.dumps(obs.label_dict)) == obs.label_dict
    assert json.loads(json.dumps(obs.dict)) == obs.dict


@pytest.mark.parametrize('input_', INPUTS)
def test_from_file_dict_has_no_none(tmp_path: pathlib.Path, input_: str) -> None:
    obs = _read_input(tmp_path, input_)
    assert [key for (key, value) in obs.dict.items() if value is None] == []


@pytest.mark.parametrize('input_', ['label', 'label-astrometry'])
def test_from_file_label_dict_has_no_end_and_no_sources(tmp_path: pathlib.Path,
                                                        input_: str) -> None:
    obs = _read_input(tmp_path, input_)
    assert [key for key in obs.label_dict
            if key == 'END' or key.endswith('_source')] == []


def _parser_entries(dict_: dict[str, Any]) -> list[str]:
    """The names of the entries, at any depth, that the PDS3 label parser adds beside the
    label's keywords, such as "STOP_TIME_fmt", "^IMAGE_offset" and "objects"."""

    names = []
    for (key, value) in dict_.items():
        if key != key.upper():
            names.append(key)
        if isinstance(value, dict):
            names += _parser_entries(value)
    return names


@pytest.mark.parametrize('input_', ['label', 'label-astrometry'])
def test_from_file_label_dict_has_no_parser_entries(tmp_path: pathlib.Path,
                                                    input_: str) -> None:
    obs = _read_input(tmp_path, input_)
    assert _parser_entries(obs.label_dict) == []


@pytest.mark.parametrize('label_input', ['parsed-with-sources', 'sources-option'])
def test_from_file_label_dict_has_no_source_entries(tmp_path: pathlib.Path,
                                                    label_input: str) -> None:
    (label, _image) = _write_label_and_image(tmp_path)
    if label_input == 'parsed-with-sources':
        obs = VoyagerISS.from_file(pdsparser.Pds3Label(label, sources=True),
                                   astrometry=True)
    else:
        obs = VoyagerISS.from_file(label, astrometry=True,
                                   pds3_options={'sources': True})
    assert [key for key in obs.label_dict if key.endswith('_source')] == []


@pytest.mark.parametrize('input_', INPUTS)
def test_from_file_times_are_iso_strings(tmp_path: pathlib.Path, input_: str) -> None:
    obs = _read_input(tmp_path, input_)
    assert obs.dict['STOP_TIME'] == '1980-10-29T05:57:22.000'


def test_from_file_label_dict_gives_sets_as_lists(tmp_path: pathlib.Path) -> None:
    (label, _image) = _write_label_and_image(tmp_path)
    label.write_text(label.read_text().replace('NOTE                            = "NONE"',
                                               'NOTE = {"B", "A"}'))
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.label_dict['NOTE'] == ['B', 'A']
    assert obs.dict['NOTE'] == ['B', 'A']


def test_from_file_label_keeps_the_edit_mode_as_text(tmp_path: pathlib.Path) -> None:
    """The quoted EDIT_MODE_ID "1:32", which the fast parser reads as a time, is the text
    of the label."""

    (label, _image) = _write_label_and_image(tmp_path)
    label.write_text(label.read_text().replace('"1:1"', '"1:32"'))
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.label_dict['EDIT_MODE_ID'] == '1:32'
    assert obs.dict['EDIT_MODE_ID'] == '1:32'


def test_from_file_label_dict_gives_times_as_iso_strings(tmp_path: pathlib.Path) -> None:
    obs = _read_input(tmp_path, 'label-astrometry')
    assert obs.label_dict['START_TIME'] == '1980-10-29T05:57:20.080'


@pytest.mark.parametrize('input_', ['label', 'image', 'image-astrometry'])
def test_from_file_dict_leaves_out_the_converted_header_lines(tmp_path: pathlib.Path,
                                                              input_: str) -> None:
    """LAB02, LAB03 and LAB05 appear only under the PDS3 names of their items, and
    LABEL3, whose value no item holds, under its own name."""

    obs = _read_input(tmp_path, input_)
    assert [key for key in ('LAB02', 'LAB03', 'LAB05') if key in obs.dict] == []
    assert obs.dict['LABEL3'] == _LABEL3


@pytest.mark.parametrize('input_', INPUTS)
def test_from_file_spacecraft_tells_voyager_2(tmp_path: pathlib.Path,
                                              input_: str) -> None:
    obs = _read_input(tmp_path, input_, spacecraft=2)
    assert obs.spacecraft == 2
    assert type(obs.spacecraft) is int


def test_from_file_label_resolves_differences(tmp_path: pathlib.Path) -> None:
    """Where the label and the VICAR header differ, each item follows its rule."""

    # The label gives the exposure of 1.9255 s as 1.92 s, and names the target
    # differently
    lab03 = _LAB03.replace('EXP    1920.0 MSEC', 'EXP    1925.5 MSEC')
    (label, _image) = _write_label_and_image(tmp_path, lab03=lab03)
    text = label.read_text()
    text = text.replace('TARGET_NAME                     = "SATURN"',
                        'TARGET_NAME                     = "TITAN"')
    label.write_text(text)
    obs = VoyagerISS.from_file(label)
    assert obs.texp == pytest.approx(EXPOSURE)
    assert obs.dict['EXPOSURE_DURATION'] == pytest.approx(EXPOSURE)
    assert obs.label_target == 'TITAN'
    assert obs.target == 'TITAN'


@pytest.mark.parametrize(('field', 'target'), [('ENCELADU', 'ENCELADUS'),
                                               ('1989N1', 'PROTEUS')])
def test_from_file_image_with_a_header_spelling_of_its_target(
        tmp_path: pathlib.Path, field: str, target: str) -> None:
    image = _write_vicar(tmp_path, lab05=_LAB05.replace('SATURN     ', f'{field:11}'))
    obs = VoyagerISS.from_file(image)
    assert obs.target == target
    assert obs.label_target == target


def test_from_file_image_of_a_star_with_a_header_spelling(tmp_path: pathlib.Path) -> None:
    image = _write_vicar(tmp_path, lab05=_LAB05.replace('SATURN     ', 'SIGMASGR   '))
    obs = VoyagerISS.from_file(image)
    assert obs.lightsource.name == 'sig Sgr'


def test_from_file_label_fills_a_null_from_the_header(tmp_path: pathlib.Path) -> None:
    (label, _image) = _write_label_and_image(tmp_path)
    text = label.read_text()
    for key in ('IMAGE_TIME', 'STOP_TIME'):
        text = text.replace(f'{key:32}= 1980-10-29T05:57:22.00', f'{key:32}= "UNK"')
    text = text.replace('EXPOSURE_DURATION               = 1.920 <SECOND>',
                        'EXPOSURE_DURATION               = "UNK"')
    label.write_text(text)
    obs = VoyagerISS.from_file(label)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.texp == pytest.approx(EXPOSURE)
    assert obs.label_dict['STOP_TIME'] == 'UNK'
    assert obs.dict['STOP_TIME'] == '1980-10-29T05:57:22.000'


def test_from_file_label_fills_a_placeholder_from_the_header(
        tmp_path: pathlib.Path) -> None:
    (label, _image) = _write_label_and_image(tmp_path)
    label.write_text(label.read_text().replace('"0469S1-015"', '"0000Z1+000"'))
    obs = VoyagerISS.from_file(label)
    assert obs.label_dict['IMAGE_ID'] == '0000Z1+000'
    assert obs.dict['IMAGE_ID'] == '0469S1-015'


def test_from_file_label_reads_an_image_with_a_garbled_header(
        tmp_path: pathlib.Path) -> None:
    """A label loads even when its image's SCET is unknown, with the label's time."""

    lab02 = _LAB02.replace('SCET 80.303 05:57:22', 'SCET **.*** **:**:**')
    (label, _image) = _write_label_and_image(tmp_path, lab02=lab02)
    obs = VoyagerISS.from_file(label)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.time_from_sclk is False


def test_from_file_image_with_a_garbled_time_takes_it_from_the_clock(
        tmp_path: pathlib.Path) -> None:
    """The time of an image file whose SCET is unknown comes from its FDS count, in the
    clock partition nearest the earth received time of LAB04. This ERT is one clock cycle
    after the real one, in 1986, so it selects partition 3, while the imaging periods
    would select partition 2."""

    lab02 = _LAB02.replace('SCET 80.303 05:57:22', 'SCET **.*** **:**:**')
    lab04 = _LAB04.replace('ERT 80.303 07:35:06', 'ERT 86.295 20:23:02')
    image = _write_vicar(tmp_path, lab02=lab02, lab04=lab04)
    obs = VoyagerISS.from_file(image)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME) + _CYCLE, abs=1.e-6)
    assert obs.time_from_sclk is True
    assert 'STOP_TIME' not in obs.dict


def test_from_file_image_with_no_fds_count_takes_it_from_its_name(
        tmp_path: pathlib.Path) -> None:
    lab02 = _LAB02.replace('FDS 34502.01', 'FDS 00000.00')
    image = _write_vicar(tmp_path, lab02=lab02)
    obs = VoyagerISS.from_file(image)
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))
    assert not hasattr(obs, 'spacecraft_clock_stop_count')


def test_from_file_parsed_label_without_a_path(tmp_path: pathlib.Path) -> None:
    label = pdsparser.Pds3Label(RAW_LABEL.read_text())
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert [name for name in ('filepath', 'basename', 'abspath', 'image_url')
            if hasattr(obs, name)] == []
    with pytest.raises(oops.host.HostFormatError, match='no file path is known'):
        VoyagerISS.from_file(label)


def test_from_file_parsed_image_without_a_path() -> None:
    vic = vicar.VicarImage.from_array(np.arange(64, dtype='uint8').reshape(8, 8))
    for (key, value) in {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05}.items():
        vic[key] = value
    obs = VoyagerISS.from_file(vic)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert [name for name in ('filepath', 'basename', 'abspath', 'image_url')
            if hasattr(obs, name)] == []


def test_from_file_parsed_vicar_label_without_a_path_cannot_read_data() -> None:
    label = vicar.VicarLabel.from_file(GEOMED_VICAR)
    label.filepath = None
    with pytest.raises(oops.host.HostFormatError, match='no file path is known'):
        VoyagerISS.from_file(label)


_REMOTE_VOLUME = 'https://pds-rings.seti.org/holdings/volumes/VGISS_6xxx/VGISS_6109/'


def _remote_label(file_cache: FileCache) -> pdsparser.Pds3Label:
    """The GEOMED test label as if read from the holdings, with `file_cache` as the
    cache of its URL."""

    label = pdsparser.Pds3Label(GEOMED_LABEL.read_text())
    label.filepath = FCPath(_REMOTE_VOLUME + GEOMED_LABEL.name, filecache=file_cache)
    return label


def _put_in_cache(file_cache: FileCache, url: str) -> pathlib.Path:
    """Write a file into `file_cache` as if `url` had been retrieved; its local path."""

    local = file_cache.get_local_path(url)
    assert isinstance(local, pathlib.Path)
    local.write_bytes(b'')
    return local


def test_from_file_remote_label_for_astrometry_names_the_image_url(
        file_cache: FileCache) -> None:
    obs = VoyagerISS.from_file(_remote_label(file_cache), astrometry=True)
    assert obs.image_url == _REMOTE_VOLUME + GEOMED_VICAR.name


def test_from_file_label_for_astrometry_lacks_an_abspath() -> None:
    """Only a read that retrieves the image file gives its local path, even when the
    file is beside the label."""

    obs = VoyagerISS.from_file(GEOMED_LABEL, astrometry=True)
    assert not hasattr(obs, 'abspath')


def test_from_file_label_read_in_full_has_the_image_abspath(
        tmp_path: pathlib.Path) -> None:
    (label, image) = _write_label_and_image(tmp_path)
    obs = VoyagerISS.from_file(label)
    assert obs.abspath == image.resolve()


def test_from_file_local_label_for_astrometry_without_its_image_file(
        tmp_path: pathlib.Path) -> None:
    label = tmp_path / GEOMED_LABEL.name
    shutil.copy(GEOMED_LABEL, label)
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.filepath == FCPath(tmp_path / GEOMED_VICAR.name)


def test_from_file_label_needs_the_image_file_for_data(tmp_path: pathlib.Path) -> None:
    label = tmp_path / GEOMED_LABEL.name
    shutil.copy(GEOMED_LABEL, label)
    with pytest.raises(FileNotFoundError):
        VoyagerISS.from_file(label)


def test_from_file_label_finds_the_image_file_in_another_case(
        tmp_path: pathlib.Path) -> None:
    (label, image) = _write_label_and_image(tmp_path, image_name='c3450201.img',
                                            pointer_name='C3450201.IMG')
    # On a case-insensitive file system the name as given also finds the file
    obs = VoyagerISS.from_file(label)
    assert pathlib.Path(obs.abspath).samefile(image)


def test_from_file_label_reports_an_invalid_vicar_header(tmp_path: pathlib.Path) -> None:
    label = tmp_path / GEOMED_LABEL.name
    shutil.copy(GEOMED_LABEL, label)
    (tmp_path / GEOMED_VICAR.name).write_bytes(b'NOT A VICAR HEADER')
    with pytest.raises(oops.host.VicarHostError, match=GEOMED_VICAR.name):
        VoyagerISS.from_file(label)


@pytest.mark.parametrize('astrometry', [False, True])
def test_from_file_invalid_vicar_header_names_the_file(tmp_path: pathlib.Path,
                                                       astrometry: bool) -> None:
    image = tmp_path / 'C3450201.IMG'
    image.write_bytes(b"LBLSIZE=40      BAD='       ")
    with pytest.raises(oops.host.VicarHostError,
                       match=r'^cannot parse the VICAR header of .*C3450201\.IMG: '):
        VoyagerISS.from_file(image, astrometry=astrometry)


@pytest.mark.parametrize('input_', ['label', 'image'])
def test_from_file_truncated_image_is_a_vicar_error(tmp_path: pathlib.Path,
                                                    input_: str) -> None:
    (label, image) = _write_label_and_image(tmp_path)
    image.write_bytes(image.read_bytes()[:-10])
    with pytest.raises(oops.host.VicarHostError, match=r'C3450201\.IMG'):
        VoyagerISS.from_file(label if input_ == 'label' else image)


def test_from_file_label_without_an_image_pointer(tmp_path: pathlib.Path) -> None:
    """A Voyager ISS label of a table, such as a RESLOC or GEOMA label, lacks an IMAGE
    pointer."""

    label = tmp_path / 'C3450201_RESLOC.LBL'
    label.write_text('\r\n'.join(line for line in RAW_LABEL.read_text().splitlines()
                                 if not line.startswith('^IMAGE ')))
    with pytest.raises(oops.host.PdsHostError,
                       match=r'^Voyager ISS label lacks an \^IMAGE pointer: '
                             r'.*C3450201_RESLOC\.LBL$'):
        VoyagerISS.from_file(label, astrometry=True)


@pytest.mark.parametrize('astrometry', [False, True])
def test_from_file_vicar_table_is_not_an_image(tmp_path: pathlib.Path,
                                               astrometry: bool) -> None:
    """A Voyager ISS VICAR file of a table, such as a RESLOC or GEOMA file, is not an
    image."""

    # Like the real files, the table has no image lines
    image = _write_vicar(tmp_path, name='C3450201_RESLOC.DAT')
    text = image.read_bytes().replace(b"TYPE='IMAGE'", b"TYPE='TABULAR'")
    image.write_bytes(text.replace(b'  NL=8 ', b'  NL=0 ').replace(b'  N2=8 ', b'  N2=0 '))
    with pytest.raises(oops.host.VicarHostError,
                       match=r'C3450201_RESLOC\.DAT is not an image: TYPE is TABULAR$'):
        VoyagerISS.from_file(image, astrometry=astrometry)


def test_from_file_parsed_label_of_another_host() -> None:
    label = pdsparser.Pds3Label('PDS_VERSION_ID = PDS3\r\nSPACECRAFT_NAME = "GALILEO '
                                'ORBITER"\r\nINSTRUMENT_ID = "SSI"\r\nEND\r\n')
    with pytest.raises(oops.host.WrongHostError,
                       match=r'^not a Voyager ISS PDS3 label: Pds3Label without a file '
                             r'path$'):
        VoyagerISS.from_file(label, astrometry=True)


def test_from_file_parsed_vicar_header_of_another_host() -> None:
    vic = vicar.VicarImage.from_array(np.zeros((8, 8), dtype='uint8'))
    with pytest.raises(oops.host.WrongHostError,
                       match=r'^not a Voyager ISS VICAR file: VicarImage without a file '
                             r'path$'):
        VoyagerISS.from_file(vic)


@pytest.mark.parametrize('input_', ['label', 'label-astrometry', 'image'])
def test_from_file_missing_record_names_the_image_file(
        tmp_path: pathlib.Path, fake_spice: FakeSpice, input_: str) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    (label, image) = _write_label_and_image(tmp_path)
    with pytest.raises(oops.OopsSpiceError) as info:
        VoyagerISS.from_file(label if input_.startswith('label') else image,
                             astrometry=input_.endswith('astrometry'))
    assert str(info.value) == (f'no Voyager 1 ISS NAC pointing at {MIDTIME} for '
                               f'{FCPath(image)}: SPICE(CKINSUFFDATA)')


def test_from_file_custom_frame_never_raises_a_spice_error(
        fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True, frame=oops.Frame.J2000)
    assert obs.frame is oops.Frame.J2000

def _label_with_note_on_its_own_line(directory: pathlib.Path, *,
                                     unquoted: str | None = None) -> pathlib.Path:
    """A copy of the GEOMED label and image whose NOTE value starts on the line after its
    keyword, as in C2673426_GEOMED.LBL of VGISS_7205; return the label's path. If
    `unquoted` is given, it replaces the quoted SHUTTER_MODE_ID without quotes, which
    the strict parser rejects."""

    shutil.copy(GEOMED_VICAR, directory)
    text = GEOMED_LABEL.read_text().replace(
        'NOTE                            = "NONE"',
        'NOTE                            =\n"1 WA HALF OF 6 NA/WA ATMOS DYNAMICS PAIRS '
        'IMAGING URANUS. EXPS SET FOR HI\nLATS."')
    if unquoted is not None:
        text = text.replace('SHUTTER_MODE_ID                 = "NAONLY"',
                            f'SHUTTER_MODE_ID                 = {unquoted}')
    label = directory / GEOMED_LABEL.name
    label.write_text(text)
    return label


def test_from_file_label_the_fast_parser_rejects(tmp_path: pathlib.Path) -> None:
    label = _label_with_note_on_its_own_line(tmp_path)
    with pytest.raises(pdsparser.PdsSyntaxError):
        pdsparser.Pds3Label(label, method='fast')
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.dict['NOTE'].startswith('1 WA HALF OF 6 NA/WA ATMOS DYNAMICS PAIRS')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


def test_from_file_label_only_the_loose_parser_reads(tmp_path: pathlib.Path) -> None:
    label = _label_with_note_on_its_own_line(tmp_path, unquoted='N/A')
    with pytest.raises(pdsparser.PdsSyntaxError):
        pdsparser.Pds3Label(label, method='strict')
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.label_dict['SHUTTER_MODE_ID'] == 'N/A'
    assert 'SHUTTER_MODE_ID' not in obs.dict


def test_from_file_label_honors_another_parser_method(tmp_path: pathlib.Path) -> None:
    label = _label_with_note_on_its_own_line(tmp_path)
    obs = VoyagerISS.from_file(label, astrometry=True, pds3_options={'method': 'strict'})
    assert obs.dict['NOTE'].startswith('1 WA HALF OF 6 NA/WA ATMOS DYNAMICS PAIRS')


@pytest.mark.parametrize('method', [None, 'strict'], ids=['default', 'strict'])
def test_from_file_label_reports_an_invalid_label(tmp_path: pathlib.Path,
                                                  method: str | None) -> None:
    label = tmp_path / 'X.LBL'
    label.write_text('PDS_VERSION_ID = PDS3\r\nINSTRUMENT_ID\r\nEND\r\n')
    options = None if method is None else {'method': method}
    with pytest.raises(oops.host.PdsHostError, match=r'X\.LBL'):
        VoyagerISS.from_file(label, astrometry=True, pds3_options=options)


@pytest.mark.parametrize('time', ['1980-11-31T00:00:00', '1980-10-29T24:00:00'],
                         ids=['every-parser-value-error', 'fast-value-error'])
def test_from_file_label_with_an_invalid_time(tmp_path: pathlib.Path, time: str) -> None:
    """The fast parser rejects an invalid time with ValueError, which leads on to the
    other parsers and ends as a PDS3 error."""

    label = tmp_path / 'X.LBL'
    label.write_text(RAW_LABEL.read_text().replace('1980-10-29T05:57:22.00', time))
    with pytest.raises(oops.host.PdsHostError, match=r'X\.LBL'):
        VoyagerISS.from_file(label, astrometry=True)


def test_from_file_label_with_an_invalid_parser_option() -> None:
    with pytest.raises(oops.OopsValueError, match='unexpected keyword'):
        VoyagerISS.from_file(RAW_LABEL, astrometry=True, pds3_options={'bogus': 1})


def test_from_file_label_with_an_invalid_parser_method() -> None:
    with pytest.raises(oops.OopsValueError, match='invalid method'):
        VoyagerISS.from_file(RAW_LABEL, astrometry=True,
                             pds3_options={'method': 'bogus'})

def test_from_file_geomed_label() -> None:
    obs = VoyagerISS.from_file(GEOMED_LABEL, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]


def test_from_file_vicar_header() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))


def test_from_file_sets_local_paths(tmp_path: pathlib.Path) -> None:
    image = _write_vicar(tmp_path)
    obs = VoyagerISS.from_file(image)
    assert obs.abspath == image.resolve()
    assert obs.image_url == FCPath(image).absolute().as_posix()


def test_from_file_image_for_astrometry_lacks_an_abspath() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert not hasattr(obs, 'abspath')


def test_from_file_remote_image_has_the_abspath_of_its_retrieved_copy(
        file_cache: FileCache, tmp_path: pathlib.Path) -> None:
    url = _REMOTE_VOLUME + 'C3450201.IMG'
    local = _put_in_cache(file_cache, url)
    local.write_bytes(_write_vicar(tmp_path).read_bytes())
    obs = VoyagerISS.from_file(FCPath(url, filecache=file_cache))
    assert obs.abspath == local.resolve()


def test_from_file_given_vicar_image_lacks_an_abspath(tmp_path: pathlib.Path) -> None:
    """The caller read the image file, so this call did not retrieve it."""

    obs = VoyagerISS.from_file(vicar.VicarImage(_write_vicar(tmp_path)))
    assert not hasattr(obs, 'abspath')


def test_from_file_vicar_if_scale_from_its_header() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.if_scale == pytest.approx(1.e-4, rel=1.e-12)


def test_from_file_calibrated_product_converts_its_data_to_i_over_f() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    [calibration] = obs.calibrations
    assert isinstance(calibration, oops.calibration.FlatCalib)
    assert calibration.name == 'I/F'
    i_over_f = float(calibration.extended_from_dn(250., (0, 0)).vals)
    assert i_over_f == pytest.approx(0.025, rel=1.e-12)


def test_from_file_raw_product_has_no_calibration() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL.with_suffix('.IMG'), astrometry=True)
    assert obs.calibrations == []


def test_from_file_calibrations_override_the_i_over_f() -> None:
    calibration = oops.calibration.FlatCalib('DN', 1.)
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True, calibrations=[calibration])
    assert obs.calibrations == [calibration]


def test_from_file_raw_vicar_has_no_if_scale() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL.with_suffix('.IMG'), astrometry=True)
    assert not hasattr(obs, 'if_scale')


@pytest.mark.parametrize(('label3', 'if_scale'), [
    ('FOR (I/F)*10000., MULTIPLY DN VALUE BY               0.10000', 1.e-5),
    ('FOR (I/F)*10000., MULTIPLY DN VALUE BY               N/A', None),
    (None, None),
], ids=['calibrated', 'null-factor', 'raw'])
def test_from_file_label_if_scale_from_the_image_header(tmp_path: pathlib.Path,
                                                        label3: str | None,
                                                        if_scale: float | None) -> None:
    (label, image) = _write_label_and_image(tmp_path, label3=label3)
    from_label = VoyagerISS.from_file(label)
    from_image = VoyagerISS.from_file(image)
    if if_scale is None:
        assert not hasattr(from_label, 'if_scale')
        assert not hasattr(from_image, 'if_scale')
    else:
        assert from_label.if_scale == pytest.approx(if_scale, rel=1.e-12)
        assert from_image.if_scale == from_label.if_scale


@pytest.mark.parametrize('path', [RAW_LABEL, GEOMED_LABEL, GEOMED_VICAR],
                         ids=['raw-label', 'geomed-label', 'geomed-vicar'])
def test_from_file_spacecraft(path: pathlib.Path) -> None:
    assert VoyagerISS.from_file(path, astrometry=True).spacecraft == 1


@pytest.mark.parametrize('path', [GEOMED_LABEL, GEOMED_VICAR], ids=['label', 'vicar'])
def test_from_file_filter_is_a_plain_string(path: pathlib.Path) -> None:
    obs = VoyagerISS.from_file(path, astrometry=True)
    assert type(obs.filter) is str
    assert obs.filter == 'VIOLET'


@pytest.mark.parametrize('records_early', [0, 1], ids=['as-labeled', 'label-one-early'])
def test_from_file_label_reads_the_data_its_header_describes(
        tmp_path: pathlib.Path, records_early: int) -> None:
    """The data come from where the VICAR header places them, even if the label's IMAGE
    pointer is a record early, as in C2673426_GEOMED.LBL of VGISS_7205."""

    (label, image) = _write_label_and_image(tmp_path, records_early=records_early)
    obs = VoyagerISS.from_file(label)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert obs.basename == image.name


def _write_label_and_image(directory: pathlib.Path, *, records_early: int = 0,
                           label3: str | None = None, image_name: str = 'C3450201.IMG',
                           pointer_name: str | None = None, lab02: str = _LAB02,
                           lab03: str = _LAB03,
                           spacecraft: int = 1) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a small Voyager VICAR image and a PDS3 label for it; return their paths.

    Parameters:
        directory: Where to write both files.
        records_early: How many records before the start of the data the label's IMAGE
            pointer points.
        label3: The value of LABEL3 in the VICAR header; None to leave it out.
        image_name: The name of the image file.
        pointer_name: The name of the image file in the label's IMAGE pointer; None for
            `image_name`.
        lab02: The LAB02 line of the VICAR header.
        lab03: The LAB03 line of the VICAR header.
        spacecraft: The spacecraft that both files name.
    """

    lab02 = lab02.replace('VGR-1', f'VGR-{spacecraft}')
    image = _write_vicar(directory, name=image_name, label3=label3, lab02=lab02,
                         lab03=lab03)
    pointer_name = pointer_name or image_name
    lblsize = vicar.VicarLabel.from_file(image)['LBLSIZE']
    assert isinstance(lblsize, int)
    first_record = lblsize // 8 + 1 - records_early
    lines = RAW_LABEL.read_text().replace('C3450201_RAW_edited.IMG', pointer_name)
    lines = lines.replace('"VOYAGER 1"', f'"VOYAGER {spacecraft}"')
    lines = lines.replace('RECORD_BYTES                    = 1024', 'RECORD_BYTES = 8')
    lines = lines.replace('FILE_RECORDS                    = 804', 'FILE_RECORDS = 8')
    for pointer in ('VICAR_HEADER', 'ENGINEERING_TABLE', 'VICAR_EXTENSION_HEADER'):
        lines = '\n'.join(line for line in lines.splitlines()
                          if not line.startswith('^' + pointer))
    lines = lines.replace(f'("{pointer_name}", 4)', f'("{pointer_name}", {first_record})')
    lines = lines.replace('LINES                         = 800', 'LINES = 8')
    lines = lines.replace('LINE_SAMPLES                  = 800', 'LINE_SAMPLES = 8')
    lines = lines.replace('LINE_PREFIX_BYTES             = 224', 'LINE_PREFIX_BYTES = 0')
    label = directory / 'C3450201_RAW.LBL'
    label.write_text(_strip_objects(lines, ('VICAR_HEADER', 'ENGINEERING_TABLE',
                                            'VICAR_EXTENSION_HEADER')))
    return (label, image)


def _strip_objects(text: str, names: tuple[str, ...]) -> str:
    """The label text without the named OBJECTs and their nested OBJECTs."""

    kept = []
    depth = 0
    for line in text.splitlines():
        words = line.split()
        if depth == 0 and words[:3] in [['OBJECT', '=', name] for name in names]:
            depth = 1
            continue
        if depth:
            if words[:2] == ['OBJECT', '=']:
                depth += 1
            elif words[:2] == ['END_OBJECT', '=']:
                depth -= 1
            continue
        kept.append(line)
    return '\r\n'.join(kept) + '\r\n'


def _write_vicar(directory: pathlib.Path, *, name: str = 'C3450201.IMG',
                 label3: str | None = None, lab02: str = _LAB02,
                 lab03: str = _LAB03, lab04: str = _LAB04,
                 lab05: str = _LAB05) -> pathlib.Path:
    """Write a small Voyager VICAR image without a PDS3 label; return its path. The header
    holds LABEL3 if `label3` is given, the given LAB02, LAB03, LAB04 and LAB05, and the
    HOST of the archive's files, `_VICAR_HOST`."""

    vic = vicar.VicarImage.from_array(np.arange(64, dtype='uint8').reshape(8, 8))
    for (key, value) in {'LAB02': lab02, 'LAB03': lab03, 'LAB04': lab04,
                         'LAB05': lab05, 'HOST': _VICAR_HOST}.items():
        vic[key] = value
    if label3 is not None:
        vic['LABEL3'] = label3
    path = directory / name
    vic.write_file(path)
    return path


def test_from_file_reads_vicar_data(tmp_path: pathlib.Path) -> None:
    obs = VoyagerISS.from_file(_write_vicar(tmp_path))
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert obs.fov.uv_shape.vals.tolist() == [800, 800]


def test_from_file_vicar_label_reads_the_image(tmp_path: pathlib.Path) -> None:
    path = _write_vicar(tmp_path)
    label = vicar.VicarLabel.from_file(path)
    label.filepath = path
    obs = VoyagerISS.from_file(label)
    assert obs.data.shape == (8, 8)


def test_from_file_accepts_a_parsed_label() -> None:
    label = oops.host.host_.Pds3Label(RAW_LABEL)
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


@pytest.mark.usefixtures('body_path')
def test_from_file_passes_every_override() -> None:
    """from_file hands each override to oops.Host, which rejects `parallel` together
    with `tracker`."""

    parallel = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    with pytest.raises(oops.OopsValueError,
                       match='parallel and tracker cannot both be specified'):
        VoyagerISS.from_file(GEOMED_VICAR, astrometry=True, parallel=parallel,
                             target='TITAN', tracker='midtime')


def test_from_file_parallel_frame() -> None:
    parallel = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True, parallel=parallel)
    assert obs.frame.reference is parallel.frame


def test_from_file_initializes_iss() -> None:
    VoyagerISS.from_file(RAW_LABEL, astrometry=True)
    assert VoyagerISS._initialized


def test_host_from_file_dispatches_to_iss() -> None:
    obs = oops.Host.from_file(RAW_LABEL, astrometry=True)
    assert obs.instrument == 'ISS'


def test_host_from_file_dispatches_vicar_to_iss() -> None:
    obs = oops.Host.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.host == 'Voyager'


def test_from_file_rejects_other_hosts(tmp_path: pathlib.Path) -> None:
    label = tmp_path / 'X.LBL'
    label.write_text('PDS_VERSION_ID = PDS3\r\nSPACECRAFT_NAME = "GALILEO ORBITER"\r\n'
                     'INSTRUMENT_ID = "SSI"\r\nEND\r\n')
    with pytest.raises(oops.host.WrongHostError):
        VoyagerISS.from_file(label, astrometry=True)


def test_from_file_rejects_an_unknown_keyword() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'astrometric'"):
        VoyagerISS.from_file(RAW_LABEL, astrometric=True)


@pytest.mark.parametrize('select', [0, slice(None), 'data'])
def test_from_file_refuses_select(select: Any) -> None:
    with pytest.raises(oops.OopsValueError,
                       match='each Voyager ISS image file is one observation; select '
                             'must be None'):
        VoyagerISS.from_file(RAW_LABEL, astrometry=True, select=select)

##########################################################################################
# from_index()
##########################################################################################


def _merged_rows() -> list[dict[str, Any]]:
    """The rows of the test index merged with its supplement, as the host reads them."""

    label = VoyagerISS._index_label(FCPath(INDEX), None, None)
    return list(VoyagerISS._index_rows(INDEX, label=label))


def _index_only_rows() -> list[dict[str, Any]]:
    """The rows of the test index read without its supplement."""

    return list(VoyagerISS._index_rows(INDEX, supplement=None))


def test_from_index_reads_every_row() -> None:
    assert len(list(VoyagerISS.from_index(INDEX))) == INDEX_ROWS


def test_rows_are_paired_by_position() -> None:
    """Every VGISS supplement lists the images of its index in the same order. The class
    sets the flag itself, so a change of the oops default cannot change it."""

    assert 'MERGE_SUPPLEMENT_BY_FILE' in vars(VoyagerISS)
    assert VoyagerISS.MERGE_SUPPLEMENT_BY_FILE is False


def test_from_index_call_reads_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call reads only the labels; the rows are read when the iteration starts, a
    few at a time, and neither table is ever loaded whole."""

    reads: list[Any] = []
    loads: list[Any] = []
    iter_dicts = pdstable.PdsTable.iter_dicts
    load = pdstable.PdsTable._load

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        reads.append(self)
        return iter_dicts(self, *args, **kwargs)

    def load_spy(self: Any) -> Any:
        loads.append(self)
        return load(self)

    monkeypatch.setattr(pdstable.PdsTable, 'iter_dicts', spy)
    monkeypatch.setattr(pdstable.PdsTable, '_load', load_spy)
    snapshots = VoyagerISS.from_index(INDEX)
    assert reads == []
    assert loads == []
    next(snapshots)
    assert len(reads) == 2      # the index and the supplement
    assert loads == []


def _write_rows(table: pathlib.Path, edit: Any) -> None:
    """Rewrite the rows of a copied table through a function of its list of rows."""

    lines = table.read_bytes().split(b'\r\n')
    (rows, end) = (lines[:INDEX_ROWS], lines[INDEX_ROWS:])
    table.write_bytes(b'\r\n'.join(edit(rows) + end))


def test_from_index_yields_the_rows_before_a_missing_row(tmp_path: pathlib.Path) -> None:
    """Rows are read as the iteration proceeds, so in an index whose table ends five rows
    before its label says, every row it holds is built before the error is raised."""

    index = _copy_index(tmp_path)
    _write_rows(index.with_suffix('.tab'), lambda rows: rows[:25])
    snapshots = VoyagerISS.from_index(index)
    assert len(list(itertools.islice(snapshots, 25))) == 25
    with pytest.raises(oops.host.HostIndexError,
                       match='25 rows in file; label says 30 rows'):
        next(snapshots)


def test_from_index_raises_at_the_first_row_out_of_order(tmp_path: pathlib.Path) -> None:
    """Rows are paired by position, so a supplement whose rows 12 and 13 are swapped
    gives the first 12 rows and then raises at row 12."""

    index = _copy_index(tmp_path)

    def swap(rows: list[bytes]) -> list[bytes]:
        (rows[12], rows[13]) = (rows[13], rows[12])
        return rows

    _write_rows(tmp_path / SUPPLEMENT.with_suffix('.tab').name, swap)
    snapshots = VoyagerISS.from_index(index)
    assert len(list(itertools.islice(snapshots, 12))) == 12
    with pytest.raises(oops.host.HostIndexError,
                       match=r'^index row 12 gives .* but the supplement row in the same '
                             r'position gives'):
        next(snapshots)


def test_from_index_builds_each_row_when_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result is an iterator that builds each row's Snapshot when it reaches the row
    and yields the rows in index order."""

    built: list[Any] = []
    make_snapshot = VoyagerISS._make_snapshot

    def spy(dict_: dict[str, Any], **kwargs: Any) -> Any:
        built.append(kwargs['filepath'])
        return make_snapshot(dict_, **kwargs)

    monkeypatch.setattr(VoyagerISS, '_make_snapshot', spy)
    snapshots = VoyagerISS.from_index(INDEX)
    assert isinstance(snapshots, Iterator)
    assert built == []
    next(snapshots)
    assert len(built) == 1
    rest = list(snapshots)
    assert len(rest) == INDEX_ROWS - 1
    assert rest[8].basename == 'C3450201_RAW.IMG'


def test_from_index_merges_the_supplement() -> None:
    obs = list(VoyagerISS.from_index(INDEX))[9]
    assert obs.dict['STOP_TIME'] == STOP_TIME
    assert obs.dict['SPACECRAFT_CLOCK_PARTITION_NUMBER'] == 2


def _copy_index(directory: pathlib.Path) -> pathlib.Path:
    """Copies of the test index and supplement in `directory`; the path of the index."""

    directory.mkdir(parents=True, exist_ok=True)
    for path in (INDEX, SUPPLEMENT):
        shutil.copy(path, directory)
        shutil.copy(path.with_suffix('.tab'), directory)
    return directory / INDEX.name


def _holdings_index(holdings: pathlib.Path) -> pathlib.Path:
    """Copies of the test index and supplement where the holdings keep them; the path
    of the index."""

    return _copy_index(holdings / 'metadata/VGISS_6xxx/VGISS_6109')


# The image file of row 9 of the test index, C3450201, within its volume
_ROW9_IMAGE = 'DATA/C34502XX/C3450201_RAW.IMG'


def test_from_index_names_the_image_file_beside_the_index(tmp_path: pathlib.Path) -> None:
    """An index outside the holdings layout has its volume directory beside it."""

    image = tmp_path / 'VGISS_6109' / _ROW9_IMAGE
    obs = list(VoyagerISS.from_index(_copy_index(tmp_path)))[9]
    assert obs.filepath == FCPath(image)
    assert obs.basename == 'C3450201_RAW.IMG'
    assert obs.image_url == FCPath(image).absolute().as_posix()


def test_from_index_local_image_file_lacks_an_abspath(tmp_path: pathlib.Path) -> None:
    """Reading an index does not retrieve the image files, so a Snapshot never has a
    local path, even for a file that exists."""

    image = tmp_path / 'VGISS_6109' / _ROW9_IMAGE
    image.parent.mkdir(parents=True)
    image.write_bytes(b'')
    obs = list(VoyagerISS.from_index(_copy_index(tmp_path)))[9]
    assert not hasattr(obs, 'abspath')


def test_from_index_names_the_image_file_in_the_volumes_tree(
        tmp_path: pathlib.Path) -> None:
    obs = list(VoyagerISS.from_index(_holdings_index(tmp_path)))[9]
    image = tmp_path / 'volumes/VGISS_6xxx/VGISS_6109' / _ROW9_IMAGE
    assert obs.filepath == FCPath(image)
    assert obs.basename == 'C3450201_RAW.IMG'


@pytest.mark.parametrize('layout', ['beside', 'holdings'])
def test_from_index_without_the_volume_creates_nothing(tmp_path: pathlib.Path,
                                                       layout: str) -> None:
    """With no volume where the index places it, no directory is created."""

    index = (_copy_index(tmp_path) if layout == 'beside'
             else _holdings_index(tmp_path))
    before = sorted(tmp_path.rglob('*'))
    list(VoyagerISS.from_index(index))
    assert sorted(tmp_path.rglob('*')) == before


def _remote_index(file_cache: FileCache) -> FCPath:
    """The URL of the test index in the holdings, with `file_cache` as its cache."""

    return FCPath('https://pds-rings.seti.org/holdings/metadata/VGISS_6xxx/VGISS_6109/'
                  'VGISS_6109_raw_image_index.lbl', filecache=file_cache)


# The URL of the image file of row 9 of the test index in the holdings
_ROW9_URL = 'https://pds-rings.seti.org/holdings/volumes/VGISS_6xxx/VGISS_6109/' + _ROW9_IMAGE


def test_from_index_remote_image_file_has_a_url(file_cache: FileCache) -> None:
    obs = list(VoyagerISS.from_index(_remote_index(file_cache),
                                     row_dicts=_merged_rows()))[9]
    assert obs.image_url == _ROW9_URL
    assert str(obs.filepath) == _ROW9_URL


def test_from_index_consumer_subfields() -> None:
    obs = list(VoyagerISS.from_index(INDEX))[9]
    assert obs.spacecraft == 1
    assert not hasattr(obs, 'if_scale')
    assert obs.spacecraft_clock_stop_count == CLOCK_COUNT
    assert type(obs.filter) is str
    assert obs.filter == 'VIOLET'
    assert type(obs.label_target) is str


def test_from_index_raw_fov() -> None:
    assert {obs.fov.uv_shape.vals[0] for obs in VoyagerISS.from_index(INDEX)} == {800}


def test_from_index_both_cameras() -> None:
    assert {obs.detector for obs in VoyagerISS.from_index(INDEX)} == {'NAC', 'WAC'}


def test_from_index_with_supplement(tmp_path: pathlib.Path) -> None:
    copy = tmp_path / 'raw.lbl'
    shutil.copy(INDEX, copy)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    obs = list(VoyagerISS.from_index(copy, supplement=SUPPLEMENT))
    assert len(obs) == INDEX_ROWS


def test_from_index_finds_the_supplement_for_the_sentinel() -> None:
    obs = list(VoyagerISS.from_index(INDEX, supplement=oops.Host.SUPPLEMENT))
    assert obs[9].dict['STOP_TIME'] == STOP_TIME


def test_from_index_requires_a_raw_image_index(tmp_path: pathlib.Path) -> None:
    copy = tmp_path / 'VGISS_6109_index.lbl'
    shutil.copy(INDEX, copy)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    with pytest.raises(oops.host.HostIndexError, match='needs a raw image index'):
        VoyagerISS.from_index(copy)


def test_from_index_refuses_the_rows_of_the_full_index() -> None:
    """The name of the index is checked when rows are given too."""

    with pytest.raises(oops.host.HostIndexError, match='needs a raw image index'):
        VoyagerISS.from_index(INDEX.with_name('VGISS_6109_index.lbl'),
                              row_dicts=_merged_rows())


def test_from_index_uses_given_rows_as_they_are() -> None:
    """Rows read from the index alone lack the supplement's STOP_TIME, and are not read
    again; their time is the index's IMAGE_TIME, which is to the second."""

    obs = list(VoyagerISS.from_index(INDEX, row_dicts=_index_only_rows()[9:10]))
    assert len(obs) == 1
    assert 'STOP_TIME' not in obs[0].dict
    assert obs[0].time[1] == pytest.approx(tdb('1980-10-29T05:57:22'), abs=1.e-6)


def test_from_index_does_not_pair_given_rows_with_a_named_supplement() -> None:
    """Given rows are used as they are, so a named supplement's rows are not compared with
    them: the rows in reverse order, which differ from the supplement's from row 0, all
    give Snapshots."""

    rows = _merged_rows()[::-1]
    obs = list(VoyagerISS.from_index(INDEX, supplement=SUPPLEMENT, row_dicts=rows))
    assert len(obs) == INDEX_ROWS


def test_from_index_reads_given_rows_as_the_iteration_proceeds() -> None:
    """Given rows are taken one at a time, so a row that cannot be produced stops the
    iteration only when it is reached."""

    def rows() -> Iterator[dict[str, Any]]:
        yield from _merged_rows()[:2]
        raise RuntimeError('no third row')

    snapshots = VoyagerISS.from_index(INDEX, row_dicts=rows())
    assert len(list(itertools.islice(snapshots, 2))) == 2
    with pytest.raises(RuntimeError, match='no third row'):
        next(snapshots)


def test_from_index_requires_the_supplement(tmp_path: pathlib.Path) -> None:
    shutil.copy(INDEX, tmp_path)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    with pytest.raises(FileNotFoundError):
        VoyagerISS.from_index(tmp_path / INDEX.name)


def test_from_index_finds_an_upper_case_supplement(tmp_path: pathlib.Path) -> None:
    index = tmp_path / 'VGISS_6109_RAW_IMAGE_INDEX.LBL'
    shutil.copy(INDEX, index)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    shutil.copy(SUPPLEMENT, tmp_path / 'VGISS_6109_SUPPLEMENTAL_INDEX.LBL')
    shutil.copy(SUPPLEMENT.with_suffix('.tab'), tmp_path)
    assert len(list(VoyagerISS.from_index(index))) == INDEX_ROWS


def test_from_index_rejects_another_host(tmp_path: pathlib.Path) -> None:
    table = tmp_path / 'GO_0022_index.tab'
    # newline='' keeps Windows from writing CR CR LF, which would lengthen the records
    table.write_text('"GALILEO","SSI"\r\n', newline='')
    label = tmp_path / 'GO_0022_index.lbl'
    label.write_text('\r\n'.join([
        'PDS_VERSION_ID = PDS3', 'RECORD_TYPE = FIXED_LENGTH', 'RECORD_BYTES = 17',
        'FILE_RECORDS = 1', '^INDEX_TABLE = "GO_0022_index.tab"',
        'OBJECT = INDEX_TABLE', 'INTERCHANGE_FORMAT = ASCII', 'ROWS = 1', 'COLUMNS = 2',
        'ROW_BYTES = 17',
        'OBJECT = COLUMN', 'NAME = MISSION_NAME', 'DATA_TYPE = CHARACTER',
        'START_BYTE = 2', 'BYTES = 7', 'END_OBJECT = COLUMN',
        'OBJECT = COLUMN', 'NAME = INSTRUMENT_ID', 'DATA_TYPE = CHARACTER',
        'START_BYTE = 12', 'BYTES = 3', 'END_OBJECT = COLUMN',
        'END_OBJECT = INDEX_TABLE', 'END', '']), newline='')
    with pytest.raises(oops.host.WrongHostError, match='not a Voyager ISS index'):
        VoyagerISS.from_index(label)


def test_from_index_keeps_images_without_a_record(fake_spice: FakeSpice) -> None:
    """The row is kept without checking its pointing; using that pointing raises."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    snapshots = list(VoyagerISS.from_index(INDEX))
    assert len(snapshots) == INDEX_ROWS
    obs = snapshots[9]
    with pytest.raises(oops.OopsSpiceError) as info:
        obs.frame.transform_at_time(obs.midtime)
    image = FCPath(INDEX.parent / 'VGISS_6109/DATA/C34502XX/C3450201_RAW.IMG')
    assert str(info.value) == (f'no Voyager 1 ISS NAC pointing at {MIDTIME} for '
                               f'{image}: SPICE(CKINSUFFDATA)')


def test_from_index_keeps_them_with_a_custom_frame(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    snapshots = list(VoyagerISS.from_index(INDEX, frame=oops.Frame.J2000))
    assert snapshots[9].frame is oops.Frame.J2000


def test_from_index_row_without_a_time_takes_it_from_the_clock() -> None:
    rows = _merged_rows()
    rows[9].update({'STOP_TIME': 'UNK', 'IMAGE_TIME': 'UNK'})
    obs = list(VoyagerISS.from_index(INDEX, row_dicts=rows))[9]
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.time_from_sclk is True


def _index_with_exposure(directory: pathlib.Path, field: str) -> pathlib.Path:
    """Copies of the test index and supplement in which the EXPOSURE_DURATION of
    C3450201, the tenth row, is the nine characters `field`; the path of the index."""

    for path in (INDEX, SUPPLEMENT):
        shutil.copy(path, directory)
        shutil.copy(path.with_suffix('.tab'), directory)
    table = directory / INDEX.with_suffix('.tab').name
    lines = table.read_bytes().split(b'\r\n')
    assert lines[9][279:288] == b'   1.9200'
    lines[9] = lines[9][:279] + field.encode() + lines[9][288:]
    table.write_bytes(b'\r\n'.join(lines))
    return directory / INDEX.name


# The index labels give the exposure 7 bytes, though its field and its FORMAT, F9.4, have
# 9, so a table reader that follows the label reads 0.0075 as 0.0
def test_from_index_reads_every_digit_of_the_exposure(tmp_path: pathlib.Path) -> None:
    obs = list(VoyagerISS.from_index(_index_with_exposure(tmp_path, '   0.0075')))[9]
    assert obs.dict['EXPOSURE_DURATION'] == 0.0075
    assert obs.texp == pytest.approx(0.0075)


def test_from_index_reads_a_corrected_label_as_it_is(tmp_path: pathlib.Path) -> None:
    """A label that gives the exposure column its 9 bytes is read as it is."""

    index = _index_with_exposure(tmp_path, '   0.0075')
    text = index.read_bytes()
    exposure = text.index(b'NAME                       = EXPOSURE_DURATION')
    bytes_7 = text.index(b'BYTES                      = 7', exposure)
    index.write_bytes(text[:bytes_7] + b'BYTES                      = 9'
                      + text[bytes_7 + 30:])
    rows = [obs.dict for obs in VoyagerISS.from_index(index)]
    assert rows[9]['EXPOSURE_DURATION'] == 0.0075
    assert rows[10:] == [obs.dict for obs in VoyagerISS.from_index(INDEX)][10:]


def test_from_index_dispatch_reads_every_digit_of_the_exposure(
        tmp_path: pathlib.Path) -> None:
    obs = list(oops.Host.from_index(_index_with_exposure(tmp_path, '   0.0075')))[9]
    assert obs.dict['EXPOSURE_DURATION'] == 0.0075


def test_from_index_null_exposure_is_a_format_error(tmp_path: pathlib.Path) -> None:
    """The row is refused when the iteration reaches it, after the rows before it."""

    snapshots = VoyagerISS.from_index(_index_with_exposure(tmp_path, ' -99.9999'))
    assert len(list(itertools.islice(snapshots, 9))) == 9
    with pytest.raises(oops.host.HostFormatError,
                       match=r'C34502XX/C3450201_RAW\.IMG has no EXPOSURE_DURATION$'):
        next(snapshots)


def test_from_index_dict_is_json() -> None:
    obs = list(VoyagerISS.from_index(INDEX))[9]
    assert json.loads(json.dumps(obs.dict)) == obs.dict


def test_from_index_dict_has_no_masks() -> None:
    obs = list(VoyagerISS.from_index(INDEX))[9]
    assert [key for key in obs.dict if key.endswith('_mask')] == []


# The supplement is required, so None is refused rather than taken as "search for it"
def test_from_index_refuses_no_supplement() -> None:
    with pytest.raises(oops.host.HostIndexError,
                       match=r'Voyager ISS raw image index needs its supplemental index: '
                             r'.*VGISS_6109_raw_image_index_edited\.lbl'):
        VoyagerISS.from_index(INDEX, supplement=None)


def test_from_index_refuses_an_empty_supplement() -> None:
    """oops reads the index alone for an empty path, so the host refuses it as None."""

    with pytest.raises(oops.host.HostIndexError,
                       match=r'Voyager ISS raw image index needs its supplemental index: '):
        VoyagerISS.from_index(INDEX, supplement='')


def test_from_index_given_rows_need_no_supplement() -> None:
    obs = list(VoyagerISS.from_index(INDEX, row_dicts=_index_only_rows(),
                                     supplement=None))
    assert len(obs) == INDEX_ROWS


def test_from_index_uses_a_given_label(monkeypatch: pytest.MonkeyPatch) -> None:
    label = pdsparser.Pds3Label(INDEX.read_text().replace('VOLUME_ID                       '
                                                          '= VGISS_6109',
                                                          'VOLUME_ID = GIVEN'))
    labels: list[Any] = []
    index_rows = VoyagerISS._index_rows

    def spy(filepath: Any, **kwargs: Any) -> Any:
        labels.append(kwargs['label'])
        return index_rows(filepath, **kwargs)

    monkeypatch.setattr(VoyagerISS, '_index_rows', spy)
    VoyagerISS.from_index(INDEX, label=label)
    assert labels[0]['VOLUME_ID'] == 'GIVEN'


def test_from_index_rejects_an_unknown_keyword() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'supplementary'"):
        VoyagerISS.from_index(INDEX, supplementary=None)


@pytest.mark.parametrize('select', [0, slice(None), 'data'])
def test_from_index_refuses_select(select: Any) -> None:
    """The call itself raises, before any iteration."""

    with pytest.raises(oops.OopsValueError,
                       match='each row of a Voyager ISS raw image index is one '
                             'observation'):
        VoyagerISS.from_index(INDEX, select=select)


def test_from_index_reports_an_invalid_label(tmp_path: pathlib.Path) -> None:
    index = tmp_path / INDEX.name
    index.write_text('PDS_VERSION_ID = PDS3\r\nRECORD_TYPE\r\nEND\r\n')
    with pytest.raises(oops.host.PdsHostError, match='missing "=" at RECORD_TYPE'):
        VoyagerISS.from_index(index, supplement=SUPPLEMENT)


def test_from_index_with_an_invalid_parser_option() -> None:
    with pytest.raises(oops.OopsValueError, match='unexpected keyword'):
        VoyagerISS.from_index(INDEX, supplement=SUPPLEMENT, pds3_options={'bogus': 1})


def test_from_index_dict_holds_python_numbers() -> None:
    obs = list(VoyagerISS.from_index(INDEX))[9]
    assert type(obs.dict['SPACECRAFT_CLOCK_PARTITION_NUMBER']) is int


def test_from_index_filter() -> None:
    obs = list(VoyagerISS.from_index(INDEX,
                                     filter={'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'}))
    assert {o.detector for o in obs} == {'WAC'}
    assert 0 < len(obs) < INDEX_ROWS


def test_from_index_filter_with_an_unknown_column() -> None:
    """The call itself raises, before any iteration, and names the column."""

    with pytest.raises(oops.OopsValueError, match='CAMERA_NAME'):
        VoyagerISS.from_index(INDEX, filter={'CAMERA_NAME': 'WIDE ANGLE CAMERA'})


def test_from_index_fov_override() -> None:
    fov = oops.fov.FlatFOV(1.e-5, 10)
    assert all(obs.fov is fov for obs in VoyagerISS.from_index(INDEX, fov=fov))


def test_host_from_index_dispatches_to_iss() -> None:
    obs = list(oops.Host.from_index(INDEX))[9]
    assert obs.instrument == 'ISS'
    assert obs.dict['STOP_TIME'] == STOP_TIME
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))

##########################################################################################
