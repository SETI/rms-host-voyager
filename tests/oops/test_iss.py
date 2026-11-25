##########################################################################################
# tests/oops/test_iss.py
##########################################################################################
"""Tests for the Voyager ISS host, host_voyager.oops.iss."""

import datetime as dt
import pathlib
import re
import shutil
from typing import Any

import cspyce
import julian
import numpy as np
import oops
import pytest
from conftest import (CLOCK_COUNT, GEOMED_VICAR, HEADER_LINES, IF_LINE, INDEX,
                      INDEX_ROWS, LINE_SAMPLES, LINES,
                      RAW_LABEL, SPICE_FRAME_ANGLE, STOP_TIME, SUPPLEMENT, FakeSpice,
                      FakeSpiceDB, camera_matrix, fake_scencd, fake_sct2e, rotation,
                      write_image,
                      write_label, write_product)
from filecache import FCPath
from vicar import VicarImage, VicarLabel

import host_voyager
import host_voyager.oops
from host_voyager.oops.iss import VoyagerISS

EXPOSURE = 1.92     # seconds
SCET = dt.datetime(1980, 10, 29, 5, 57, 22)

# The tolerance of a comparison of times in seconds TDB, which are of order 1e9 s, so that
# pytest.approx's default relative tolerance would allow hundreds of seconds
TIME_TOLERANCE = 1.e-3


def tdb(iso: str) -> float:
    """The time in seconds TDB of an ISO UTC time."""

    return float(julian.tdb_from_tai(julian.tai_from_iso(iso)))


def esc(path: pathlib.Path) -> str:
    """A path as the host writes it in a message, escaped for a regular expression."""

    return re.escape(str(FCPath(path)))


def frame_matrix(obs: Any) -> np.ndarray:
    """The J2000-to-camera matrix of an observation's frame."""

    frame = oops.Frame.as_frame(obs.frame)
    return np.asarray(frame.wrt(oops.Frame.J2000).transform_at_time(0.).matrix.vals)


def read_label(directory: pathlib.Path, name: str = 'C3450201_RAW', *,
               label: dict[str, Any] | None = None, image: dict[str, Any] | None = None,
               **kwargs: Any) -> Any:
    """The Snapshot of a synthetic product, read from its label."""

    (label_path, _) = write_product(directory, name, label=label, image=image)
    return VoyagerISS.from_file(label_path, **kwargs)


def read_image(directory: pathlib.Path, name: str = 'C3450201_RAW', **kwargs: Any) -> Any:
    """The Snapshot of a synthetic image file, read from the file alone; keywords of
    :func:`write_image` change the file, and every other keyword goes to from_file."""

    image_keys = {'header', 'tasks', 'if_line', 'bands'}
    image = {key: kwargs.pop(key) for key in list(kwargs) if key in image_keys}
    path = write_image(directory / f'{name}.IMG', **image)
    return VoyagerISS.from_file(path, **kwargs)


def lab02(**fields: str) -> str:
    """A LAB02 line of C3450201 with some of its fields replaced."""

    values = {'vgr': '1', 'fds': '34502.01', 'picno': '0469S1-015',
              'scet': '80.303 05:57:22'} | fields
    return (f'VGR-{values["vgr"]}   FDS {values["fds"]}   PICNO {values["picno"]}   '
            f'SCET {values["scet"]}         C')


def lab03(*, camera: str = 'NA', exp: str = '1920.0', filt: str = '1(VIOLET)') -> str:
    """A LAB03 line of C3450201 with some of its fields replaced."""

    return f'{camera} CAMERA  EXP {exp:>9} MSEC FILT {filt}  LO GAIN  SCAN RATE  5:1  C'


def lab05(target: str) -> str:
    """A LAB05 line naming a target."""

    return f'IN/112380/ 9 OUT/xxxxxx/xx     {target:<12}DSS #63   BIT SNR    8.297  C'


# A row of a raw image index merged with its supplement, as the table reader gives it
ROW = {
    'VOLUME_NAME'                 : 'VGISS_6109',
    'FILE_SPECIFICATION_NAME'     : 'DATA/C34502XX/C3450201_RAW.LBL',
    'PRODUCT_ID'                  : 'C3450201_RAW.IMG',
    'INSTRUMENT_HOST_NAME'        : 'VOYAGER 1',
    'INSTRUMENT_NAME'             : 'NARROW ANGLE CAMERA',
    'MISSION_PHASE_NAME'          : 'SATURN ENCOUNTER',
    'TARGET_NAME'                 : 'SATURN',
    'IMAGE_NUMBER'                : np.float64(34502.01),
    'IMAGE_TIME'                  : '1980-10-29T05:57:22',
    'STOP_TIME'                   : STOP_TIME,
    'SPACECRAFT_CLOCK_START_COUNT': '34502:00:768',
    'SPACECRAFT_CLOCK_STOP_COUNT' : CLOCK_COUNT,
    'FILTER_NAME'                 : 'VIOLET',
    'EXPOSURE_DURATION'           : np.float64(EXPOSURE),
    'EXPOSURE_DURATION_mask'      : np.False_,
}


def from_row(**changes: Any) -> Any:
    """The Snapshot of ROW changed by `changes`, in which None removes a column; a
    keyword named for a from_index argument is passed to it."""

    arguments = {key: changes.pop(key) for key in list(changes)
                 if key in ('frame', 'select', 'return_all_planets', 'target')}
    row = {key: value for (key, value) in (ROW | changes).items() if value is not None}
    (obs,) = VoyagerISS.from_index(INDEX, row_dicts=[row], **arguments)
    return obs

##########################################################################################
# Registration and package layout
##########################################################################################


def test_iss_is_registered_with_oops() -> None:
    """Importing the module registers the host with oops.Host."""

    assert VoyagerISS in oops.Host._HOSTS


@pytest.mark.parametrize('module', [host_voyager, host_voyager.oops])
def test_package_exposes_host(module: Any) -> None:
    """Both packages export the host class."""

    assert module.VoyagerISS is VoyagerISS

##########################################################################################
# Detectors
##########################################################################################


@pytest.mark.parametrize(('label', 'expected'), [
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 2', 'INSTRUMENT_ID': 'ISSW'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_ID': 'PPS'}, False),
    ({'INSTRUMENT_HOST_NAME': 'GALILEO ORBITER', 'INSTRUMENT_ID': 'ISSN'}, False),
    ({'SPACECRAFT_NAME': 'VOYAGER 1', 'INSTRUMENT_ID': 'ISSN'}, False),
], ids=['voyager-iss', 'other-instrument', 'other-mission', 'spacecraft-name'])
def test_detect_in_pds3(label: dict[str, str], expected: bool) -> None:
    """A label is Voyager ISS's if INSTRUMENT_HOST_NAME and INSTRUMENT_ID say so."""

    assert VoyagerISS._detect_in_pds3(label) is expected


