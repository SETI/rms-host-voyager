##########################################################################################
# tests/oops/test_iss.py
##########################################################################################
"""Tests for the Voyager ISS host, host_voyager.oops.iss."""

import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import julian
import numpy as np
import oops
import pytest
import vicar
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
    every other keyword changes the label."""

    overrides = {name: changes.pop(name, None) for name in OVERRIDE_NAMES}
    VoyagerISS.initialize()
    return VoyagerISS._make_snapshot(label_dict(**changes), filepath='VGISS_6109/X.LBL',
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
def test_no_time_is_unusable(changes: dict[str, Any]) -> None:
    with pytest.raises(oops.host.HostError,
                       match=r'no STOP_TIME or IMAGE_TIME .* C3450201_RAW\.IMG'):
        snapshot(**changes)


def test_time_never_comes_from_the_clock() -> None:
    # The clock count is one FDS minute later than STOP_TIME here
    obs = snapshot(SPACECRAFT_CLOCK_STOP_COUNT='34502:02:001')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)


def test_null_exposure_is_zero() -> None:
    assert snapshot(EXPOSURE_DURATION=-99.9999).texp == 0.

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


def test_count_in_no_partition_is_unusable() -> None:
    with pytest.raises(oops.host.HostError, match='no Voyager 1 scan platform pointing'):
        snapshot(SPACECRAFT_CLOCK_PARTITION_NUMBER=1)


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
    assert obs.basename == 'X.LBL'
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


def test_vicar_to_pds3() -> None:
    dict_ = VoyagerISS._vicar_to_pds3(vicar.VicarLabel.from_file(GEOMED_VICAR))
    assert dict_['INSTRUMENT_HOST_NAME'] == 'VOYAGER 1'
    assert dict_['INSTRUMENT_ID'] == 'ISSN'
    assert dict_['PRODUCT_TYPE'] == 'GEOMETRICALLY_CORRECTED_IMAGE'
    assert dict_['IMAGE_NUMBER'] == '34502.01'
    assert dict_['IMAGE_ID'] == '0469S1-015'
    assert dict_['STOP_TIME'] == '1980-10-29T05:57:22'
    assert dict_['IMAGE_TIME'] == '1980-10-29T05:57:22'
    assert dict_['EXPOSURE_DURATION'] == pytest.approx(EXPOSURE)
    assert dict_['FILTER_NAME'] == 'VIOLET'
    assert dict_['FILTER_NUMBER'] == 1
    assert dict_['TARGET_NAME'] == 'SATURN'
    assert dict_['MISSION_PHASE_NAME'] == 'SATURN ENCOUNTER'


def _vicar_header(**keywords: Any) -> vicar.VicarImage:
    """A small VICAR image whose header holds the given keywords."""

    vic = vicar.VicarImage.from_array(np.zeros((8, 8), dtype='uint8'))
    for (key, value) in keywords.items():
        vic[key] = value
    return vic


class _TaskHeader:
    """A stand-in VICAR header with the Voyager lines and the given processing tasks."""

    def __init__(self, tasks: list[str]) -> None:
        self._tasks = ['TASK', *tasks]

    def as_dict(self) -> dict[str, str]:
        return {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05}

    def get(self, name: str, default: Any) -> Any:
        return self._tasks if name == 'TASK+' else default


@pytest.mark.parametrize(('tasks', 'product'), [
    ([], 'DECOMPRESSED_RAW_IMAGE'),
    (['VGRFILLI', 'RESSAR77'], 'CLEANED_IMAGE'),
    (['VGRFILLI', 'RESSAR77', 'FICOR77'], 'CALIBRATED_IMAGE'),
    (['VGRFILLI', 'RESSAR77', 'FICOR77', 'GEOMA'], 'GEOMETRICALLY_CORRECTED_IMAGE'),
], ids=['raw', 'cleaned', 'calib', 'geomed'])
def test_vicar_product_type(tasks: list[str], product: str) -> None:
    assert VoyagerISS._vicar_to_pds3(_TaskHeader(tasks))['PRODUCT_TYPE'] == product


def test_vicar_target_underscores_and_wide_angle() -> None:
    lab03 = _LAB03.replace('NA CAMERA', 'WA CAMERA')
    lab05 = _LAB05.replace('SATURN     ', 'S_RINGS    ')
    dict_ = VoyagerISS._vicar_to_pds3(_vicar_header(LAB02=_LAB02, LAB03=lab03,
                                                    LAB05=lab05))
    assert dict_['TARGET_NAME'] == 'S RINGS'
    assert dict_['INSTRUMENT_ID'] == 'ISSW'


@pytest.mark.parametrize('keywords', [{'LAB02': _LAB02}, {'LAB03': _LAB03}],
                         ids=['no-lab03', 'no-lab02'])
def test_vicar_without_voyager_lines(keywords: dict[str, str]) -> None:
    with pytest.raises(oops.host.HostFormatError, match='no Voyager LAB02 or LAB03'):
        VoyagerISS._vicar_to_pds3(_vicar_header(**keywords))

##########################################################################################
# from_file()
##########################################################################################


def test_from_file_raw_label() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [800, 800]
    assert obs.basename == 'C3450201_RAW_edited.LBL'
    assert not hasattr(obs, 'data')


def test_from_file_geomed_label() -> None:
    obs = VoyagerISS.from_file(GEOMED_LABEL, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]


def test_from_file_vicar_header() -> None:
    obs = VoyagerISS.from_file(GEOMED_VICAR, astrometry=True)
    assert obs.fov.uv_shape.vals.tolist() == [1000, 1000]
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))


def test_from_file_sets_local_paths() -> None:
    obs = VoyagerISS.from_file(RAW_LABEL, astrometry=True)
    assert obs.abspath == RAW_LABEL.resolve()
    assert obs.image_url.endswith('/test_files/C3450201_RAW_edited.LBL')


def test_from_file_reads_pds3_data(tmp_path: pathlib.Path) -> None:
    image = (np.arange(64, dtype='uint8').reshape(8, 8))
    (tmp_path / 'C3450201_RAW.IMG').write_bytes(image.tobytes())
    lines = RAW_LABEL.read_text().replace('C3450201_RAW_edited.IMG', 'C3450201_RAW.IMG')
    lines = lines.replace('RECORD_BYTES                    = 1024', 'RECORD_BYTES = 8')
    lines = lines.replace('FILE_RECORDS                    = 804', 'FILE_RECORDS = 8')
    for pointer in ('VICAR_HEADER', 'ENGINEERING_TABLE', 'VICAR_EXTENSION_HEADER'):
        lines = '\n'.join(line for line in lines.splitlines()
                          if not line.startswith('^' + pointer))
    lines = lines.replace('("C3450201_RAW.IMG", 4)', '"C3450201_RAW.IMG"')
    lines = lines.replace('LINES                         = 800', 'LINES = 8')
    lines = lines.replace('LINE_SAMPLES                  = 800', 'LINE_SAMPLES = 8')
    lines = lines.replace('LINE_PREFIX_BYTES             = 224', 'LINE_PREFIX_BYTES = 0')
    label = tmp_path / 'C3450201_RAW.LBL'
    label.write_text(_strip_objects(lines, ('VICAR_HEADER', 'ENGINEERING_TABLE',
                                            'VICAR_EXTENSION_HEADER')))
    obs = VoyagerISS.from_file(label)
    assert obs.data.tolist() == image.tolist()


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


def _write_vicar(directory: pathlib.Path) -> pathlib.Path:
    """Write a small Voyager VICAR image with no PDS3 label; return its path."""

    vic = vicar.VicarImage.from_array(np.arange(64, dtype='uint8').reshape(8, 8))
    for (key, value) in {'LAB02': _LAB02, 'LAB03': _LAB03, 'LAB05': _LAB05}.items():
        vic[key] = value
    path = directory / 'C3450201.IMG'
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


def test_from_index_filepath() -> None:
    obs = VoyagerISS.from_index(INDEX)[9]
    assert obs.filepath == 'VGISS_6109/DATA/C34502XX/C3450201_RAW.LBL'
    assert obs.basename == 'C3450201_RAW.LBL'


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
    table.write_text('"GALILEO","SSI"\r\n')
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
        'END_OBJECT = INDEX_TABLE', 'END', '']))
    with pytest.raises(oops.host.WrongHostError, match='not a Voyager ISS index'):
        VoyagerISS.from_index(label)


def test_from_index_leaves_out_images_without_a_record(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    names = [obs.basename for obs in VoyagerISS.from_index(INDEX)]
    assert len(names) == INDEX_ROWS - 1
    assert 'C3450201_RAW.LBL' not in names


def test_from_index_keeps_them_with_a_custom_frame(fake_spice: FakeSpice) -> None:
    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    assert len(VoyagerISS.from_index(INDEX, frame=oops.Frame.J2000)) == INDEX_ROWS


def test_from_index_leaves_out_images_without_a_time() -> None:
    rows = VoyagerISS._read_index_rows(INDEX, supplement=SUPPLEMENT)
    rows[9]['STOP_TIME'] = 'UNK'
    rows[9]['IMAGE_TIME'] = 'UNK'
    names = [obs.basename for obs in VoyagerISS.from_index(INDEX, row_dicts=rows)]
    assert len(names) == INDEX_ROWS - 1
    assert 'C3450201_RAW.LBL' not in names


def test_from_index_filter() -> None:
    obs = VoyagerISS.from_index(INDEX, filter={'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'})
    assert {o.detector for o in obs} == {'WAC'}
    assert 0 < len(obs) < INDEX_ROWS


def test_from_index_fov_override() -> None:
    fov = oops.fov.FlatFOV(1.e-5, 10)
    assert all(obs.fov is fov for obs in VoyagerISS.from_index(INDEX, fov=fov))


def test_host_from_index_dispatches_to_iss() -> None:
    # oops.Host.from_index cannot name this supplement, so it reads the index alone and
    # the time comes from IMAGE_TIME, to the second
    obs = oops.Host.from_index(INDEX)[9]
    assert obs.instrument == 'ISS'
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=1.e-6)
    assert frame_matrix(obs) == pytest.approx(camera_matrix(CLOCK_COUNT))

##########################################################################################
