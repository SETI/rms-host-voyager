##########################################################################################
# host_voyager/oops/iss.py
##########################################################################################

import datetime as dt
import re

import julian
import cspyce
import numpy as np

from filecache import FCPath
from pdsparser import Pds3Label
from vicar     import VicarError, VicarImage

import oops
from ._voyager import _Voyager

__all__ = ['VoyagerISS']

# The scan platform C kernels hold one pointing record per image, stamped at the image's
# own spacecraft clock stop count, which can be whole FDS minutes (48 s each) after the
# true close of the shutter, STOP_TIME. The record is therefore found by the count, never
# by the time; see docs/pointing.rst. The tolerance is in clock ticks of 60 ms.
_TICK_TOLERANCE = 20

# The short error code at the start of a SPICE error message, as in
#   SPICE(CKINSUFFDATA) -- sigerr -- insufficient C kernel data to evaluate ...
_SPICE_ERROR_CODE = re.compile(r'SPICE\([A-Z0-9_]+\)')

# NAIF IDs: the scan platform is -31100 or -32100; its frame has the same ID, and the
# camera frames VG{n}_ISSNA and VG{n}_ISSWA are the platform ID minus 1 and minus 2
_SCAN_PLATFORM_ID = {1: -31100, 2: -32100}
_CAMERA_FRAME_OFFSET = {'NAC': 1, 'WAC': 2}

# The value of PRODUCT_TYPE for a geometrically corrected image, which is 1000x1000 and
# free of the camera's distortion; every other product is a raw 800x800 vidicon image
_GEOMED = 'GEOMETRICALLY_CORRECTED_IMAGE'
_RAW_PIXELS = 800
_GEOMED_PIXELS = 1000

# Half of the field of view in radians, keyed by (camera, geometrically corrected)
_HALF_FOV = {
    ('NAC', False): 0.003700098,
    ('NAC', True) : np.radians(0.4493) / 2.,
    ('WAC', False): 0.02765,
    ('WAC', True) : np.radians(3.364) / 2.,
}

# The VICAR tasks that produce each product; the last one found names the product
_VICAR_PRODUCT_TASKS = [
    ('RESSAR77', 'CLEANED_IMAGE'),
    ('FICOR77' , 'CALIBRATED_IMAGE'),
    ('GEOMA'   , _GEOMED),
]

# The methods of the PDS3 label parser, in the order in which they are tried
_PDS3_METHODS = ('fast', 'strict', 'loose')

# The columns that identify the same image in a raw image index and its supplement
_INDEX_KEYS = ['VOLUME_NAME', 'FILE_SPECIFICATION_NAME']

# Target name as found in a PDS label or index -> the oops body name
_TARGET_NAME_REPAIRS = {
    'J RINGS'  : 'JUPITER_RING_PLANE',
    'S RINGS'  : 'SATURN_RING_PLANE',
    'U RINGS'  : 'URANUS_RING_PLANE',
    'N RINGS'  : 'NEPTUNE_RING_PLANE',
    'CAL LAMPS': 'NONE',
    'DARK'     : 'NONE',
    'ORION'    : 'NONE',
    'PLAQUE'   : 'NONE',
    'SCORPIUS' : 'NONE',
    'SKY'      : 'NONE',
    'STAR'     : 'NONE',
    'TAURUS'   : 'NONE',
}

# Targets that stand for the planet of the encounter
_PLANET_TARGETS = {'SYSTEM', 'UNK SAT'}

# Star targets -> the name under which oops.lightsource.star_lookup() finds the star
_TARGET_STARS = {
    'ARCTURUS' : 'ARCTURUS',
    'BETA CMA' : 'bet CMa',
    'PLEIADES' : 'PLEIADES',
    'SIGMA SGR': 'sig Sgr',
    'THETA CAR': 'tet Car',
    'VEGA'     : 'VEGA',
}

# Lines of the VICAR header of a Voyager image, as in
#   LAB02='VGR-1   FDS 34502.01   PICNO 0469S1-015   SCET 80.303 05:57:22         C'
#   LAB03='NA CAMERA  EXP    1920.0 MSEC FILT 1(VIOLET)  LO GAIN  SCAN RATE  5:1  C'
# Each field is read on its own, because the ground system filled a field it did not know
# with asterisks ("SCET **.*** **:**:**", "PICNO **********"), placeholders ("PICNO
# 0000Z1+000", "FDS 00000.00") or garbage bytes; such a field does not match and the item
# is missing from the header.
_LAB02_SPACECRAFT = re.compile(r'^\s*VGR-(?P<vgr>[12])\s')
_LAB02_FDS        = re.compile(r'\sFDS\s+(?P<hours>\d{5})\.(?P<minutes>[0-5]\d)\s')
_LAB02_PICNO      = re.compile(r'\sPICNO\s+(?P<picno>(?!0000)\d{4}[A-Z]\d[+-]\d{3})\s')
_LAB02_SCET       = re.compile(r'\sSCET\s+(?P<year>\d\d)\.(?P<doy>\d{3})\s+'
                               r'(?P<hms>\d\d:\d\d:\d\d)\s')
_LAB03_CAMERA     = re.compile(r'^\s*(?P<camera>[NW])A CAMERA\s')
_LAB03_EXPOSURE   = re.compile(r'\sEXP\s+(?P<exp>\d+\.?\d*)\s+MSEC\s')
_LAB03_FILTER     = re.compile(r'\sFILT\s+(?P<number>\d)\((?P<name>[^)]*\S[^)]*)\)')
_MSEC = 1000.

# The target name occupies these columns of LAB05, with underscores for blanks
_LAB05_TARGET = slice(31, 43)

# The line of a calibrated product's VICAR header that gives its I/F scale, as in
#   LABEL3='FOR (I/F)*10000., MULTIPLY DN VALUE BY               1.00000'
_IF_LINE = re.compile(r'FOR \(I/F\)\*10000\., MULTIPLY DN VALUE BY\s+(?P<scale>\S+)')
_IF_LINE_UNITS = 10000.

# The PDS3 null constants that a label or index gives for an unknown value, and the
# NULL_CONSTANT of the numeric columns of a VGISS raw image index. Either counts as
# missing.
_NULL_STRINGS = {'UNK', 'N/A', 'NULL'}
_NULL_NUMBER = -99.9999

# The two-digit years of a SCET: VGISS images date from 1978 to 1989. A garbled LAB02
# such as "SCET 08.272 77:05:04" is rejected by its year or its time of day.
_SCET_YEARS = range(77, 100)
_SCET_CENTURY = 1900


def _filter_name(text):
    """A filter name as a PDS3 label spells it.

    The LAB03 line of a VICAR header pads the name with blanks and writes "CH4/JS" and
    "CH4/U" for the label's "CH4_JS" and "CH4_U"; an index gives each name as a NumPy
    string.

    Parameters:
        text (str): The name as a label, an index row or a LAB03 line gives it.

    Returns:
        str: The name, such as "CLEAR" or "CH4_JS".
    """

    return str(text).strip().replace('/', '_')