@pytest.mark.parametrize(('label', 'expected'), [
    (HEADER_LINES, True),
    ({'LAB03': HEADER_LINES['LAB03']}, False),
    ({'LAB02': HEADER_LINES['LAB02']}, False),
], ids=['voyager-header', 'no-lab02', 'no-lab03'])
def test_detect_in_vicar(label: dict[str, str], expected: bool) -> None:
    """A header is Voyager ISS's if its LAB02 names the spacecraft and LAB03 the camera."""

    assert VoyagerISS._detect_in_vicar(label) is expected


def test_detect_in_index() -> None:
    """An index label cannot tell; the rows decide."""

    assert VoyagerISS._detect_in_index({'DATA_SET_ID': 'VG1/VG2-S-ISS-2/3/4/6'}) is None


@pytest.mark.parametrize(('row', 'expected'), [
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'WIDE ANGLE CAMERA'}, True),
    ({'INSTRUMENT_HOST_NAME': 'VOYAGER 1', 'INSTRUMENT_NAME': 'PPS'}, False),
], ids=['voyager-iss', 'other-instrument'])
def test_detect_in_row(row: dict[str, str], expected: bool) -> None:
    """A row is Voyager ISS's if it names a Voyager camera."""

    assert VoyagerISS._detect_in_row(row) is expected

##########################################################################################
# initialize()
##########################################################################################


def test_initialize_loads_the_mission(fake_spice: FakeSpice,
                                      fake_spicedb: FakeSpiceDB) -> None:
    """initialize() defines the Solar System and furnishes the ISS kernels, with the
    caller's options."""

    VoyagerISS.initialize(planets=[7, 8], asof='2010-01-01', irregulars=False)
    assert fake_spice.solar_system[0][1] == {'asof': '2010-01-01', 'planets': [7, 8],
                                             'irregulars': False}
    assert fake_spicedb.named('furnish_inst')[0][1] == {'inst': ['ISS'],
                                                        'asof': '2010-01-01'}


def test_initialize_runs_once(fake_spice: FakeSpice) -> None:
    """A second call does nothing."""

    VoyagerISS.initialize()
    VoyagerISS.initialize(planets=[5])
    assert len(fake_spice.solar_system) == 1


def test_from_file_initializes(tmp_path: pathlib.Path, fake_spice: FakeSpice) -> None:
    """from_file() calls initialize() itself."""

    read_label(tmp_path)
    assert len(fake_spice.solar_system) == 1


def test_from_index_initializes(fake_spice: FakeSpice) -> None:
    """from_index() calls initialize() when it is called, before any row is read."""

    VoyagerISS.from_index(INDEX)
    assert len(fake_spice.solar_system) == 1


@pytest.mark.parametrize(('camera', 'geomed', 'half_fov', 'pixels'), [
    ('NAC', False, 0.003700098, 800),
    ('NAC', True, np.radians(0.4493) / 2., 1000),
    ('WAC', False, 0.02765, 800),
    ('WAC', True, np.radians(3.364) / 2., 1000),
])
def test_fovs(camera: str, geomed: bool, half_fov: float, pixels: int) -> None:
    """Each camera has a raw and a geometrically corrected flat FOV."""

    VoyagerISS.initialize()
    fov = VoyagerISS._FOVS[camera, geomed]
    scale = np.arctan(np.tan(half_fov) / (pixels / 2.))
    assert tuple(fov.uv_shape.vals) == (pixels, pixels)
    assert tuple(fov.uv_scale.vals) == pytest.approx((scale, scale))

##########################################################################################
# from_file(): the files read
##########################################################################################


def test_label_reads_the_image_data(tmp_path: pathlib.Path) -> None:
    """Given a label, the data come from the image file it names."""

    obs = read_label(tmp_path)
    assert np.array_equal(obs.data, np.arange(LINES * LINE_SAMPLES).reshape(LINES,
                                                                            LINE_SAMPLES))


def test_label_reads_the_data_through_the_vicar_file(tmp_path: pathlib.Path) -> None:
    """The data are read where the VICAR header places them, whatever record the label's
    IMAGE pointer names."""

    (label_path, image_path) = write_product(tmp_path)
    label_path.write_text(label_path.read_text().replace(f'"{image_path.name}", 1)',
                                                         f'"{image_path.name}", 9)'))
    obs = VoyagerISS.from_file(label_path)
    assert obs.data[0, 1] == 1


def test_label_sources(tmp_path: pathlib.Path) -> None:
    """Given a label, label_dict and header_dict are the two parsed labels."""

    obs = read_label(tmp_path)
    assert obs.label_dict['STOP_TIME_fmt'] == '1980-10-29T05:57:22.000'
    assert obs.header_dict['LAB02'] == HEADER_LINES['LAB02']


def test_label_path_attributes(tmp_path: pathlib.Path) -> None:
    """Given a label, the file subfields name the image file, and the label's name it."""

    (label_path, image_path) = write_product(tmp_path)
    obs = VoyagerISS.from_file(label_path)
    assert obs.filepath == FCPath(image_path)
    assert obs.basename == image_path.name
    assert obs.image_url == image_path.as_posix()
    assert obs.image_abspath == image_path.resolve()
    assert obs.label_url == label_path.as_posix()
    assert obs.label_abspath == label_path.resolve()


def test_label_for_astrometry_reads_the_label_alone(tmp_path: pathlib.Path) -> None:
    """With astrometry, a label is read alone, and its image file need not exist."""

    label_path = write_label(tmp_path / 'C3450201_RAW.LBL', 'C3450201_RAW.IMG')
    obs = VoyagerISS.from_file(label_path, astrometry=True)
    assert sorted(set(obs.subfields) & {'data', 'header_dict', 'image_abspath',
                                        'label_abspath'}) == ['label_abspath']


def test_label_needs_its_image_file(tmp_path: pathlib.Path) -> None:
    """Without astrometry, a label whose image file is missing raises an OSError."""

    label_path = write_label(tmp_path / 'C3450201_RAW.LBL', 'C3450201_RAW.IMG')
    with pytest.raises(FileNotFoundError, match=r'C3450201_RAW\.IMG'):
        VoyagerISS.from_file(label_path)


def test_image_reads_the_image_file_alone(tmp_path: pathlib.Path) -> None:
    """Given the image file, it alone is read."""

    obs = read_image(tmp_path)
    assert sorted(set(obs.subfields) & {'data', 'header_dict', 'image_abspath',
                                        'label_dict', 'label_url'}) == [
        'data', 'header_dict', 'image_abspath']


def test_image_for_astrometry_reads_the_header(tmp_path: pathlib.Path) -> None:
    """With astrometry, the image file's header is read without its data."""

    obs = read_image(tmp_path, astrometry=True)
    assert sorted(set(obs.subfields) & {'data', 'header_dict', 'image_abspath'}) == [
        'header_dict', 'image_abspath']


