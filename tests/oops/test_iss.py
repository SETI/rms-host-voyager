##########################################################################################
# tests/oops/test_iss.py
##########################################################################################
"""Tests for the Voyager ISS host, host_voyager.oops.iss."""

import datetime as dt
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
import pytest
import vicar
from filecache import FCPath
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


def snapshot(*, return_all_planets: bool = False, **changes: Any) -> Any:
    """A Snapshot from `_make_snapshot`; keywords named for overrides are overrides and
    every other keyword changes the label, whose null values are then left out, as
    `from_file` and `from_index` leave them out."""

    overrides = {name: changes.pop(name, None) for name in OVERRIDE_NAMES}
    VoyagerISS.initialize()
    return VoyagerISS._make_snapshot(iss._without_nulls(label_dict(**changes)),
                                     filepath='VGISS_6109/X.IMG',
                                     return_all_planets=return_all_planets,
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
_LAB05 = 'IN/112380/ 9 OUT/xxxxxx/xx     SATURN      DSS #63   BIT SNR    8.297  C'
_LABEL3 = 'FOR (I/F)*10000., MULTIPLY DN VALUE BY               0.10000'


@pytest.mark.parametrize(('label', 'expected'), [
    ({'LAB02': _LAB02, 'LAB03': _LAB03}, True),
    ({'LAB02': _LAB02}, False),
    ({'LAB02': 'GLL', 'LAB03': _LAB03}, False),
    ({'MISSION': 'GALILEO', 'SENSOR': 'SSI'}, False),
], ids=['voyager', 'no-lab03', 'bad-lab02', 'galileo'])
def test_detect_in_vicar(label: dict[str, str], expected: bool) -> None:
    assert VoyagerISS._detect_in_vicar(label) is expected


def test_detect_in_index_defers_to_the_rows() -> None:
    assert VoyagerISS._detect_in_index({'VOLUME_ID': 'VGISS_6109'}) is None


@pytest.mark.parametrize(('row', 'expected'), [
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'NARROW ANGLE CAMERA'}, True),
    ({'SPACECRAFT_NAME': 'VOYAGER 2', 'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'ULTRAVIOLET'}, False),
    ({'MISSION_NAME': 'GALILEO', 'INSTRUMENT_ID': 'SSI'}, False),
], ids=['raw-image-index', 'volume-index', 'other-instrument', 'galileo'])
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
    obs = snapshot()
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert obs.texp == pytest.approx(EXPOSURE)


def test_time_falls_back_to_image_time() -> None:
    obs = snapshot(STOP_TIME=None, IMAGE_TIME='1980-10-29T05:57:23')
    assert obs.time[1] == pytest.approx(tdb('1980-10-29T05:57:23'), abs=1.e-6)


@pytest.mark.parametrize('changes', [{'STOP_TIME': None, 'IMAGE_TIME': None},
                                     {'STOP_TIME': 'UNK', 'IMAGE_TIME': 'UNK'}],
                         ids=['absent', 'unknown'])
def test_no_time_is_a_format_error(changes: dict[str, Any]) -> None:
    with pytest.raises(oops.host.HostFormatError,
                       match=r'VGISS_6109/X\.IMG has no STOP_TIME or IMAGE_TIME'):
        snapshot(**changes)


def test_time_never_comes_from_the_clock() -> None:
    # The clock count is one FDS minute later than STOP_TIME here
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT='34502:02:001')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