def _is_null(value):
    """True if a label, index or header value is a null constant, meaning unknown."""

    if isinstance(value, str):
        return value.strip() in _NULL_STRINGS
    return isinstance(value, float) and value == _NULL_NUMBER


def _without_nulls(dict_):
    """A copy of a dictionary without the keys whose values are null constants."""

    return {key: value for (key, value) in dict_.items() if not _is_null(value)}


def _scet(lab02):
    """The SCET of a LAB02 line, the close of the shutter, as a datetime.

    Parameters:
        lab02 (str): The LAB02 line of a VICAR header.

    Returns:
        datetime.datetime | None: The time; None if the SCET is filled with asterisks or
        is not a valid time in the years of the mission.
    """

    match = _LAB02_SCET.search(lab02)
    if match is None or int(match['year']) not in _SCET_YEARS:
        return None
    year = _SCET_CENTURY + int(match['year'])
    try:
        return dt.datetime.strptime(f'{year}.{match["doy"]} {match["hms"]}',
                                    '%Y.%j %H:%M:%S')
    except ValueError:      # an hour of 24 or more, a day of 367 or more, ...
        return None

##########################################################################################
# Resolution rules
##########################################################################################
# A label input reads two sources, the PDS3 label and the VICAR header of the image
# file. Where both give an item and the two values differ, the rule of that item below
# chooses one. A header value that is not valid, such as a SCET of asterisks or an hour
# of 24, is missing from the header and never reaches a rule. The counts are from a
# survey of the VICAR headers of all 76,652 VGISS raw images against the volume indexes,
# whose values are the labels' (2,495 of 2,496 labels read agree), and of the labels and
# headers of about 3,000 products read directly, including every header that the
# comparison could not parse. One issue in this repository records every difference
# for a final decision (#14). Absent other evidence, the label wins, because it is
# the archive's curated description of the product.


def _prefer_label(label_value, header_value):
    """The label's value."""

    return label_value


def _prefer_untruncated_exposure(label_value, header_value):
    """The header's exposure if the label's is that exposure truncated to 0.01 s; else the
    label's.

    Parameters:
        label_value (float): EXPOSURE_DURATION from the label, in seconds.
        header_value (float): The LAB03 exposure, converted to seconds.

    Returns:
        float: The chosen exposure in seconds.
    """

    # The header gives the exposure to 0.1 ms; work in those units to avoid round-off
    tenths_ms = round(header_value * _TENTHS_MS_PER_SECOND)
    truncated = (tenths_ms // _TENTHS_MS_PER_LABEL_STEP * _TENTHS_MS_PER_LABEL_STEP
                 / _TENTHS_MS_PER_SECOND)
    if abs(truncated - label_value) < _EXPOSURE_TOLERANCE:
        return header_value
    return label_value


# A label's EXPOSURE_DURATION is sometimes the exposure truncated to 0.01 s, which is 100
# units of the 0.1 ms to which the header gives it
_TENTHS_MS_PER_SECOND = 10000
_TENTHS_MS_PER_LABEL_STEP = 100
_EXPOSURE_TOLERANCE = 1.e-9

_RESOLUTION = {
    # 2 differ, C1630038 and C1630041, whose garbled headers say VGR-2 (#14)
    'INSTRUMENT_HOST_NAME'       : _prefer_label,

    # 5 differ, all narrow angle in the label and wide angle in the header, such as
    # C1570449; the two choices point 60-75 pixels apart (#14)
    'INSTRUMENT_ID'              : _prefer_label,

    # No difference among the products read (#14)
    'PRODUCT_TYPE'               : _prefer_label,

    # The FDS count: 11 headers give the fill value FDS 00000.00 or 0.00, and 63 give
    # another count, 62 of them one or two FDS minutes later than the label's. For
    # every one checked, the C kernels hold no record at the header's count and the
    # image's pointing at the label's (#14)
    'IMAGE_NUMBER'               : _prefer_label,
    'SPACECRAFT_CLOCK_STOP_COUNT': _prefer_label,

    # The picture number: of the products read, 13 headers give the placeholder
    # "0000Z1+000", missing from the header, where the label gives the real one, and none
    # gives another real one (#14)
    'IMAGE_ID'                   : _prefer_label,

    # The header's SCET is to the second and the label's time to 1/100 s. 3 differ by
    # more: C1941239 and C2066403 by one FDS minute (48 s) and C4375222 by 60 s. A
    # further 93 SCETs are asterisks or invalid, so missing from the header (#14)
    'IMAGE_TIME'                 : _prefer_label,
    'STOP_TIME'                  : _prefer_label,

    # About 910 differ. For about 800, all in VGISS_5xxx, the label's value is the
    # header's truncated to 0.01 s, such as 0.04 s for 45 ms (C2037439), so the header's
    # is used; 112 differ by more than 0.1 s, mostly long exposures of 96 or 192 s for
    # which the header gives 5 ms (#14)
    'EXPOSURE_DURATION'          : _prefer_untruncated_exposure,

    # 5 differ, such as C1984746, ORANGE in the label and VIOLET in the header (#14)
    'FILTER_NAME'                : _prefer_label,
    'FILTER_NUMBER'              : _prefer_label,

    # 1,946 differ: the header's target field drops the blank of 677 ("CALLAMPS"), cuts
    # 100 to 8 characters ("ENCELADU"), and names another target for 1,169, such as
    # SATURN for the label's S RINGS or STAR for VEGA (#14)
    'TARGET_NAME'                : _prefer_label,

    # Computed from the SCET for a header; no difference where both give it (#14)
    'MISSION_PHASE_NAME'         : _prefer_label,
}


class _UnusableImageError(oops.host.HostError):
    """An image whose pointing is unavailable.

    :meth:`VoyagerISS.from_file` raises it as an :class:`oops.host.HostError`, and
    :meth:`VoyagerISS.from_index` leaves the image out.
    """


class _MissingItemError(oops.host.HostFormatError):
    """An image for which no source read gives an item that the Snapshot needs.

    :meth:`VoyagerISS.from_file` raises it as an :class:`oops.host.HostFormatError`, and
    :meth:`VoyagerISS.from_index` leaves the image out.
    """