def test_given_vicar_image(tmp_path: pathlib.Path) -> None:
    """A VicarImage read from the file, as oops.Host.from_file passes it, gives its data,
    and its file path names the file."""

    path = write_image(tmp_path / 'C3450201_RAW.IMG')
    image = VicarImage(path, strict=False)
    image.filepath = path
    obs = VoyagerISS.from_file(image)
    assert obs.data[0, 1] == 1
    assert obs.basename == 'C3450201_RAW.IMG'


def test_given_vicar_label_for_astrometry(tmp_path: pathlib.Path) -> None:
    """A VicarLabel read for astrometry gives the header alone."""

    path = write_image(tmp_path / 'C3450201_RAW.IMG')
    obs = VoyagerISS.from_file(VicarLabel(path, strict=False), astrometry=True)
    assert 'data' not in obs.subfields
    assert obs.dict['STOP_TIME'] == SCET


def test_given_vicar_label_for_a_full_read(tmp_path: pathlib.Path) -> None:
    """Given a VicarLabel without astrometry, the image file it was read from supplies
    the data."""

    path = write_image(tmp_path / 'C3450201_RAW.IMG')
    obs = VoyagerISS.from_file(VicarLabel(path, strict=False))
    assert obs.data[0, 1] == 1


@pytest.mark.parametrize('kind', ['label', 'image'])
def test_oops_host_dispatches(tmp_path: pathlib.Path, kind: str) -> None:
    """oops.Host.from_file hands a Voyager ISS label or image file to this host."""

    (label_path, image_path) = write_product(tmp_path)
    obs = oops.Host.from_file(label_path if kind == 'label' else image_path)
    assert obs.basename == 'C3450201_RAW.IMG'


def test_real_label_parses() -> None:
    """A real VGISS label is read with its IMAGE pointer."""

    obs = VoyagerISS.from_file(RAW_LABEL, astrometry=True)
    assert obs.basename == 'C3450201_RAW_edited.IMG'


def test_unknown_keyword() -> None:
    """from_file takes no pixel offset, or any other keyword not listed."""

    with pytest.raises(TypeError, match="unexpected keyword argument 'offset'"):
        VoyagerISS.from_file(RAW_LABEL, offset=(1., 2.))


def test_select_none_returns_the_snapshot(tmp_path: pathlib.Path) -> None:
    """select=None returns the one Snapshot."""

    assert isinstance(read_label(tmp_path, select=None), oops.observation.Snapshot)


def test_select_zero_returns_the_snapshot(tmp_path: pathlib.Path) -> None:
    """select=0 chooses the one Snapshot from the tuple that holds it."""

    assert isinstance(read_label(tmp_path, select=0), oops.observation.Snapshot)


def test_select_one_is_out_of_range(tmp_path: pathlib.Path) -> None:
    """select=1 is an index beyond the one Snapshot."""

    with pytest.raises(oops.OopsIndexError, match='out of range'):
        read_label(tmp_path, select=1)

##########################################################################################
# from_file(): files that cannot be read
##########################################################################################


def test_label_without_an_image_pointer(tmp_path: pathlib.Path) -> None:
    """A label without an IMAGE pointer raises PdsHostError naming it."""

    path = write_label(tmp_path / 'C3450201_RESLOC.LBL', None)
    with pytest.raises(oops.host.PdsHostError,
                       match=f'^Voyager ISS label {esc(path)} has no \\^IMAGE pointer$'):
        VoyagerISS.from_file(path)


def test_label_that_cannot_be_parsed(tmp_path: pathlib.Path) -> None:
    """A label that the parser refuses raises PdsHostError naming it."""

    path = write_label(tmp_path / 'C3450201_RAW.LBL', 'C3450201_RAW.IMG',
                       NOTE='"unclosed')
    with pytest.raises(oops.host.PdsHostError, match='cannot parse the PDS3 label'):
        VoyagerISS.from_file(path, astrometry=True)


def test_label_of_another_host(tmp_path: pathlib.Path) -> None:
    """A label of another instrument raises WrongHostError."""

    path = write_label(tmp_path / 'X.LBL', 'X.IMG', INSTRUMENT_ID='"PPS"')
    with pytest.raises(oops.host.WrongHostError, match='not a Voyager ISS PDS3 label'):
        VoyagerISS.from_file(path, astrometry=True)


def test_image_of_another_host(tmp_path: pathlib.Path) -> None:
    """A VICAR file without the Voyager header lines raises WrongHostError naming it."""

    path = write_image(tmp_path / 'X.IMG', header={'LAB02': None})
    with pytest.raises(oops.host.WrongHostError,
                       match=f'^not a Voyager ISS VICAR file: {esc(path)}$'):
        VoyagerISS.from_file(path)


def test_image_header_that_cannot_be_parsed(tmp_path: pathlib.Path) -> None:
    """A file whose VICAR header cannot be parsed raises VicarHostError naming it."""

    path = tmp_path / 'C3450201_RAW.IMG'
    path.write_bytes(b'LBLSIZE=40 FORMAT= \x00' + bytes(40))
    with pytest.raises(oops.host.VicarHostError,
                       match=f'^cannot read the VICAR file {esc(path)}: '):
        VoyagerISS.from_file(path)


def test_image_cut_short(tmp_path: pathlib.Path) -> None:
    """An image file whose data are cut short raises VicarHostError naming it."""

    (label_path, image_path) = write_product(tmp_path)
    image_path.write_bytes(image_path.read_bytes()[:-5])
    with pytest.raises(oops.host.VicarHostError,
                       match=f'^cannot read the VICAR file {esc(image_path)}: '):
        VoyagerISS.from_file(label_path)


def test_real_image_cut_short() -> None:
    """The test copy of C3450201_GEOMED, cut after its header, cannot be read in full."""

    with pytest.raises(oops.host.VicarHostError, match='cannot read the VICAR file'):
        VoyagerISS.from_file(GEOMED_VICAR)


def test_image_of_several_bands(tmp_path: pathlib.Path) -> None:
    """An image of more than one band raises VicarHostError naming the file."""

    with pytest.raises(oops.host.VicarHostError,
                       match=r'^cannot read the VICAR image .*C3450201_RAW.IMG as one '
                             'band: '):
        read_image(tmp_path, bands=2)


def test_array_that_does_not_match_its_label(tmp_path: pathlib.Path) -> None:
    """An array that does not have the shape its label describes raises
    HostFormatError."""

    (label_path, image_path) = write_product(tmp_path, label={'lines': LINES + 1})
    with pytest.raises(oops.host.HostFormatError,
                       match=f'^Voyager ISS image {esc(image_path)} holds a 4x6 array, where '
                             f'its label {esc(label_path)} describes 5x6$'):
        VoyagerISS.from_file(label_path)

##########################################################################################
# from_file(): the PDS3 parse method
##########################################################################################