@pytest.mark.parametrize(('changes', 'item'), [
    ({'EXPOSURE_DURATION': -99.9999}, 'EXPOSURE_DURATION'),
    ({'EXPOSURE_DURATION': 'UNK'}, 'EXPOSURE_DURATION'),
    ({'INSTRUMENT_ID': None}, 'INSTRUMENT_NAME'),
    ({'INSTRUMENT_HOST_NAME': None}, 'INSTRUMENT_HOST_NAME or SPACECRAFT_NAME'),
    ({'MISSION_PHASE_NAME': 'UNK'}, 'MISSION_PHASE_NAME'),
    ({'SPACECRAFT_CLOCK_STOP_COUNT': None, 'IMAGE_NUMBER': None}, 'IMAGE_NUMBER'),
], ids=['index-null-exposure', 'unknown-exposure', 'camera', 'spacecraft', 'phase',
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


def test_missing_record_is_unusable(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.host.HostError,
                       match=rf'no Voyager 1 scan platform pointing .* {CLOCK_COUNT}'):
        snapshot()


def test_missing_record_names_the_spice_error(fake_spice: FakeSpice) -> None:
    """The message carries the SPICE short error code, by which a caller recognizes
    missing SPICE data, and the SPICE exception is kept as the cause."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    with pytest.raises(oops.host.HostError) as info:
        snapshot()
    assert str(info.value) == (f'no Voyager 1 scan platform pointing for clock count '
                               f'{CLOCK_COUNT}: SPICE(CKINSUFFDATA)')
    assert isinstance(info.value.__cause__, OSError)


def test_count_in_no_partition_is_unusable() -> None:
    with pytest.raises(oops.host.HostError) as info:
        snapshot(SPACECRAFT_CLOCK_PARTITION_NUMBER=1)
    assert str(info.value) == ('no Voyager 1 scan platform pointing for clock count '
                               f'{CLOCK_COUNT}: SPICE(NOTINPART)')


def test_missing_record_without_a_spice_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def ckgp(*args: Any) -> None:
        raise LookupError('no record')

    monkeypatch.setattr(iss.cspyce, 'ckgp', ckgp)
    with pytest.raises(oops.host.HostError) as info:
        snapshot()
    assert str(info.value) == ('no Voyager 1 scan platform pointing for clock count '
                               f'{CLOCK_COUNT}')


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
        'IMAGE_TIME'                 : dt.datetime(1980, 10, 29, 5, 57, 22),
        'STOP_TIME'                  : dt.datetime(1980, 10, 29, 5, 57, 22),
        'EXPOSURE_DURATION'          : EXPOSURE,
        'FILTER_NAME'                : 'VIOLET',
        'FILTER_NUMBER'              : '1',
        'TARGET_NAME'                : 'SATURN',
        'MISSION_PHASE_NAME'         : 'SATURN ENCOUNTER',
    }


def _vicar_header(**keywords: Any) -> vicar.VicarImage:
    """A small VICAR image whose header holds the given keywords."""

    vic = vicar.VicarImage.from_array(np.zeros((8, 8), dtype='uint8'))
    for (key, value) in keywords.items():
        vic[key] = value
    return vic


def _header_items(**lines: str) -> dict[str, Any]:
    """The items of a VICAR header holding the test LAB02, LAB03 and LAB05 lines, any of
    which a keyword replaces."""

    keywords = {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05} | lines
    items: dict[str, Any] = VoyagerISS._header_items(_vicar_header(**keywords))
    return items


class _TaskHeader:
    """A stand-in VICAR header with the Voyager lines and the given processing tasks."""

    def __init__(self, tasks: list[str]) -> None:
        self._tasks = ['TASK', *tasks]

    def as_dict(self, unique: bool = True) -> dict[str, str]:
        return {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05}

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
    ('LAB02', 'PICNO 0469S1-015', 'PICNO **********', ['IMAGE_ID']),
    ('LAB02', 'PICNO 0469S1-015', 'PICNO 0000Z1+000', ['IMAGE_ID']),
    ('LAB02', 'FDS 34502.01', 'FDS 0.00    ',
     ['IMAGE_NUMBER', 'SPACECRAFT_CLOCK_STOP_COUNT']),
    ('LAB02', 'FDS 34502.01', 'FDS 00000.00',
     ['IMAGE_NUMBER', 'SPACECRAFT_CLOCK_STOP_COUNT']),
    ('LAB03', 'EXP    1920.0 MSEC', 'EXP    ****** MSEC', ['EXPOSURE_DURATION']),
    ('LAB03', 'FILT 1(VIOLET)', 'FILT *(******)', ['FILTER_NAME', 'FILTER_NUMBER']),
    ('LAB05', 'SATURN     ', '           ', ['TARGET_NAME']),
], ids=['scet-asterisks', 'scet-hour-34', 'scet-year-08', 'picno-asterisks',
        'picno-placeholder', 'fds-short', 'fds-zero', 'exposure', 'filter',
        'target-blank'])
def test_header_leaves_out_a_garbled_field(line: str, old: str, new: str,
                                           items: list[str]) -> None:
    lines = {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05}
    assert old in lines[line]
    found = _header_items(**{line: lines[line].replace(old, new)})
    assert [item for item in items if item in found] == []
    assert len(found) == 13 - len(items)


def test_header_without_voyager_lines() -> None:
    assert VoyagerISS._header_items(_vicar_header()) == {
        'PRODUCT_TYPE': 'DECOMPRESSED_RAW_IMAGE'}

##########################################################################################
# Merging the label and the VICAR header
##########################################################################################


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
    ('IMAGE_TIME', dt.datetime(1979, 5, 29, 9, 1, 38),                        # C1941239
     dt.datetime(1979, 5, 29, 9, 0, 50), dt.datetime(1979, 5, 29, 9, 1, 38)),
    ('STOP_TIME', dt.datetime(1979, 5, 29, 9, 1, 38, 500000),
     dt.datetime(1979, 5, 29, 9, 1, 38), dt.datetime(1979, 5, 29, 9, 1, 38, 500000)),
    ('EXPOSURE_DURATION', 0.04, 0.045, 0.045),                                  # C2037439
    ('EXPOSURE_DURATION', 0.0, 0.0075, 0.0075),                                 # C1565612
    ('EXPOSURE_DURATION', 351.35, 351.359, 351.359),                            # C1138003
    ('EXPOSURE_DURATION', 0.48, 0.0125, 0.48),                                  # C1549450
    ('EXPOSURE_DURATION', 96.0, 0.005, 96.0),                                   # C2049737
    ('FILTER_NAME', 'ORANGE', 'VIOLET', 'ORANGE'),                              # C1984746
    ('FILTER_NUMBER', '3', '1', '3'),
    ('TARGET_NAME', 'ENCELADUS', 'ENCELADU', 'ENCELADUS'),                      # C4398323
    ('TARGET_NAME', 'S RINGS', 'SATURN', 'S RINGS'),                            # C4364518
    ('MISSION_PHASE_NAME', 'SATURN ENCOUNTER', 'JUPITER ENCOUNTER',
     'SATURN ENCOUNTER'),
], ids=['spacecraft', 'camera', 'product', 'image-number', 'clock-count', 'picno',
        'image-time', 'stop-time', 'exposure-truncated', 'exposure-truncated-to-zero',
        'exposure-truncated-long', 'exposure-different', 'exposure-long', 'filter-name',
        'filter-number', 'target-truncated', 'target-different', 'phase'])
def test_merge_resolution_rule(item: str, label_value: Any, header_value: Any,
                               chosen: Any) -> None:
    merged = VoyagerISS._merge_items({item: label_value}, {item: header_value})
    assert merged == {item: chosen}


def test_every_header_item_has_a_resolution_rule() -> None:
    items = VoyagerISS._header_items(vicar.VicarLabel.from_file(GEOMED_VICAR))
    assert set(items) == set(iss._RESOLUTION)

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
    assert obs.abspath == image.resolve()
    assert obs.image_url.endswith(f'/test_files/{image.name}')


def test_from_file_label_for_astrometry_reads_only_the_label(
        tmp_path: pathlib.Path) -> None:
    label = tmp_path / GEOMED_LABEL.name
    shutil.copy(GEOMED_LABEL, label)
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert [name for name in ('if_scale', 'header_dict', 'data') if hasattr(obs, name)] \
        == []
    assert obs.label_dict == pdsparser.Pds3Label(label, sources=True).as_dict()
    assert obs.spacecraft == 1
    assert obs.basename == GEOMED_VICAR.name


def test_from_file_label_reads_the_label_and_the_image(tmp_path: pathlib.Path) -> None:
    (label, image) = _write_label_and_image(tmp_path, label3=_LABEL3)
    obs = VoyagerISS.from_file(label)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert obs.label_dict == pdsparser.Pds3Label(label, sources=True).as_dict()
    assert obs.header_dict == vicar.VicarLabel.from_file(image).as_dict(unique=False)


def test_from_file_label_merges_both_sources(tmp_path: pathlib.Path) -> None:
    (label, _image) = _write_label_and_image(tmp_path, label3=_LABEL3)
    obs = VoyagerISS.from_file(label)
    # Every key of either source, with the label's value for an item both give
    assert set(obs.dict) == set(obs.label_dict) | set(obs.header_dict) | {'IMAGE_ID'}
    assert obs.dict['LAB02'] == _LAB02
    assert obs.dict['LABEL3'] == _LABEL3
    assert obs.dict['STOP_TIME'] == obs.label_dict['STOP_TIME']
    assert obs.dict['IMAGE_ID'] == '0469S1-015'


def test_from_file_image_reads_only_the_image(tmp_path: pathlib.Path) -> None:
    image = _write_vicar(tmp_path, label3=_LABEL3)
    # A label beside the image is never read
    (tmp_path / 'C3450201.LBL').write_text('NOT A LABEL')
    obs = VoyagerISS.from_file(image)
    assert obs.data.tolist() == np.arange(64).reshape(8, 8).tolist()
    assert obs.header_dict == vicar.VicarLabel.from_file(image).as_dict(unique=False)
    assert not hasattr(obs, 'label_dict')
    assert obs.dict['LABEL3'] == _LABEL3


def test_from_file_image_for_astrometry_reads_no_data() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert [name for name in ('data', 'label_dict') if hasattr(obs, name)] == []
    assert obs.header_dict['LAB02'] == _LAB02


def test_from_file_label_resolves_differences(tmp_path: pathlib.Path) -> None:
    """Where the label and the VICAR header differ, each item follows its rule."""

    # The label truncates the exposure of 1.9255 s to 1.92 s, and names the target
    # differently
    lab03 = _LAB03.replace('EXP    1920.0 MSEC', 'EXP    1925.5 MSEC')
    (label, _image) = _write_label_and_image(tmp_path, lab03=lab03)
    text = label.read_text()
    text = text.replace('TARGET_NAME                     = "SATURN"',
                        'TARGET_NAME                     = "TITAN"')
    label.write_text(text)
    obs = VoyagerISS.from_file(label)
    assert obs.texp == pytest.approx(1.9255)
    assert obs.dict['EXPOSURE_DURATION'] == pytest.approx(1.9255)
    assert obs.label_dict['EXPOSURE_DURATION'] == pytest.approx(EXPOSURE)
    assert obs.label_target == 'TITAN'
    assert obs.target == 'TITAN'


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
    assert obs.dict['STOP_TIME'] == dt.datetime(1980, 10, 29, 5, 57, 22)


def test_from_file_label_reads_an_image_with_a_garbled_header(
        tmp_path: pathlib.Path) -> None:
    """A label loads even when its image's SCET is unknown, and the image file alone
    then reports what is missing."""

    lab02 = _LAB02.replace('SCET 80.303 05:57:22', 'SCET **.*** **:**:**')
    (label, image) = _write_label_and_image(tmp_path, lab02=lab02)
    obs = VoyagerISS.from_file(label)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    with pytest.raises(oops.host.HostFormatError,
                       match=r'C3450201\.IMG has no STOP_TIME or IMAGE_TIME'):
        VoyagerISS.from_file(image)


def test_from_file_parsed_label_without_a_path(tmp_path: pathlib.Path) -> None:
    label = pdsparser.Pds3Label(RAW_LABEL.read_text())
    obs = VoyagerISS.from_file(label, astrometry=True)
    assert [name for name in ('filepath', 'basename', 'abspath', 'image_url')
            if hasattr(obs, name)] == []
    with pytest.raises(oops.host.HostFormatError, match='without a file path'):
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
    with pytest.raises(oops.host.HostFormatError, match='without a file path'):
        VoyagerISS.from_file(label)


def test_from_file_remote_label_for_astrometry_has_no_abspath() -> None:
    params: dict[str, Any] = {}
    overrides = dict.fromkeys(OVERRIDE_NAMES)
    image = FCPath('https://pds-rings.seti.org/holdings/volumes/X.IMG')
    VoyagerISS._apply_path_overrides(params, image, False, overrides)
    assert 'abspath' not in params
    assert params['image_url'] == 'https://pds-rings.seti.org/holdings/volumes/X.IMG'


def test_from_file_local_label_for_astrometry_has_an_abspath() -> None:
    obs = VoyagerISS.from_file(GEOMED_LABEL, astrometry=True)
    assert obs.abspath == GEOMED_VICAR.resolve()


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
    with pytest.raises(oops.host.VicarHostError):
        VoyagerISS.from_file(label)


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
    with pytest.raises(oops.host.PdsHostError):
        VoyagerISS.from_file(label, astrometry=True, pds3_options=options)


def test_from_file_geomed_label() -> None:
    obs = VoyagerISS.from_file(GEOMED_LABEL, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]


def test_from_file_vicar_header() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))


def test_from_file_sets_local_paths() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.abspath == GEOMED_VICAR.resolve()
    assert obs.image_url.endswith('/test_files/C3450201_GEOMED_edited.IMG')


def test_from_file_vicar_if_scale_from_its_header() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.if_scale == pytest.approx(1.e-4, rel=1.e-12)


def test_from_file_raw_vicar_has_no_if_scale() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL.with_suffix('.IMG'), astrometry=True)
    assert not hasattr(obs, 'if_scale')


@pytest.mark.parametrize(('label3', 'if_scale'), [
    ('FOR (I/F)*10000., MULTIPLY DN VALUE BY               0.10000', 1.e-5),
    (None, None),
], ids=['calibrated', 'raw'])
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
                           lab03: str = _LAB03) -> tuple[pathlib.Path, pathlib.Path]:
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
    """

    image = _write_vicar(directory, name=image_name, label3=label3, lab02=lab02,
                         lab03=lab03)
    pointer_name = pointer_name or image_name
    lblsize = vicar.VicarLabel.from_file(image)['LBLSIZE']
    assert isinstance(lblsize, int)
    first_record = lblsize // 8 + 1 - records_early
    lines = RAW_LABEL.read_text().replace('C3450201_RAW_edited.IMG', pointer_name)
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
                 lab03: str = _LAB03) -> pathlib.Path:
    """Write a small Voyager VICAR image with no PDS3 label; return its path. The header
    holds LABEL3 if `label3` is given, and the given LAB02 and LAB03."""

    vic = vicar.VicarImage.from_array(np.arange(64, dtype='uint8').reshape(8, 8))
    for (key, value) in {'LAB02': lab02, 'LAB03': lab03, 'LAB05': _LAB05}.items():
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

##########################################################################################
# from_index()
##########################################################################################


def test_from_index_reads_every_row() -> None:
    assert len(VoyagerISS.from_index(INDEX)) == INDEX_ROWS


def test_from_index_merges_the_supplement() -> None:
    obs = VoyagerISS.from_index(INDEX)[9]
    assert obs.dict['STOP_TIME'] == STOP_TIME
    assert obs.dict['SPACECRAFT_CLOCK_PARTITION_NUMBER'] == 2


def test_from_index_names_the_image_file() -> None:
    obs = VoyagerISS.from_index(INDEX)[9]
    assert obs.filepath == 'VGISS_6109/DATA/C34502XX/C3450201_RAW.IMG'
    assert obs.basename == 'C3450201_RAW.IMG'


def test_from_index_consumer_subfields() -> None:
    obs = VoyagerISS.from_index(INDEX)[9]
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
    obs = VoyagerISS.from_index(copy, supplement=SUPPLEMENT)
    assert len(obs) == INDEX_ROWS


def test_from_index_requires_a_raw_image_index(tmp_path: pathlib.Path) -> None:
    copy = tmp_path / 'VGISS_6109_index.lbl'
    shutil.copy(INDEX, copy)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    with pytest.raises(oops.host.HostIndexError, match='needs a raw image index'):
        VoyagerISS.from_index(copy)


def test_from_index_rejects_the_rows_of_a_volume_index(tmp_path: pathlib.Path) -> None:
    copy = tmp_path / 'VGISS_6109_index.lbl'
    shutil.copy(INDEX, copy)
    shutil.copy(INDEX.with_suffix('.tab'), tmp_path)
    rows = VoyagerISS._read_index_rows(INDEX, supplement=SUPPLEMENT)
    with pytest.raises(oops.host.HostIndexError, match='needs a raw image index'):
        VoyagerISS.from_index(copy, row_dicts=rows)


def test_from_index_merges_the_supplement_into_given_rows() -> None:
    rows = VoyagerISS._read_index_rows(INDEX)[9:11]
    obs = VoyagerISS.from_index(INDEX, row_dicts=rows)
    assert [o.dict['STOP_TIME'] for o in obs] == [STOP_TIME, '1980-10-29T06:02:10.00']


def test_from_index_requires_a_supplement_row_for_each_given_row() -> None:
    rows = VoyagerISS._read_index_rows(INDEX)[9:10]
    rows[0]['FILE_SPECIFICATION_NAME'] = 'DATA/C34502XX/C3450299_RAW.LBL'
    with pytest.raises(oops.host.HostIndexError,
                       match=r'no row for VGISS_6109/DATA/C34502XX/C3450299_RAW\.LBL'):
        VoyagerISS.from_index(INDEX, row_dicts=rows)


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
    assert len(VoyagerISS.from_index(index)) == INDEX_ROWS


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


def test_from_index_leaves_out_images_without_a_record(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    names = [obs.basename for obs in VoyagerISS.from_index(INDEX)]
    assert len(names) == INDEX_ROWS - 1
    assert 'C3450201_RAW.IMG' not in names


def test_from_index_keeps_them_with_a_custom_frame(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    assert len(VoyagerISS.from_index(INDEX, frame=oops.Frame.J2000)) == INDEX_ROWS


def test_from_index_leaves_out_images_without_a_time() -> None:
    rows = VoyagerISS._read_index_rows(INDEX, supplement=SUPPLEMENT)
    rows[9]['STOP_TIME'] = 'UNK'
    rows[9]['IMAGE_TIME'] = 'UNK'
    names = [obs.basename for obs in VoyagerISS.from_index(INDEX, row_dicts=rows)]
    assert len(names) == INDEX_ROWS - 1
    assert 'C3450201_RAW.IMG' not in names


def test_from_index_leaves_out_images_without_an_exposure() -> None:
    rows = VoyagerISS._read_index_rows(INDEX, supplement=SUPPLEMENT)
    rows[9]['EXPOSURE_DURATION'] = -99.9999
    names = [obs.basename for obs in VoyagerISS.from_index(INDEX, row_dicts=rows)]
    assert names == [row['PRODUCT_ID'] for (i, row) in enumerate(rows) if i != 9]


def test_from_index_filter() -> None:
    obs = VoyagerISS.from_index(INDEX, filter={'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'})
    assert {o.detector for o in obs} == {'WAC'}
    assert 0 < len(obs) < INDEX_ROWS


def test_from_index_fov_override() -> None:
    fov = oops.fov.FlatFOV(1.e-5, 10)
    assert all(obs.fov is fov for obs in VoyagerISS.from_index(INDEX, fov=fov))


def test_host_from_index_dispatches_to_iss() -> None:
    # oops.Host.from_index cannot name this supplement, so VoyagerISS merges it
    obs = oops.Host.from_index(INDEX)[9]
    assert obs.instrument == 'ISS'
    assert obs.dict['STOP_TIME'] == STOP_TIME
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))

##########################################################################################