class VoyagerISS(oops.Host):
    """The Voyager ISS host: Snapshot constructors for Imaging Science Subsystem images.

    The host covers the narrow and wide angle cameras of Voyager 1 and Voyager 2. The
    time of each image comes from STOP_TIME, the close of the shutter. Its default frame
    is a fixed C-matrix built from the image's own record in the scan platform C kernels,
    which is found by the image's spacecraft clock stop count rather than by its time,
    because the records are stamped at the count and the count can fall minutes after
    the shutter closed.
    """

    NAME = 'Voyager ISS'

    _FOVS = {}
    _initialized = False

    @staticmethod
    def from_file(fileinfo, *, return_all_planets=False, pds3_options=None,
                  astrometry=False, target=None, lightsource=None, path=None, frame=None,
                  cadence=None, fov=None, calibrations=None, timeshift=None,
                  navigation=None, tracker=None, parallel=None, **kwargs):
        """A Snapshot object based on a given Voyager ISS image file or its label.

        Parameters:
            fileinfo (str | pathlib.Path | FCPath | Pds3Label | VicarLabel | VicarImage):
                The path to the PDS3 label or the VICAR image file, or its parsed content
                as a Pds3Label, VicarLabel, or VicarImage. Given the image file, only
                that file is read: its VICAR header supplies the metadata and, unless
                `astrometry` is True, the file supplies the data. Given the label, the
                label is read and, unless `astrometry` is True, so is the image file it
                names, which must then exist; the metadata are merged from the label
                and the image's VICAR header. With `astrometry`, only the label is read
                and the image file need not exist. The data are always read where the
                image file's VICAR header places them.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser. If the
                method, by default "fast", cannot parse a label, the label is parsed
                again with each later method in the order "fast", "strict", "loose".
            astrometry (bool, optional): True to specify that the returned Observation
                will only be used for timing and astrometry. In this case, no data array
                is read and returned in the Observation.
            target (str | oops.Body, optional): Override for the default target Body of
                the observation. Use "NONE" for inertial pointing, indicating that no
                Solar System body was tracked.
            lightsource (oops.LightSource, optional): Override for the default LightSource
                of the observation.
            path (str | oops.Path, optional): Override for the Path of the observer.
            frame (str | oops.Frame, optional): Override for the Frame of the observing
                instrument.
            cadence (oops.Cadence, optional): Override for the Cadence of the observation.
            fov (oops.FOV, optional): Override for the default FOV of the observing
                instrument.
            calibrations (oops.Calibration | list[oops.Calibration], optional): Override
                for the calibration or list of calibrations.
            timeshift (tuple[float, str], optional): Assign a Fittable time shift to one
                or more attributes of the Observation. The first item is the initial time
                shift in seconds. The second item is a string indicating which attributes
                are to be time-shifted: "T" for the start/stop time, "P" for the path,
                and/or "F" for the frame. If multiple letters are given, the time shifts
                are coupled to whichever attribute is listed first.
            navigation (tuple[float, ...], optional): Two or three rotation angles in
                radians to apply to the frame in order to align the calculated geometry
                with the observed geometry. In this case, a fittable Navigation frame
                wraps the default frame. Specify three angles to include a rotation about
                the optic axis or two for a pointing offset without rotation. Use (0,0) or
                (0,0,0) as the input if you have no starting guess. To start from an
                offset in pixels, convert it with the FOV's
                :meth:`~oops.FOV.offset_angles_from_duv`.
            tracker (str, optional): Assign a TrackerFrame to the Observation, to ensure
                that the target body remains at a fixed position within the FOV. Use one
                of "start", "midtime", and "end", indicating the time within the
                Observation at which the target body's calculated position within the FOV
                is accurate.
            parallel (Observation, optional): A parallel Observation (same origin and
                time, different frame and FOV) relative to which this Observation has a
                fixed offset. Note that no more than one of `parallel` and `tracker` can
                be specified.
            **kwargs: Additional keyword arguments; they are accepted and ignored. This
                includes `select`.

        Returns:
            Snapshot: The observation, with subfields `dict`, `host`, `instrument`,
            `detector`, `planet`, `spacecraft`, `spice_kernels`, `spice_to_frame`,
            `spice_frame_name` and `spice_frame_id` inserted; `data` unless `astrometry`
            is True; and each of these when its source gives it: `label_dict`,
            `header_dict`, `filter`, `label_target`, `if_scale`,
            `spacecraft_clock_start_count`, `spacecraft_clock_stop_count`, `filepath`,
            `basename`, `abspath` and `image_url`. A subfield whose value no source read
            gives is absent, never None.

            - `dict` is the metadata merged from every source read. The items that both
              the label and the VICAR header give are keyed by their PDS3 names
              (INSTRUMENT_HOST_NAME, INSTRUMENT_ID, PRODUCT_TYPE, IMAGE_NUMBER,
              SPACECRAFT_CLOCK_STOP_COUNT, IMAGE_ID, IMAGE_TIME, STOP_TIME,
              EXPOSURE_DURATION, FILTER_NAME, FILTER_NUMBER, TARGET_NAME and
              MISSION_PHASE_NAME), with the header's values converted to the label's
              forms. Where only one source gives an item, its value is used; where both
              give it and they differ, the label's value is used, except that the
              header's exposure is used where the label's is the same exposure
              truncated to 0.01 s. Every other key of each source is kept
              under its own name, such as LAB02 and LABEL3 of the header. A key whose
              value is a PDS3 null constant ("UNK", "N/A" or "NULL") is left out.
            - `label_dict` is the PDS3 label as a dictionary, as it is; present only
              when the label was read.
            - `header_dict` is the VICAR header as a dictionary keyed by parameter name,
              as it is; present only when the image file was read.
            - `filter` is the FILTER_NAME as a PDS3 label spells it, such as "CLEAR" or
              "CH4_JS".
            - `label_target` is the TARGET_NAME.
            - `spacecraft` is the spacecraft number, 1 or 2, from INSTRUMENT_HOST_NAME
              (or SPACECRAFT_NAME).
            - `if_scale` is the factor that converts the data of a calibrated product
              (CALIB or GEOMED) to I/F, from the line "FOR (I/F)*10000., MULTIPLY DN
              VALUE BY x" of its VICAR header, so x / 10000. It is absent for a product
              whose header has no such line (RAW or CLEANED) and whenever the header
              was not read. The PDS3 label's REFLECTANCE_SCALING_FACTOR is not always
              the same: for Voyager 1 images of Saturn it is 3.345 times larger.
            - `spacecraft_clock_start_count` and `spacecraft_clock_stop_count` are the
              SPACECRAFT_CLOCK_START_COUNT and SPACECRAFT_CLOCK_STOP_COUNT as the label
              spells them, such as "34502:01:001". A VICAR header gives the stop count
              alone, as its FDS image number "hhhhh.mm", which is spelled the same way.
            - `filepath`, `basename`, `abspath` and `image_url` name the image file,
              even when the PDS3 label is given; with `astrometry`, as the label names
              it, whether or not the file exists. They are absent if the parsed content
              given has no file path. `abspath` is absent for a remote image file that
              was not retrieved.

            The time of the image is STOP_TIME, else IMAGE_TIME, and its exposure is
            EXPOSURE_DURATION. A geometrically corrected image has a 1000x1000 FOV and
            every other product an 800x800 one.

        Raises:
            oops.host.HostError: If `frame` is not given and the C kernels hold no record
                for the image's spacecraft clock stop count. The SPICE exception is the
                cause, and the message ends with its short error code, as in
                "...: SPICE(CKINSUFFDATA)", so that missing SPICE data can be told
                from other failures.
            oops.host.HostFormatError: If no source read gives the spacecraft, the
                camera, the time, the exposure, the mission phase or, unless `frame` is
                given, the spacecraft clock stop count, or if the image file must be read
                and parsed content given without a file path does not locate it. The
                message names the missing item and the file.
            oops.host.PdsHostError: If the PDS3 label cannot be parsed.
            oops.host.VicarHostError: If the VICAR header cannot be parsed.
            oops.host.WrongHostError: If the file is not a Voyager ISS image.
            OSError: If the file, or unless `astrometry` is True the image file a PDS3
                label names, cannot be read.
        """

        VoyagerISS.initialize()     # define everything the first time through

        pds3_options = oops.Host._pds3_options_with_defaults(pds3_options)
        parsed = VoyagerISS._parse_fileinfo(fileinfo, astrometry=astrometry,
                                            pds3_options=pds3_options)

        # Given a PDS3 label, the image file it names is the observation's file, and it
        # is read too unless only timing and astrometry are needed. Given the image file,
        # it alone is read.
        if isinstance(parsed, Pds3Label):
            label = parsed
            filepath = VoyagerISS._image_path(label, find=not astrometry)
            header = None
        else:
            label = None
            filepath = None if parsed.filepath is None else FCPath(parsed.filepath)
            header = parsed

        # Read the data, if needed, where the VICAR header places them, whichever file
        # was given; the labels of some GEOMED products place the image a record too
        # early or too late, such as C2673426_GEOMED of VGISS_7205 and C3489611_GEOMED
        # of VGISS_6112.
        data = None
        if not astrometry:
            if not isinstance(header, VicarImage):
                if filepath is None:
                    raise oops.host.HostFormatError('Voyager ISS content given without a '
                                                    'file path does not locate its '
                                                    'image file')
                header = VoyagerISS._read_vicar_image(filepath)
            data = header.data_2d

        label_dict = None if label is None else label.as_dict()
        header_dict = None if header is None else header.as_dict(unique=False)
        merged = VoyagerISS._merge_items(
            {} if label is None else _without_nulls(label_dict),
            {} if header is None else VoyagerISS._header_items(header))
        dict_ = _without_nulls({**(header_dict or {}), **merged})

        overrides = {
            'target'      : target,
            'lightsource' : lightsource,
            'path'        : path,
            'frame'       : frame,
            'cadence'     : cadence,
            'fov'         : fov,
            'calibrations': calibrations,
            'timeshift'   : timeshift,
            'navigation'  : navigation,
            'tracker'     : tracker,
            'parallel'    : parallel,
        }

        sources = {'label_dict': label_dict, 'header_dict': header_dict,
                   'if_scale': None if header is None else VoyagerISS._if_scale(header)}
        return VoyagerISS._make_snapshot(dict_, filepath=filepath, data=data,
                                         sources=sources,
                                         fetched=label is None or not astrometry,
                                         return_all_planets=return_all_planets,
                                         overrides=overrides)

    @staticmethod
    def _parse_fileinfo(fileinfo, *, astrometry, pds3_options):
        """The parsed PDS3 label or VICAR header of a Voyager ISS image.

        Parameters:
            fileinfo (str | pathlib.Path | FCPath | Pds3Label | VicarLabel | VicarImage):
                The path to the PDS3 label or the VICAR image file, or its parsed content.
            astrometry (bool): True to read only the header of a VICAR file.
            pds3_options (dict[str, Any]): The options for the PDS3 label parser,
                including its method.

        Returns:
            Pds3Label | VicarLabel | VicarImage: The parsed content.

        Raises:
            oops.host.PdsHostError: If the PDS3 label cannot be parsed.
            oops.host.VicarHostError: If the VICAR header cannot be parsed.
            oops.host.WrongHostError: If the file is not a Voyager ISS image.
            OSError: If the file cannot be read.
        """

        # The fast parser rejects some valid labels, such as C2673426_GEOMED.LBL of
        # VGISS_7205, in which the quoted value of NOTE starts on the next line; the
        # strict parser rejects labels with common syntax errors that "loose" tolerates
        method = pds3_options['method']
        methods = (_PDS3_METHODS[_PDS3_METHODS.index(method):]
                   if method in _PDS3_METHODS else (method,))
        for method in methods[:-1]:
            try:
                return VoyagerISS._read_fileinfo(fileinfo, formats='PV',
                                                 astrometry=astrometry,
                                                 pds3_options=pds3_options
                                                 | {'method': method})
            except oops.host.PdsHostError:
                continue

        return VoyagerISS._read_fileinfo(fileinfo, formats='PV', astrometry=astrometry,
                                         pds3_options=pds3_options
                                         | {'method': methods[-1]})

    @staticmethod
    def _image_path(label, *, find):
        """The image file that a PDS3 label describes.

        Parameters:
            label (Pds3Label): The parsed label.
            find (bool): True to look for the file, which then need not have the same
                case as in the label; False to name it without touching the file system.

        Returns:
            FCPath | None: The file named by the label's IMAGE pointer, in the directory
            of the label. If `find` is True and no file of that name exists, the same
            name in upper or lower case is used if it exists. None if the label was
            parsed from content without a file path.
        """

        if label.filepath is None:
            return None

        name = label['^IMAGE']
        parent = FCPath(label.filepath).parent
        if find:
            for option in dict.fromkeys((name, name.upper(), name.lower())):
                if (parent / option).is_file():
                    return parent / option
        return parent / name

    @staticmethod
    def _read_vicar_image(filepath):
        """The VICAR image and header in an image file.

        Parameters:
            filepath (FCPath): The path to the image file.

        Returns:
            VicarImage: The image with its header.

        Raises:
            oops.host.VicarHostError: If the header cannot be parsed.
            OSError: If the file cannot be read.
        """

        try:
            return VicarImage(filepath, strict=False)
        except VicarError as err:
            raise oops.host.VicarHostError(str(err)) from err

    @staticmethod
    def _if_scale(header):
        """The factor that converts a calibrated product's data to I/F.

        Parameters:
            header (VicarLabel | VicarImage): The VICAR header of the image.

        Returns:
            float | None: The factor from the header line "FOR (I/F)*10000., MULTIPLY DN
            VALUE BY x", which FICOR77 writes as LABEL3, so x / 10000; None if the
            header has no such line.
        """

        for value in header.as_dict(unique=False).values():
            match = _IF_LINE.search(value) if isinstance(value, str) else None
            if match:
                return float(match['scale']) / _IF_LINE_UNITS
        return None

    @staticmethod
    def from_index(filepath, *, supplement=None, row_dicts=None, pds3_options=None,
                   filter=None, return_all_planets=False, select=None, target=None,
                   lightsource=None, path=None, frame=None, cadence=None, fov=None,
                   calibrations=None, timeshift=None, navigation=None, tracker=None,
                   parallel=None, **kwargs):
        """A list of Snapshot objects, one for each row of a Voyager ISS raw image index.

        Parameters:
            filepath (str | pathlib.Path | FCPath): The path to the PDS label of a VGISS
                raw image index, such as "VGISS_6109_raw_image_index.lbl", which has one
                row per raw image. The volume index, "VGISS_6109_index.lbl", which has a
                row for every product, is not accepted.
            supplement (str | pathlib.Path | FCPath, optional): The path to the PDS label
                of the supplemental index, whose columns are merged into each row. Its
                rows must name the same files in the same order as the index; where both
                tables give a column, the index value is kept. If not specified, it is
                the file in the directory of `filepath` whose name matches except that
                "_raw_image_index" is replaced by "_supplemental_index" in upper or lower
                case.
            row_dicts (list[dict[str, Any]], optional): A list of dictionaries, one per
                row of the index, or of some of its rows. Include if `filepath` has
                already been read. If the rows have no STOP_TIME column, the supplement
                has not been merged; each row is then merged with the row of the
                supplement that has the same VOLUME_NAME and FILE_SPECIFICATION_NAME.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser.
            filter (dict[str, Any], optional): A dictionary of index column names and
                their values or a set or tuple of values. If provided, only rows of the
                index in which each named column equals the value, falls in the set of
                values, or falls in a range defined by a tuple of two values, will be
                included in the returned list.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            select (int | slice | str | tuple[int | slice | str, ...]): Accepted for
                compatibility with :meth:`oops.Host.from_index` and ignored, because each
                row describes a single observation.
            target (str | oops.Body, optional): Override for the default target Body of
                every observation. Use "NONE" for inertial pointing, indicating that no
                Solar System body was tracked.
            lightsource (oops.LightSource, optional): Override for the default LightSource
                of every observation.
            path (str | oops.Path, optional): Override for the Path of the observer.
            frame (str | oops.Frame, optional): Override for the Frame of the observing
                instrument.
            cadence (oops.Cadence, optional): Override for the Cadence of the observation.
            fov (oops.FOV, optional): Override for the FOV of every observation.
            calibrations (oops.Calibration | list[oops.Calibration], optional): Override
                for the calibration or list of calibrations.
            timeshift (tuple[float, str], optional): Assign a Fittable time shift to one
                or more attributes of the Observation. The first item is the initial time
                shift in seconds. The second item is a string indicating which attributes
                are to be time-shifted: "T" for the start/stop time, "P" for the path,
                and/or "F" for the frame. If multiple letters are given, the time shifts
                are coupled to whichever attribute is listed first.
            navigation (tuple[float, ...], optional): Two or three rotation angles in
                radians to apply to the frame in order to align the calculated geometry
                with the observed geometry. In this case, a fittable Navigation frame
                wraps the default frame. Specify three angles to include a rotation about
                the optic axis or two for a pointing offset without rotation. Use (0,0) or
                (0,0,0) as the input if you have no starting guess.
            tracker (str, optional): Assign a TrackerFrame to the Observation, to ensure
                that the target body remains at a fixed position within the FOV. Use one
                of "start", "midtime", and "end", indicating the time within the
                Observation at which the target body's calculated position within the FOV
                is accurate.
            parallel (Observation, optional): A parallel Observation (same origin and
                time, different frame and FOV) relative to which this Observation has a
                fixed offset. Note that no more than one of `parallel` and `tracker` can
                be specified.
            **kwargs: Additional keyword arguments; they are accepted and ignored.

        Returns:
            list[Snapshot]: One raw 800x800 observation per row of the index, each with
            subfields `dict`, `host`, `instrument`, `detector`, `planet`, `spacecraft`,
            `spice_kernels`, `filepath`, `basename`, `spice_to_frame`,
            `spice_frame_name` and `spice_frame_id` inserted, and `filter`,
            `label_target`, `spacecraft_clock_start_count` and
            `spacecraft_clock_stop_count` when the row gives them, as described for
            :meth:`from_file`. `dict` is the row, merged with its row of the supplement,
            without the columns whose values are null. `filepath` names the raw image
            file: the VOLUME_NAME, the directory of the FILE_SPECIFICATION_NAME and the
            PRODUCT_ID joined by slashes. A row is left out if it gives no time,
            exposure, camera, spacecraft or mission phase, or if `frame` is not given
            and the C kernels hold no record for the image's spacecraft clock stop
            count.

        Raises:
            FileNotFoundError: If the supplement does not exist.
            oops.host.HostIndexError: If `supplement` is not specified and the name of
                `filepath` contains no "_raw_image_index", if the supplement does not
                have the same number of rows as the index, or if a row's VOLUME_NAME or
                FILE_SPECIFICATION_NAME differs from the index's; or, for `row_dicts`
                without STOP_TIME, if the supplement has no row for one of them.
            oops.host.WrongHostError: If `filepath` is not a Voyager ISS index.
            OSError: If `filepath`, or a `supplement` that is specified, does not exist or
                cannot be read.
        """

        VoyagerISS.initialize()

        # Read and filter the index table. oops.Host.from_index() passes the rows of
        # whatever index it was given, read without the supplement, whose name it cannot
        # derive. Naming the supplement here rejects any index but a raw image index, and
        # the supplement is merged into rows that lack it.
        filepath = FCPath(filepath)
        if supplement is None:
            supplement = VoyagerISS._supplement_path(filepath, pds3_options)
        if row_dicts is None:
            row_dicts = VoyagerISS._read_index_rows(filepath, supplement=supplement,
                                                    validate=_INDEX_KEYS,
                                                    pds3_options=pds3_options)
        elif row_dicts and 'STOP_TIME' not in row_dicts[0]:
            row_dicts = VoyagerISS._merge_supplement(row_dicts, supplement, pds3_options)
        row_dicts = oops.Host._filter_index_rows(row_dicts, filter)

        overrides = {
            'target'      : target,
            'lightsource' : lightsource,
            'path'        : path,
            'frame'       : frame,
            'cadence'     : cadence,
            'fov'         : fov,
            'calibrations': calibrations,
            'timeshift'   : timeshift,
            'navigation'  : navigation,
            'tracker'     : tracker,
            'parallel'    : parallel,
        }

        snapshots = []
        for row_dict in row_dicts:
            directory = row_dict['FILE_SPECIFICATION_NAME'].rpartition('/')[0]
            fpath = '/'.join([row_dict['VOLUME_NAME'], directory,
                              row_dict['PRODUCT_ID']])
            try:
                obs = VoyagerISS._make_snapshot(_without_nulls(row_dict), filepath=fpath,
                                                return_all_planets=return_all_planets,
                                                overrides=overrides)
            except (_MissingItemError, _UnusableImageError):
                # A time or pointing taken from anywhere else could be minutes wrong
                continue
            snapshots.append(obs)

        return snapshots

    @staticmethod
    def _supplement_path(filepath, pds3_options):
        """The supplemental index beside a raw image index.

        Parameters:
            filepath (FCPath): The path to the label of the raw image index.
            pds3_options (dict[str, Any] | None): The options for the PDS3 label parser.

        Returns:
            FCPath: The path to the label of the supplemental index.

        Raises:
            oops.host.HostIndexError: If the name of `filepath` contains no
                "_raw_image_index" and the file is a Voyager ISS index.
            oops.host.WrongHostError: If the name of `filepath` contains no
                "_raw_image_index" and the file is not a Voyager ISS index.
        """

        name = filepath.name
        for (index, supp) in (('_raw_image_index', '_supplemental_index'),
                              ('_RAW_IMAGE_INDEX', '_SUPPLEMENTAL_INDEX')):
            if index in name:
                return filepath.with_name(name.replace(index, supp))

        # Report another host's index as such; reading the rows checks the host
        VoyagerISS._read_index_rows(filepath, pds3_options=pds3_options)
        raise oops.host.HostIndexError('Voyager ISS needs a raw image index, '
                                       f'"VGISS_xxxx_raw_image_index.lbl": {filepath}')

    @staticmethod
    def _merge_supplement(row_dicts, supplement, pds3_options):
        """Rows of a raw image index merged with their rows of the supplemental index.

        Parameters:
            row_dicts (list[dict[str, Any]]): Rows of the raw image index.
            supplement (str | pathlib.Path | FCPath): The path to the label of the
                supplemental index.
            pds3_options (dict[str, Any] | None): The options for the PDS3 label parser.

        Returns:
            list[dict[str, Any]]: A new dictionary for each row, holding the row of the
            supplement with the same VOLUME_NAME and FILE_SPECIFICATION_NAME, updated by
            the row of the index.

        Raises:
            oops.host.HostIndexError: If the supplement has no row for one of the rows.
        """

        supp_dicts = oops.Host._read_index_rows(supplement, pds3_options=pds3_options)
        supp_by_key = {tuple(row[key] for key in _INDEX_KEYS): row for row in supp_dicts}

        merged = []
        for row_dict in row_dicts:
            key = tuple(row_dict[key] for key in _INDEX_KEYS)
            if key not in supp_by_key:
                raise oops.host.HostIndexError(f'no row for {"/".join(key)} in '
                                               f'{supplement}')
            merged.append(supp_by_key[key] | row_dict)
        return merged

    @staticmethod
    def _make_snapshot(dict_, filepath, data=None, *, sources=None, fetched=True,
                       return_all_planets=False, overrides):
        """A Snapshot built from merged metadata or a row of an index.

        Parameters:
            dict_ (dict): The metadata merged from the PDS3 label and the VICAR header, or
                an index row, in either case without null values.
            filepath (FCPath | str | None): The path to the image file, or for an index
                row, the VOLUME_NAME, directory, and PRODUCT_ID joined by slashes; None
                if it is not known.
            data (numpy.ndarray, optional): The image array; None to omit the `data`
                subfield.
            sources (dict[str, Any], optional): The values of the subfields `label_dict`,
                `header_dict` and `if_scale`, each None if it is absent.
            fetched (bool, optional): False if the image file was not read, so that a
                remote image file is not in the local cache.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            overrides (dict[str, Any]): The values of the standard overrides, `target`,
                `lightsource`, `path`, `frame`, `cadence`, `fov`, `calibrations`,
                `timeshift`, `navigation`, `tracker` and `parallel`, as described for
                :meth:`from_file`.

        Returns:
            Snapshot: The observation, with the subfields listed for :meth:`from_file`.

        Raises:
            _MissingItemError: If `dict_` gives no spacecraft, camera, time, exposure or
                mission phase, or, if `frame` is not overridden, no clock stop count.
            _UnusableImageError: If `frame` is not overridden and the C kernels hold no
                record for the image's spacecraft clock stop count.
        """

        name = dict_.get('PRODUCT_ID', '') if filepath is None else filepath
        vgr = VoyagerISS._spacecraft(dict_, name)
        camera = VoyagerISS._camera(dict_, name)
        geomed = dict_.get('PRODUCT_TYPE') == _GEOMED
        texp = float(VoyagerISS._required(dict_, 'EXPOSURE_DURATION', name))
        tstop = VoyagerISS._tstop(dict_, name)
        planet = VoyagerISS._required(dict_, 'MISSION_PHASE_NAME', name).split()[0]

        params = {
            'cadence'     : oops.cadence.SnapCadence(tstop - texp, texp),
            'fov'         : VoyagerISS._FOVS[camera, geomed],
            'path'        : _Voyager.PATH_IDS[vgr],
            'frame'       : None,
            'lightsource' : 'SUN',
            'calibrations': [],
        }

        label_target = None
        if 'TARGET_NAME' in dict_:
            label_target = str(dict_['TARGET_NAME'])
            if label_target in _TARGET_STARS:
                params['lightsource'] = oops.lightsource.star_lookup(
                    _TARGET_STARS[label_target])
                params['target'] = 'NONE'
            elif label_target in _PLANET_TARGETS:
                params['target'] = planet
            else:
                params['target'] = _TARGET_NAME_REPAIRS.get(label_target, label_target)

        # Look up the pointing unless a custom frame was given
        using_cks = overrides['frame'] is None
        if using_cks:
            params['frame'] = VoyagerISS._camera_frame(dict_, vgr, camera, tstop, name)

        VoyagerISS._apply_path_overrides(params, filepath, fetched, overrides)

        obs = oops.observation.Snapshot(('v', 'u'), **params)
        if data is not None:
            obs.insert_subfield('data', data)

        sources = sources or {}
        obs.insert_subfield('dict', dict_)
        for key in ('label_dict', 'header_dict'):
            if sources.get(key) is not None:
                obs.insert_subfield(key, sources[key])
        obs.insert_subfield('host', 'Voyager')
        obs.insert_subfield('instrument', 'ISS')
        obs.insert_subfield('detector', camera)
        if 'FILTER_NAME' in dict_:
            obs.insert_subfield('filter', _filter_name(dict_['FILTER_NAME']))
        obs.insert_subfield('planet', planet)
        if label_target is not None:
            obs.insert_subfield('label_target', label_target)
        obs.insert_subfield('spacecraft', vgr)
        if sources.get('if_scale') is not None:
            obs.insert_subfield('if_scale', sources['if_scale'])
        for key in ('SPACECRAFT_CLOCK_START_COUNT', 'SPACECRAFT_CLOCK_STOP_COUNT'):
            if key in dict_:
                obs.insert_subfield(key.lower(), str(dict_[key]))

        # With a custom frame, pointing never came from a CK, so any CK that happens to be
        # furnished is unrelated to this observation and must not be reported as used.
        kernels = _Voyager.used_kernels(obs.time, vgr, f'iss{camera[0].lower()}a',
                                        return_all_planets=return_all_planets,
                                        ck=using_cks)
        obs.insert_subfield('spice_kernels', kernels)
        obs.insert_subfield('spice_to_frame', oops.Matrix3.IDENTITY)
        obs.insert_subfield('spice_frame_name', f'VG{vgr}_ISS{camera[0]}A')
        obs.insert_subfield('spice_frame_id', (_SCAN_PLATFORM_ID[vgr]
                                               - _CAMERA_FRAME_OFFSET[camera]))

        return obs

    @staticmethod
    def _apply_path_overrides(params, filepath, fetched, overrides):
        """Apply the overrides to the Snapshot parameters and name the image file.

        Parameters:
            params (dict): The Snapshot parameters, modified in place.
            filepath (FCPath | str | None): The path to the image file, or for an index
                row, its path as a string; None if it is not known, in which case no
                path parameters are set.
            fetched (bool): False if the image file was not read; `abspath` is then left
                out for a remote file, which would name a file not in the local cache.
            overrides (dict[str, Any]): The standard overrides.
        """

        oops.Host._apply_overrides(params, '' if filepath is None else filepath,
                                   **overrides)
        if filepath is None:
            del params['filepath']
            del params['basename']
        elif not fetched and not isinstance(filepath, str) and not filepath.is_local():
            del params['abspath']

    @staticmethod
    def _required(dict_, key, name):
        """The value of an item that the Snapshot cannot be built without.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            key (str): The name of the item.
            name (FCPath | str): The image file or product, for the message.

        Returns:
            Any: The value.

        Raises:
            _MissingItemError: If `dict_` does not give the item.
        """

        if key not in dict_:
            raise _MissingItemError(f'Voyager ISS image {name} has no {key}')
        return dict_[key]

    @staticmethod
    def _spacecraft(dict_, name):
        """The spacecraft number, 1 or 2, from INSTRUMENT_HOST_NAME or SPACECRAFT_NAME."""

        for key in ('INSTRUMENT_HOST_NAME', 'SPACECRAFT_NAME'):
            if key in dict_:
                return int(dict_[key][-1])
        raise _MissingItemError(f'Voyager ISS image {name} has no INSTRUMENT_HOST_NAME '
                                'or SPACECRAFT_NAME')

    @staticmethod
    def _camera(dict_, name):
        """The camera, "NAC" or "WAC", from INSTRUMENT_ID or INSTRUMENT_NAME.

        A label gives INSTRUMENT_ID "ISSN" or "ISSW"; an index row gives only
        INSTRUMENT_NAME, "NARROW ANGLE CAMERA" or "WIDE ANGLE CAMERA".
        """

        if 'INSTRUMENT_ID' in dict_:
            return dict_['INSTRUMENT_ID'][-1] + 'AC'
        return VoyagerISS._required(dict_, 'INSTRUMENT_NAME', name)[0] + 'AC'

    @staticmethod
    def _tstop(dict_, name):
        """The close of the shutter in seconds TDB.

        STOP_TIME, which a label and the supplemental index give to 1/100 s, is used if
        known; otherwise IMAGE_TIME, which the index gives to the second and which is
        otherwise the same. The spacecraft clock count is never used, because it can fall
        whole FDS minutes after the shutter closed.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            name (FCPath | str): The image file or product, for the message.

        Returns:
            float: The time.

        Raises:
            _MissingItemError: If `dict_` gives neither STOP_TIME nor IMAGE_TIME.
        """

        for key in ('STOP_TIME', 'IMAGE_TIME'):
            if key in dict_:
                return julian.tdb_from_tai(julian.tai_from_iso(dict_[key]))

        raise _MissingItemError(f'Voyager ISS image {name} has no STOP_TIME or '
                                'IMAGE_TIME')

    @staticmethod
    def _clock_count(dict_, name):
        """The image's spacecraft clock stop count, "hhhhh:mm:ddd".

        A label, a VICAR header and the supplemental index give
        SPACECRAFT_CLOCK_STOP_COUNT. Otherwise it is derived from IMAGE_NUMBER, the FDS
        count "hhhhh.mm" of the image, which matches the stop count for all but five of
        the 76,652 VGISS raw images.

        Raises:
            _MissingItemError: If `dict_` gives neither.
        """

        if 'SPACECRAFT_CLOCK_STOP_COUNT' in dict_:
            return dict_['SPACECRAFT_CLOCK_STOP_COUNT']

        number = VoyagerISS._required(dict_, 'IMAGE_NUMBER', name)
        (hours, minutes) = f'{float(number):.2f}'.split('.')
        return f'{int(hours)}:{minutes}:001'

    @staticmethod
    def _camera_frame(dict_, vgr, camera, tstop, name):
        """The fixed camera frame built from the image's own C-kernel record.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            vgr (int): The spacecraft number.
            camera (str): "NAC" or "WAC".
            tstop (float): The close of the shutter in seconds TDB, which selects the
                clock partition when `dict_` does not give it.
            name (FCPath | str): The image file or product, for the message.

        Returns:
            oops.frame.Cmatrix: An unregistered frame relative to J2000.

        Raises:
            _MissingItemError: If `dict_` gives no clock stop count.
            _UnusableImageError: If the C kernels hold no record for the clock count. Its
                cause is the exception that SPICE raised, and its message ends with the
                SPICE short error code, such as "SPICE(CKINSUFFDATA)", if that exception
                gives one.
        """

        count = VoyagerISS._clock_count(dict_, name)
        partition = dict_.get('SPACECRAFT_CLOCK_PARTITION_NUMBER')
        platform_id = _SCAN_PLATFORM_ID[vgr]
        try:
            ticks = _Voyager.sclk_ticks(vgr, count, partition, near=tstop)
            (j2000_to_platform, _) = cspyce.ckgp(platform_id, ticks, _TICK_TOLERANCE,
                                                 'J2000')
        except (ValueError, LookupError, OSError) as err:
            code = _SPICE_ERROR_CODE.search(str(err))
            suffix = f': {code[0]}' if code else ''
            raise _UnusableImageError(f'no Voyager {vgr} scan platform pointing for '
                                      f'clock count {count}{suffix}') from err

        platform_to_camera = cspyce.pxform(f'VG{vgr}_SCAN_PLATFORM',
                                           f'VG{vgr}_ISS{camera[0]}A', 0.)
        return oops.frame.Cmatrix(oops.Matrix3(platform_to_camera)
                                  * oops.Matrix3(j2000_to_platform))

    @staticmethod
    def _header_items(vic):
        """The metadata items of a VICAR header, under their PDS3 names and in the forms
        of a PDS3 label.

        Parameters:
            vic (VicarLabel | VicarImage): The VICAR header.

        Returns:
            dict[str, Any]: The items that the header gives: INSTRUMENT_HOST_NAME,
            INSTRUMENT_ID, PRODUCT_TYPE, IMAGE_NUMBER, SPACECRAFT_CLOCK_STOP_COUNT,
            IMAGE_ID, IMAGE_TIME, STOP_TIME, EXPOSURE_DURATION, FILTER_NAME,
            FILTER_NUMBER, TARGET_NAME and MISSION_PHASE_NAME. An item whose field is
            missing, filled with asterisks, or not valid is left out.
        """

        dict_ = vic.as_dict(unique=False)
        lab02 = str(dict_.get('LAB02', ''))
        lab03 = str(dict_.get('LAB03', ''))
        items = {}

        if match := _LAB02_SPACECRAFT.search(lab02):
            items['INSTRUMENT_HOST_NAME'] = 'VOYAGER ' + match['vgr']
        if match := _LAB03_CAMERA.search(lab03):
            items['INSTRUMENT_ID'] = 'ISS' + match['camera']

        items['PRODUCT_TYPE'] = 'DECOMPRESSED_RAW_IMAGE'
        for (task, product) in _VICAR_PRODUCT_TASKS:
            if task in vic.get('TASK+', []):
                items['PRODUCT_TYPE'] = product

        # A label spells the count without leading zeros, such as "9096:16:001" for FDS
        # 09096.16. FDS 00000.00 is a fill value, as in C1477512.
        match = _LAB02_FDS.search(lab02)
        if match and int(match['hours']) > 0:
            (hours, minutes) = (match['hours'], match['minutes'])
            items['IMAGE_NUMBER'] = f'{hours}.{minutes}'
            items['SPACECRAFT_CLOCK_STOP_COUNT'] = f'{int(hours)}:{minutes}:001'
        if match := _LAB02_PICNO.search(lab02):
            items['IMAGE_ID'] = match['picno']

        if (scet := _scet(lab02)) is not None:
            items['IMAGE_TIME'] = scet
            items['STOP_TIME'] = scet
            tstop = julian.tdb_from_tai(julian.tai_from_iso(scet))
            items['MISSION_PHASE_NAME'] = _Voyager.encounter(tstop) + ' ENCOUNTER'

        if match := _LAB03_EXPOSURE.search(lab03):
            items['EXPOSURE_DURATION'] = float(match['exp']) / _MSEC
        if match := _LAB03_FILTER.search(lab03):
            items['FILTER_NAME'] = _filter_name(match['name'])
            items['FILTER_NUMBER'] = match['number']

        target = str(dict_.get('LAB05', ''))[_LAB05_TARGET].strip().replace('_', ' ')
        if target:
            items['TARGET_NAME'] = target

        return items

    @staticmethod
    def _merge_items(label_items, header_items):
        """The metadata merged from the PDS3 label and the VICAR header.

        Parameters:
            label_items (dict[str, Any]): The label as a dictionary, without null values;
                empty if the label was not read.
            header_items (dict[str, Any]): The items of the VICAR header, from
                :meth:`_header_items`; empty if the header was not read.

        Returns:
            dict[str, Any]: Every key of either dictionary. Where both give an item, its
            resolution rule in `_RESOLUTION` chooses the value.
        """

        merged = header_items | label_items
        for key in label_items.keys() & header_items.keys():
            merged[key] = _RESOLUTION[key](label_items[key], header_items[key])
        return merged

    ######################################################################################
    # Detectors
    ######################################################################################

    @staticmethod
    def _detect_in_pds3(label):
        """True if the given parsed PDS3 label describes this host's data."""

        host = label.get('INSTRUMENT_HOST_NAME', label.get('SPACECRAFT_NAME', ''))
        return (host.startswith('VOYAGER')
                and label.get('INSTRUMENT_ID', '') in ('ISSN', 'ISSW'))

    @staticmethod
    def _detect_in_vicar(label):
        """True if the given VicarLabel describes this host's data."""

        return (_LAB02_SPACECRAFT.search(str(label.get('LAB02', ''))) is not None
                and _LAB03_CAMERA.search(str(label.get('LAB03', ''))) is not None)

    @staticmethod
    def _detect_in_index(label):
        """None, because a VGISS index label does not name the host or instrument.

        The host is then identified from the rows by :meth:`_detect_in_row`.
        """

        return None

    @staticmethod
    def _detect_in_row(row_dict):
        """True if the given row of an index file refers to this host/instrument."""

        host = row_dict.get('INSTRUMENT_HOST_NAME', row_dict.get('SPACECRAFT_NAME', ''))
        return (host.startswith('VOYAGER')
                and row_dict.get('INSTRUMENT_NAME', '').endswith('ANGLE CAMERA'))

    ######################################################################################
    # Initialization
    ######################################################################################

    @staticmethod
    def initialize(*, planets=None, asof=None, irregulars=True):
        """Initialize key information about the ISS instrument.

        Loads the mission and ISS kernels and constructs the FOVs. Must be called first.
        After the first call, later calls to this function are ignored.

        Parameters:
            planets (list, optional): A list of planets to pass to
                :meth:`~oops.Body.define_solar_system`. None or 0 means all.
            asof (str, optional): Only use SPICE kernels that existed before this date;
                None to ignore.
            irregulars (bool, optional): True to include the irregular satellites; False
                otherwise.
        """

        if VoyagerISS._initialized:
            return

        _Voyager.initialize(planets=planets, asof=asof, irregulars=irregulars)
        _Voyager.load_instruments(asof=asof)

        # Flat FOVs, keyed by (camera, geometrically corrected)
        fovs = {}
        for ((camera, geomed), half_fov) in _HALF_FOV.items():
            pixels = _GEOMED_PIXELS if geomed else _RAW_PIXELS
            scale = np.arctan(np.tan(half_fov) / (pixels / 2.))
            fovs[camera, geomed] = oops.fov.FlatFOV((scale, scale), (pixels, pixels))
        VoyagerISS._FOVS = fovs

        VoyagerISS._initialized = True

    @staticmethod
    def _reset():
        """Reset the internal Voyager ISS parameters.

        Can be useful for debugging.
        """

        VoyagerISS._FOVS = {}
        VoyagerISS._initialized = False
        _Voyager.reset()


VoyagerISS._register()

##########################################################################################