def test_label_is_parsed_strictly(tmp_path: pathlib.Path) -> None:
    """The default parse method, strict, reads a quoted EDIT_MODE_ID "1:32" as text."""

    obs = read_label(tmp_path, label={'EDIT_MODE_ID': '"1:32"'}, astrometry=True)
    assert obs.dict['EDIT_MODE_ID'] == '1:32'


def test_label_parse_method_can_be_chosen(tmp_path: pathlib.Path) -> None:
    """pds3_options can name another method, which the label is then parsed with."""

    obs = read_label(tmp_path, label={'EDIT_MODE_ID': '"1:32"'}, astrometry=True,
                     pds3_options={'method': 'fast'})
    assert obs.dict['EDIT_MODE_ID'] == dt.time(1, 32)

##########################################################################################
# from_file(): merged metadata
##########################################################################################


def test_header_items() -> None:
    """The header's LAB02, LAB03 and LAB05 give the PDS3 items in a label's forms."""

    assert VoyagerISS._header_items(VicarLabel(HEADER_LINES)) == {
        'INSTRUMENT_HOST_NAME'       : 'VOYAGER 1',
        'INSTRUMENT_ID'              : 'ISSN',
        'PRODUCT_TYPE'               : 'DECOMPRESSED_RAW_IMAGE',
        'IMAGE_NUMBER'               : '34502.01',
        'SPACECRAFT_CLOCK_STOP_COUNT': CLOCK_COUNT,
        'IMAGE_ID'                   : '0469S1-015',
        'IMAGE_TIME'                 : SCET,
        'STOP_TIME'                  : SCET,
        'EXPOSURE_DURATION'          : EXPOSURE,
        'FILTER_NAME'                : 'VIOLET',
        'FILTER_NUMBER'              : '1',
        'TARGET_NAME'                : 'SATURN',
    }


@pytest.mark.parametrize(('tasks', 'product'), [
    (['RESSAR77'], 'CLEANED_IMAGE'),
    (['RESSAR77', 'FICOR77'], 'CALIBRATED_IMAGE'),
    (['RESSAR77', 'FICOR77', 'GEOMA'], 'GEOMETRICALLY_CORRECTED_IMAGE'),
])
def test_header_product_type(tmp_path: pathlib.Path, tasks: list[str],
                             product: str) -> None:
    """The product type is that of the last processing task found."""

    obs = read_image(tmp_path, tasks=tuple(tasks), astrometry=True)
    assert obs.dict['PRODUCT_TYPE'] == product


@pytest.mark.parametrize(('line', 'value', 'missing'), [
    ('LAB02', lab02(scet='**.*** **:**:**'), 'STOP_TIME'),
    ('LAB02', lab02(scet='80.303 24:57:22'), 'STOP_TIME'),
    ('LAB02', lab02(scet='08.303 05:57:22'), 'STOP_TIME'),
    ('LAB02', lab02(scet='81.366 05:57:22'), 'STOP_TIME'),
    ('LAB02', lab02(fds='00000.00'), 'SPACECRAFT_CLOCK_STOP_COUNT'),
    ('LAB02', lab02(picno='0000Z1+000'), 'IMAGE_ID'),
    ('LAB03', lab03(exp='******'), 'EXPOSURE_DURATION'),
    ('LAB05', lab05(''), 'TARGET_NAME'),
], ids=['scet-asterisks', 'hour-24', 'year-outside-the-mission', 'day-366-common-year',
        'fds-fill', 'picno-placeholder', 'exposure-asterisks', 'no-target'])
def test_header_leaves_out_an_invalid_field(line: str, value: str, missing: str) -> None:
    """A field filled with asterisks, a placeholder or an invalid value is missing."""

    assert missing not in VoyagerISS._header_items(VicarLabel(HEADER_LINES
                                                              | {line: value}))


def test_header_keeps_day_366_of_a_leap_year() -> None:
    """Day 366 of 1980 is a valid time."""

    items = VoyagerISS._header_items(VicarLabel({'LAB02': lab02(scet='80.366 01:02:03')}))
    assert items['STOP_TIME'] == dt.datetime(1980, 12, 31, 1, 2, 3)


@pytest.mark.parametrize(('field', 'expected'), [
    ('CH4/JS ', 'CH4_JS'), ('CH4/U', 'CH4_U'), ('CLEAR', 'CLEAR')])
def test_header_filter_is_spelled_as_a_label_spells_it(field: str, expected: str) -> None:
    """The LAB03 filter is unpadded, with "_" for "/"."""

    items = VoyagerISS._header_items(VicarLabel({'LAB03': lab03(filt=f'4({field})')}))
    assert items['FILTER_NAME'] == expected


@pytest.mark.parametrize(('field', 'expected'), [
    ('ENCELADU', 'ENCELADUS'), ('1989N1', 'PROTEUS'), ('S_RINGS', 'S RINGS')])
def test_header_target_is_spelled_as_a_label_spells_it(field: str, expected: str) -> None:
    """A header spelling that no label uses becomes the label's name, and underscores
    become blanks."""

    items = VoyagerISS._header_items(VicarLabel({'LAB05': lab05(field)}))
    assert items['TARGET_NAME'] == expected


def test_header_count_without_leading_zeros() -> None:
    """The clock count is spelled without the leading zeros of the FDS count."""

    items = VoyagerISS._header_items(VicarLabel({'LAB02': lab02(fds='09096.16')}))
    assert items['IMAGE_NUMBER'] == '09096.16'
    assert items['SPACECRAFT_CLOCK_STOP_COUNT'] == '9096:16:001'


def test_merge_label_wins(tmp_path: pathlib.Path) -> None:
    """Where both labels give an item, the PDS3 label's value is used."""

    obs = read_label(tmp_path, label={'FILTER_NAME': '"ORANGE"'})
    assert obs.dict['FILTER_NAME'] == 'ORANGE'
    assert obs.filter == 'ORANGE'


def test_merge_header_fills_a_null(tmp_path: pathlib.Path) -> None:
    """Where the label gives a null text, the header's value is used."""

    obs = read_label(tmp_path, label={'EXPOSURE_DURATION': 'UNK'},
                     image={'header': {'LAB03': lab03(exp='5.0')}})
    assert obs.dict['EXPOSURE_DURATION'] == 0.005


def test_merge_leaves_out_nulls(tmp_path: pathlib.Path) -> None:
    """A null text is left out of dict, also inside an OBJECT of the label, while
    label_dict keeps it."""

    obs = read_label(tmp_path, label={'NOTE': '"UNK"', 'scale': '"N/A"'})
    assert 'NOTE' not in obs.dict
    assert 'REFLECTANCE_SCALING_FACTOR' not in obs.dict['IMAGE']
    assert obs.label_dict['NOTE'] == 'UNK'


def test_merge_keeps_the_other_header_keys(tmp_path: pathlib.Path) -> None:
    """The converted lines appear in dict only as their items; other header keys are
    kept under their own names."""

    obs = read_label(tmp_path)
    assert sorted({'LAB02', 'LAB03', 'LAB04', 'LAB05'} & set(obs.dict)) == ['LAB04']
    assert 'LAB02' in obs.header_dict


def test_header_time_has_the_label_type(tmp_path: pathlib.Path) -> None:
    """A time from the header is a datetime, as the label parser gives a label's."""

    assert read_image(tmp_path, astrometry=True).dict['STOP_TIME'] == SCET

##########################################################################################
# Time and exposure
##########################################################################################


def test_time_ends_at_stop_time(tmp_path: pathlib.Path) -> None:
    """The exposure ends at STOP_TIME and starts one exposure earlier."""

    obs = read_label(tmp_path, label={'STOP_TIME': '1980-10-29T05:57:22.50'},
                     astrometry=True)
    assert obs.time == pytest.approx((tdb('1980-10-29T05:57:22.50') - EXPOSURE,
                                      tdb('1980-10-29T05:57:22.50')), abs=TIME_TOLERANCE)


def test_time_falls_back_to_image_time(tmp_path: pathlib.Path) -> None:
    """Without STOP_TIME, the exposure ends at IMAGE_TIME."""

    obs = read_label(tmp_path, label={'STOP_TIME': None,
                                      'IMAGE_TIME': '1980-10-29T05:57:23.00'},
                     astrometry=True)
    assert obs.time[1] == pytest.approx(tdb('1980-10-29T05:57:23.00'), abs=TIME_TOLERANCE)


def test_time_from_the_clock_count(tmp_path: pathlib.Path) -> None:
    """Without a time, the time is that of the clock stop count in the clock kernel."""

    obs = read_label(tmp_path, label={'STOP_TIME': '"UNK"', 'IMAGE_TIME': 'UNK'},
                     astrometry=True)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=TIME_TOLERANCE)


def test_time_from_the_image_number(tmp_path: pathlib.Path) -> None:
    """Without a clock stop count, the count comes from IMAGE_NUMBER."""

    obs = read_label(tmp_path, label={'STOP_TIME': None, 'IMAGE_TIME': None,
                                      'SPACECRAFT_CLOCK_STOP_COUNT': None},
                     astrometry=True)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=TIME_TOLERANCE)


def test_time_from_the_count_in_the_file_name(tmp_path: pathlib.Path) -> None:
    """Without any count, the count comes from the name of the image file."""

    obs = read_image(tmp_path, header={'LAB02': lab02(fds='00000.00', scet='**')},
                     astrometry=True)
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME), abs=TIME_TOLERANCE)


def test_count_in_march_1980_of_voyager_1() -> None:
    """A row with no time and the count 27830:18:001 takes its time from the clock; in
    1980 March, the count is in the Saturn imaging period of Voyager 1."""

    obs = from_row(STOP_TIME=None, IMAGE_TIME='UNK',
                   SPACECRAFT_CLOCK_STOP_COUNT='27830:18:001')
    assert obs.time[1] == pytest.approx(
        fake_sct2e(-31, fake_scencd(-31, '2/27830:18:001')), abs=TIME_TOLERANCE)


def test_count_in_march_1981_of_voyager_2() -> None:
    """A row with no time and the count 38333:25:001 takes its time from the clock; in
    1981 March, the count is in the Saturn imaging period of Voyager 2."""

    obs = from_row(STOP_TIME=None, IMAGE_TIME='UNK', INSTRUMENT_HOST_NAME='VOYAGER 2',
                   SPACECRAFT_CLOCK_STOP_COUNT='38333:25:001')
    assert obs.time[1] == pytest.approx(
        fake_sct2e(-32, fake_scencd(-32, '2/38333:25:001')), abs=TIME_TOLERANCE)


def test_the_iteration_continues_past_a_row_with_no_time() -> None:
    """The row after one that takes its time from the clock is given too."""

    timeless = ROW | {'STOP_TIME': 'UNK', 'IMAGE_TIME': 'UNK',
                      'SPACECRAFT_CLOCK_STOP_COUNT': '27830:18:001'}
    rows = VoyagerISS.from_index(INDEX, row_dicts=[timeless, ROW])
    assert len(list(rows)) == 2


def test_no_clock_count(tmp_path: pathlib.Path) -> None:
    """An image without any clock count raises HostFormatError naming the file."""

    with pytest.raises(oops.host.HostFormatError,
                       match=r'^Voyager ISS image .*X.IMG has no spacecraft clock '
                             'count$'):
        read_image(tmp_path, 'X', header={'LAB02': lab02(fds='00000.00')},
                   astrometry=True)


def test_clock_count_outside_the_imaging_periods(tmp_path: pathlib.Path) -> None:
    """Without a time, a count in no imaging period raises HostFormatError."""

    with pytest.raises(oops.host.HostFormatError,
                       match=r'has no time, and its clock count 20000:00:001 does not '
                             r'fall in one clock partition within the imaging periods$'):
        read_label(tmp_path, label={'STOP_TIME': None, 'IMAGE_TIME': None,
                                    'SPACECRAFT_CLOCK_STOP_COUNT': '"20000:00:001"'},
                   astrometry=True)


def test_zero_exposure_stays_zero(tmp_path: pathlib.Path) -> None:
    """A label's exposure of 0.0 s gives a zero exposure."""

    obs = read_label(tmp_path, label={'EXPOSURE_DURATION': '0.0 <SECOND>'})
    assert obs.time[0] == obs.time[1]


def test_missing_exposure_is_zero(tmp_path: pathlib.Path) -> None:
    """When no source gives an exposure, it is zero."""

    obs = read_label(tmp_path, label={'EXPOSURE_DURATION': 'UNK'}, astrometry=True)
    assert obs.time == pytest.approx((tdb(STOP_TIME), tdb(STOP_TIME)), abs=TIME_TOLERANCE)

##########################################################################################
# Pointing
##########################################################################################


def test_pointing_from_the_images_own_record(tmp_path: pathlib.Path,
                                             fake_spice: FakeSpice) -> None:
    """The frame is a fixed Cmatrix from the record at the image's own clock count,
    looked up with a tolerance of 20 ticks."""

    obs = read_label(tmp_path, label={'SPACECRAFT_CLOCK_STOP_COUNT': '"34502:02:001"'},
                     astrometry=True)
    assert frame_matrix(obs) == pytest.approx(camera_matrix('34502:02:001'))
    assert fake_spice.ckgp == [(-31100, fake_scencd(-31, '2/34502:02:001'), 20, 'J2000')]


def test_pointing_rotates_the_platform_to_the_camera(tmp_path: pathlib.Path,
                                                     fake_spice: FakeSpice) -> None:
    """The record's platform attitude is rotated to the camera by the frames kernel."""

    read_label(tmp_path, label={'INSTRUMENT_ID': '"ISSW"'}, astrometry=True)
    assert fake_spice.pxform == [('VG1_SCAN_PLATFORM', 'VG1_ISSWA')]


def test_pointing_is_frozen(tmp_path: pathlib.Path) -> None:
    """A frame from the image's own record is frozen."""

    assert read_label(tmp_path, astrometry=True).spice_frame_is_frozen is True


def test_no_record_gives_the_camera_spice_frame(tmp_path: pathlib.Path,
                                                fake_spice: FakeSpice) -> None:
    """With no record at the count, the frame is the camera's SpiceFrame, not frozen."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    obs = read_label(tmp_path, astrometry=True)
    assert fake_spice.spice_frames == ['VG1_ISSNA']
    assert obs.spice_frame_is_frozen is False
    assert frame_matrix(obs) == pytest.approx(rotation(2, SPICE_FRAME_ANGLE))


def test_count_outside_the_imaging_periods_gives_the_spice_frame(
        tmp_path: pathlib.Path, fake_spice: FakeSpice) -> None:
    """A count that falls in no imaging period names no record."""

    obs = read_label(tmp_path, label={'SPACECRAFT_CLOCK_STOP_COUNT': '"20000:00:001"'},
                     astrometry=True)
    assert fake_spice.ckgp == []
    assert fake_spice.spice_frames == ['VG1_ISSNA']
    assert obs.spice_frame_is_frozen is False


def test_frame_override_is_used_as_given(tmp_path: pathlib.Path) -> None:
    """A frame override, such as a Cmatrix, replaces the frame."""

    cmatrix = oops.frame.Cmatrix(oops.Matrix3(rotation(1, 0.2)))
    obs = read_label(tmp_path, frame=cmatrix, astrometry=True)
    assert frame_matrix(obs) == pytest.approx(rotation(1, 0.2))


def test_frame_override_leaves_the_spice_pointing_subfields(
        tmp_path: pathlib.Path) -> None:
    """A Cmatrix override does not change the subfields of the image's SPICE pointing."""

    obs = read_label(tmp_path, frame=oops.frame.Cmatrix(oops.Matrix3.IDENTITY),
                     astrometry=True)
    assert obs.spice_frame_is_frozen is True
    assert obs.ck_object_id == -31100
    assert obs.ck_sclk_id == -31
    assert obs.spice_frame_name == 'VG1_ISSNA'


def test_frame_override_leaves_out_the_cks(tmp_path: pathlib.Path,
                                           fake_spicedb: FakeSpiceDB) -> None:
    """With a frame override, the C kernels are left out of spice_kernels."""

    read_label(tmp_path, frame=oops.frame.Cmatrix(oops.Matrix3.IDENTITY), astrometry=True)
    assert 'CK' not in fake_spicedb.named('used_basenames')[-1][1]['types']


def test_ck_identity(tmp_path: pathlib.Path) -> None:
    """The CK object is the scan platform, and its clock is the spacecraft's."""

    obs = read_label(tmp_path, label={'INSTRUMENT_HOST_NAME': '"VOYAGER 2"'},
                     astrometry=True)
    assert obs.ck_object_id == -32100
    assert obs.ck_sclk_id == -32


@pytest.mark.parametrize('vgr', [1, 2])
def test_ck_clock_matches_spice(tmp_path: pathlib.Path, vgr: int) -> None:
    """SPICE derives the declared clock from the declared CK object."""

    obs = read_label(tmp_path, label={'INSTRUMENT_HOST_NAME': f'"VOYAGER {vgr}"'},
                     astrometry=True)
    assert cspyce.ckmeta(obs.ck_object_id, 'SCLK') == obs.ck_sclk_id

##########################################################################################
# Subfields
##########################################################################################


@pytest.mark.parametrize(('host', 'instrument', 'name', 'frame_id'), [
    ('VOYAGER 1', 'ISSN', 'VG1_ISSNA', -31101),
    ('VOYAGER 2', 'ISSW', 'VG2_ISSWA', -32102),
])
def test_spice_frame_subfields(tmp_path: pathlib.Path, host: str, instrument: str,
                               name: str, frame_id: int) -> None:
    """The SPICE frame of each camera is named, with its ID and the identity matrix."""

    obs = read_label(tmp_path, label={'INSTRUMENT_HOST_NAME': f'"{host}"',
                                      'INSTRUMENT_ID': f'"{instrument}"'},
                     astrometry=True)
    assert obs.spice_frame_name == name
    assert obs.spice_frame_id == frame_id
    assert obs.spice_to_frame == oops.Matrix3.IDENTITY


def test_subfields(tmp_path: pathlib.Path, fake_spicedb: FakeSpiceDB) -> None:
    """The host, instrument, camera, filter, planet, spacecraft and kernels."""

    obs = read_label(tmp_path, astrometry=True)
    assert obs.host == 'Voyager'
    assert obs.instrument == 'ISS'
    assert obs.detector == 'NAC'
    assert obs.filter == 'VIOLET'
    assert obs.planet == 'SATURN'
    assert obs.spacecraft == 1
    assert obs.spice_kernels == fake_spicedb.used


def test_clock_count_subfields(tmp_path: pathlib.Path) -> None:
    """The clock counts are given as the label spells them."""

    obs = read_label(tmp_path, astrometry=True)
    assert obs.spacecraft_clock_start_count == '34502:00:768'
    assert obs.spacecraft_clock_stop_count == CLOCK_COUNT


def test_subfields_no_source_gives_are_absent(tmp_path: pathlib.Path) -> None:
    """The filter, the start count and the target are absent when no source gives
    them."""

    obs = read_label(tmp_path, label={'FILTER_NAME': None, 'TARGET_NAME': None,
                                      'SPACECRAFT_CLOCK_START_COUNT': None},
                     astrometry=True)
    assert sorted({'filter', 'spacecraft_clock_start_count', 'target'}
                  & set(obs.subfields)) == []


def test_spacecraft_from_the_header(tmp_path: pathlib.Path) -> None:
    """An image file alone tells Voyager 2 from its LAB02."""

    assert read_image(tmp_path, header={'LAB02': lab02(vgr='2')},
                      astrometry=True).spacecraft == 2


def test_planet_from_the_time(tmp_path: pathlib.Path) -> None:
    """Without MISSION_PHASE_NAME, the planet is that of the encounter in progress."""

    obs = read_label(tmp_path, label={'MISSION_PHASE_NAME': None,
                                      'STOP_TIME': '1979-03-05T12:00:00.00'},
                     astrometry=True)
    assert obs.planet == 'JUPITER'


def test_return_all_planets(tmp_path: pathlib.Path, fake_spicedb: FakeSpiceDB) -> None:
    """return_all_planets asks for the kernels of every planet."""

    read_label(tmp_path, return_all_planets=True, astrometry=True)
    assert fake_spicedb.named('used_basenames')[-1][1]['bodies'][:4] == [1, 199, 2, 299]


@pytest.mark.parametrize(('product', 'pixels'), [
    ('GEOMETRICALLY_CORRECTED_IMAGE', 1000), ('CALIBRATED_IMAGE', 800)])
def test_fov_follows_the_product_type(tmp_path: pathlib.Path, product: str,
                                      pixels: int) -> None:
    """A GEOMED product is 1000x1000 and any other 800x800, also for astrometry."""

    obs = read_label(tmp_path, label={'PRODUCT_TYPE': product}, astrometry=True)
    assert tuple(obs.fov.uv_shape.vals) == (pixels, pixels)


def test_fov_of_a_geomed_image_file(tmp_path: pathlib.Path) -> None:
    """An image file alone is GEOMED if its history has the GEOMA task."""

    obs = read_image(tmp_path, tasks=('GEOMA',), astrometry=True)
    assert tuple(obs.fov.uv_shape.vals) == (1000, 1000)

##########################################################################################
# Target and light source
##########################################################################################


@pytest.mark.parametrize(('label_target', 'target'), [
    ('TITAN', 'TITAN'),
    ('S RINGS', 'SATURN_RING_PLANE'),
    ('CAL LAMPS', 'NONE'),
    ('SYSTEM', 'SATURN'),
])
def test_target(tmp_path: pathlib.Path, label_target: str, target: str) -> None:
    """The target is TARGET_NAME, with the names that are not oops bodies repaired."""

    obs = read_label(tmp_path, label={'TARGET_NAME': f'"{label_target}"'},
                     astrometry=True)
    assert obs.target == target
    assert obs.lightsource == 'SUN'


def test_star_target(tmp_path: pathlib.Path) -> None:
    """A star target is the light source, and nothing is tracked."""

    obs = read_label(tmp_path, label={'TARGET_NAME': '"VEGA"'}, astrometry=True)
    assert obs.target == 'NONE'
    assert obs.lightsource.name == 'alf Lyr'


def test_header_target_spelling(tmp_path: pathlib.Path) -> None:
    """An image file alone names the target with the label's spelling."""

    obs = read_image(tmp_path, header={'LAB05': lab05('ENCELADU')}, astrometry=True)
    assert obs.target == 'ENCELADUS'

##########################################################################################
# I/F
##########################################################################################


def test_if_scale_from_the_label(tmp_path: pathlib.Path) -> None:
    """With the label read, if_scale is its REFLECTANCE_SCALING_FACTOR, and the I/F
    calibration uses it."""

    obs = read_label(tmp_path, label={'scale': '0.002'}, image={'if_line': IF_LINE})
    assert obs.if_scale == 0.002
    assert obs.calibrations[0].factor == 0.002


def test_if_scale_falls_to_the_header(tmp_path: pathlib.Path) -> None:
    """Where the label's factor is null, the header's factor is used, corrected."""

    obs = read_label(tmp_path, label={'scale': 'UNK'}, image={'if_line': IF_LINE})
    assert obs.if_scale == pytest.approx(1.25e-4 * 3.345)


@pytest.mark.parametrize(('lab02_line', 'filt', 'factor'), [
    (lab02(scet='79.064 12:00:00'), '4(GREEN)', 1.),
    (lab02(), '4(GREEN)', 3.345),
    (lab02(scet='79.064 12:00:00'), '5(SODIUM)', 40.),
    (lab02(), '7(CH4/U)', 3.345 * 12.5),
    (lab02(vgr='2'), '5(SODIUM)', 1.),
], ids=['v1-jupiter', 'v1-saturn', 'v1-sodium', 'v1-saturn-ch4-u', 'v2'])
def test_if_scale_from_the_header(tmp_path: pathlib.Path, lab02_line: str, filt: str,
                                  factor: float) -> None:
    """From an image file alone, if_scale is the header's factor / 10000, with the
    documented Voyager 1 corrections."""

    obs = read_image(tmp_path, header={'LAB02': lab02_line, 'LAB03': lab03(filt=filt)},
                     if_line=IF_LINE, astrometry=True)
    assert obs.if_scale == pytest.approx(1.25e-4 * factor)


def test_if_scale_not_a_number(tmp_path: pathlib.Path) -> None:
    """A header factor that is not a number gives no if_scale."""

    obs = read_image(tmp_path, if_line=IF_LINE.replace('1.25000', 'N/A'),
                     astrometry=True)
    assert 'if_scale' not in obs.subfields


def test_no_if_scale(tmp_path: pathlib.Path) -> None:
    """Without a factor from any source, if_scale is absent and there is no
    calibration."""

    obs = read_image(tmp_path, astrometry=True)
    assert 'if_scale' not in obs.subfields
    assert obs.calibrations == []


def test_calibrations_override(tmp_path: pathlib.Path) -> None:
    """The calibrations override replaces the I/F calibration."""

    calib = oops.calibration.FlatCalib('DN', 2.)
    obs = read_label(tmp_path, calibrations=[calib], astrometry=True)
    assert obs.calibrations == [calib]

##########################################################################################
# Overrides
##########################################################################################


def test_overrides(tmp_path: pathlib.Path) -> None:
    """The target, path and cadence overrides replace the host's."""

    path = oops.path.FixedPath((1., 2., 3.), oops.Path.SSB, oops.Frame.J2000)
    cadence = oops.cadence.SnapCadence(100., 5.)
    obs = read_label(tmp_path, target='TITAN', path=path, cadence=cadence,
                     astrometry=True)
    assert obs.target == 'TITAN'
    assert obs.path == oops.Path.as_waypoint(path)
    assert obs.time == (100., 105.)


def test_navigation(tmp_path: pathlib.Path) -> None:
    """navigation wraps the frame in a Navigation frame."""

    obs = read_label(tmp_path, navigation=(1.e-3, 0.), astrometry=True)
    assert isinstance(oops.Frame.as_frame(obs.frame), oops.frame.Navigation)


@pytest.mark.usefixtures('body_path')
def test_tracker(tmp_path: pathlib.Path) -> None:
    """tracker wraps the frame in a TrackerFrame."""

    obs = read_label(tmp_path, target='TITAN', tracker='midtime', astrometry=True)
    assert isinstance(oops.Frame.as_frame(obs.frame), oops.frame.TrackerFrame)

##########################################################################################
# from_index()
##########################################################################################


def test_from_index_returns_an_iterator() -> None:
    """from_index() returns an iterator, not a list."""

    rows = VoyagerISS.from_index(INDEX)
    assert iter(rows) is rows


def test_from_index_gives_every_row() -> None:
    """Every row of the index gives one Snapshot, in index order."""

    names = [obs.basename for obs in VoyagerISS.from_index(INDEX)]
    assert len(names) == INDEX_ROWS
    assert names[0] == 'C3450001_RAW.LBL'


def test_from_index_row_paths() -> None:
    """A row names its file by the relative path the index gives, and nothing else."""

    obs = next(VoyagerISS.from_index(INDEX))
    assert obs.filepath == 'VGISS_6109/DATA/C34500XX/C3450001_RAW.LBL'
    assert obs.basename == 'C3450001_RAW.LBL'
    assert sorted({'image_url', 'image_abspath', 'label_url'} & set(obs.subfields)) == []


def test_from_index_dict_is_the_row() -> None:
    """dict is the merged row as the table reader gives it, with its masks."""

    obs = next(VoyagerISS.from_index(INDEX))
    assert obs.dict['STOP_TIME'] == '1980-10-29T04:21:22.00'
    assert not obs.dict['EXPOSURE_DURATION_mask']


def test_from_index_merges_the_default_supplement() -> None:
    """The default supplement, named after the raw image index, gives STOP_TIME."""

    obs = next(VoyagerISS.from_index(INDEX))
    assert obs.time[1] == pytest.approx(tdb('1980-10-29T04:21:22.00'), abs=TIME_TOLERANCE)


def test_from_index_without_the_default_supplement(tmp_path: pathlib.Path) -> None:
    """If the default supplement does not exist, the index is read alone."""

    for suffix in ('lbl', 'tab'):
        shutil.copy(INDEX.with_suffix(f'.{suffix}'), tmp_path)
    obs = next(VoyagerISS.from_index(tmp_path / INDEX.name))
    assert 'STOP_TIME' not in obs.dict


def test_from_index_with_no_supplement() -> None:
    """supplement=None reads the index alone."""

    obs = next(VoyagerISS.from_index(INDEX, supplement=None))
    assert 'STOP_TIME' not in obs.dict


def test_from_index_with_a_named_supplement(tmp_path: pathlib.Path) -> None:
    """A supplement named in any directory is read."""

    for suffix in ('lbl', 'tab'):
        shutil.copy(SUPPLEMENT.with_suffix(f'.{suffix}'), tmp_path / f'supp.{suffix}')
    label = tmp_path / 'supp.lbl'
    label.write_text(label.read_text().replace(SUPPLEMENT.with_suffix('.tab').name,
                                               'supp.tab'))
    obs = next(VoyagerISS.from_index(INDEX, supplement=label))
    assert 'STOP_TIME' in obs.dict


def test_from_index_with_a_missing_named_supplement(tmp_path: pathlib.Path) -> None:
    """A named supplement that does not exist raises FileNotFoundError."""

    with pytest.raises(FileNotFoundError, match=r'nope\.lbl'):
        VoyagerISS.from_index(INDEX, supplement=tmp_path / 'nope.lbl')


@pytest.mark.parametrize(('name', 'expected'), [
    ('VGISS_6109_raw_image_index.lbl', 'VGISS_6109_supplemental_index.lbl'),
    ('VGISS_6109_RAW_IMAGE_INDEX.LBL', 'VGISS_6109_SUPPLEMENTAL_INDEX.LBL'),
    ('VGISS_6109_index.lbl', None),
])
def test_supplement_path(name: str, expected: str | None) -> None:
    """The supplement replaces "_raw_image_index" with "_supplemental_index"."""

    path = VoyagerISS._supplement_path(FCPath('/data') / name)
    assert (None if path is None else path.name) == expected


def test_from_index_parses_the_labels_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The index labels are parsed with strict unless pds3_options names another."""

    calls: list[dict[str, Any]] = []

    def index_rows(cls: type, /, filepath: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return iter([])

    monkeypatch.setattr(VoyagerISS, '_index_rows', classmethod(index_rows))
    list(VoyagerISS.from_index(INDEX, pds3_options={'fmt_dirs': []}))
    assert calls[0]['pds3_options'] == {'method': 'strict', 'fmt_dirs': []}


def test_from_index_filter() -> None:
    """A filter leaves out the rows that do not pass it."""

    names = [obs.basename for obs in VoyagerISS.from_index(
             INDEX, filter={'FILE_SPECIFICATION_NAME': 'DATA/C34502XX/C3450201_RAW.LBL'})]
    assert names == ['C3450201_RAW.LBL']


def test_from_index_unknown_keyword() -> None:
    """from_index takes no pixel offset, or any other keyword not listed."""

    with pytest.raises(TypeError, match="unexpected keyword argument 'offset'"):
        VoyagerISS.from_index(INDEX, offset=(1., 2.))


def test_oops_host_from_index_dispatches() -> None:
    """oops.Host.from_index identifies the host from the first row."""

    assert next(oops.Host.from_index(INDEX)).instrument == 'ISS'


def test_row_with_a_masked_exposure() -> None:
    """A masked exposure, the declared null -99.9999, gives zero; dict keeps it."""

    obs = from_row(EXPOSURE_DURATION=np.float64(-99.9999),
                   EXPOSURE_DURATION_mask=np.True_)
    assert obs.time[0] == obs.time[1]
    assert obs.dict['EXPOSURE_DURATION'] == -99.9999


def test_row_with_a_null_time() -> None:
    """A row whose times are null texts takes its time from the clock count."""

    obs = from_row(STOP_TIME=None, IMAGE_TIME='UNK',
                   SPACECRAFT_CLOCK_STOP_COUNT='34502:02:001')
    assert obs.time[1] == pytest.approx(tdb(STOP_TIME) + 48., abs=TIME_TOLERANCE)


def test_row_camera_from_the_instrument_name() -> None:
    """A row names the camera in INSTRUMENT_NAME."""

    assert from_row(INSTRUMENT_NAME='WIDE ANGLE CAMERA').detector == 'WAC'


def test_row_count_from_the_image_number() -> None:
    """Without the supplement's count, the count comes from IMAGE_NUMBER."""

    obs = from_row(SPACECRAFT_CLOCK_STOP_COUNT=None, IMAGE_NUMBER=np.float64(34502.02))
    assert frame_matrix(obs) == pytest.approx(camera_matrix('34502:02:001'))


def test_row_without_a_record(fake_spice: FakeSpice) -> None:
    """A row without a C-kernel record is still given, with the camera's SpiceFrame."""

    fake_spice.missing.add(fake_scencd(-31, f'2/{CLOCK_COUNT}'))
    assert from_row().spice_frame_is_frozen is False


def test_row_subfields() -> None:
    """A row gives the host's subfields, without the label and header dictionaries or
    an if_scale."""

    obs = from_row()
    assert obs.detector == 'NAC'
    assert obs.filter == 'VIOLET'
    assert obs.planet == 'SATURN'
    assert obs.spacecraft == 1
    assert obs.target == 'SATURN'
    assert obs.spacecraft_clock_stop_count == CLOCK_COUNT
    assert obs.ck_object_id == -31100
    assert sorted({'label_dict', 'header_dict', 'if_scale', 'data'}
                  & set(obs.subfields)) == []


def test_row_overrides() -> None:
    """The overrides apply to every row."""

    assert from_row(target='TITAN').target == 'TITAN'


def test_row_select() -> None:
    """select=None gives each row's Snapshot."""

    assert isinstance(from_row(select=None), oops.observation.Snapshot)


##########################################################################################
