##########################################################################################
# host_voyager/oops/iss.py
##########################################################################################
"""The Voyager ISS host, which makes a Snapshot from each image of the ISS cameras.

The :doc:`user guide </user_guide/user_guide>` describes the products it reads, how it
merges the two labels of each product, and the errors it raises.
"""

import datetime as dt
import pathlib
import re

import julian
import cspyce
import numpy as np

from filecache import FCPath
from pdsparser import Pds3Label
from vicar     import VicarError, VicarImage, VicarLabel

import oops
from ._voyager import _Voyager

__all__ = ['VoyagerISS']

# The scan platform C kernels hold one pointing record per image, stamped at the image's
# own spacecraft clock stop count, which can be whole FDS minutes (48 s each) after the
# true close of the shutter, STOP_TIME. The record is therefore found by the count, never
# by the time; see docs/user_guide/user_guide_appendix_pointing.rst. The tolerance is in
# clock ticks of 60 ms.
_TICK_TOLERANCE = 20

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

# The names of a raw image index and of its supplement differ in this part, in lower or
# upper case: VGISS_6109_raw_image_index.lbl and VGISS_6109_supplemental_index.lbl
_SUPPLEMENT_NAMES = (('_raw_image_index', '_supplemental_index'),
                     ('_RAW_IMAGE_INDEX', '_SUPPLEMENTAL_INDEX'))

# The DATA_SET_ID of every VGISS index label, such as
# "VG1/VG2-J-ISS-2/3/4/6-PROCESSED-V1.0" or "VG2-N-ISS-2/3/4/6-PROCESSED-V1.0", for the
# planet J, S, U, or N. The ring profiles of VG_28xx, "VG1/VG2-SR-ISS-4-PROFILES-V1.0",
# are another data set.
_ISS_DATA_SET = re.compile(r'(VG1/VG2|VG[12])-[JSUN]-ISS-')

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

# The target field of a VICAR header holds at most eight characters, drops blanks and
# names some satellites by their discovery designations. Each spelling that no VGISS
# label uses, found in a survey of the headers of all 76,652 VGISS raw images, maps to the
# label's name for the same target, so that an image file alone names a target that oops
# knows (#14 lists the differences; the table is #15).
_HEADER_TARGET_SPELLINGS = {
    'CALLAMPS': 'CAL LAMPS',
    'ENCELADU': 'ENCELADUS',
    'EPIMETHE': 'EPIMETHEUS',
    'PROMETHE': 'PROMETHEUS',
    '1985U1'  : 'PUCK',
    '1989N1'  : 'PROTEUS',
    '1989N2'  : 'LARISSA',
    'SIGMASGR': 'SIGMA SGR',
    'BETACMA' : 'BETA CMA',
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
_LAB04_ERT        = re.compile(r'^\s*ERT\s+(?P<year>\d\d)\.(?P<doy>\d{3})\s+'
                               r'(?P<hms>\d\d:\d\d:\d\d)\s')
_MSEC = 1000.

# The name of a Voyager image file begins with "C" and its FDS count, as in C3450201_RAW
# for FDS 34502.01
_NAME_COUNT = re.compile(r'C(?P<hours>\d{5})(?P<minutes>[0-5]\d)')

# The target name occupies these columns of LAB05, with underscores for blanks
_LAB05_TARGET = slice(31, 43)

# The header lines whose fields are converted to PDS3 items; they appear in `dict` only
# under the names of those items, and as they are in `header_dict`
_CONVERTED_HEADER_LINES = ('LAB02', 'LAB03', 'LAB05')

# The VICAR TYPE of an image; the RESLOC and GEOMA files are tables
_VICAR_IMAGE_TYPE = 'IMAGE'

# The line of a calibrated product's VICAR header that gives its I/F scale, as in
#   LABEL3='FOR (I/F)*10000., MULTIPLY DN VALUE BY               1.00000'
# A factor that is not a number, such as N/A, is missing.
_IF_LINE = re.compile(r'FOR \(I/F\)\*10000\., MULTIPLY DN VALUE BY\s+'
                      r'(?P<scale>[-+]?(\d+\.?\d*|\.\d+)([Ee][-+]?\d+)?)\s*$')
_IF_LINE_UNITS = 10000.

# The NULL_CONSTANT of the numeric columns of a VGISS raw image index, which counts as
# missing, as do the texts of oops.host.PDS3_NULL_TEXTS. Among those texts, the raw image
# indexes give EARTH_RECEIVED_TIME "UNKNOWN" (1,524 rows) and IMAGE_ID "UNKNOWN" (250
# rows) where the labels give "UNK", and NOTE "" (36,095 rows). Until the index is
# corrected, "UNKNOWN" is read as missing. A corrected table gives "UNK", which is
# missing too. SETI/rms-data-projects#260 item 5. No VGISS metadata index table gives
# "NOT APPLICABLE", which oops counts as a placeholder too (grep of every one,
# 2026-11-19).
_NULL_NUMBER = -99.9999

# Placeholders that stand for an unknown value of one item and are never a real value of
# it: the picture number "0000Z1+000" or "0000Z2+000", which 34 of the 76,652 VGISS raw
# images give in their labels and indexes, as their VICAR headers give it in LAB02. The
# labels and headers keep these values, so the rule stays for them. An index row gives
# the placeholder until the index is corrected; a corrected row gives a null, which is
# missing as well, so it is read as it is. SETI/rms-data-projects#260 item 3
_PLACEHOLDERS = {'IMAGE_ID': re.compile(r'0000Z[12]\+000')}

# The two-digit years of a SCET or ERT: VGISS images date from 1978 to 1989. A garbled
# LAB02 such as "SCET 08.272 77:05:04" is rejected by its year or its time of day.
_HEADER_YEARS = range(77, 100)
_HEADER_CENTURY = 1900

# A SCET or ERT, which is to the second, in the form in which the PDS3 label parser gives
# the label's IMAGE_TIME and STOP_TIME, which carry fractions of a second, and its
# EARTH_RECEIVED_TIME, which carries none
_SCET_FORMAT = '%Y-%m-%dT%H:%M:%S.000'
_ERT_FORMAT  = '%Y-%m-%dT%H:%M:%S'

# Each VGISS raw image index label gives the EXPOSURE_DURATION column 7 bytes, though its
# FORMAT, "F9.4", and the table itself give it 9, so a table reader that follows the label
# drops the last two characters: 0.0450 s becomes 0.04 s, 0.0075 s becomes 0.0 s, and the
# null constant -99.9999 becomes -99.99. All 87 volumes (the *999 cumulative indexes
# aside) do this, for 1,597 of their 76,652 rows; no other column of these indexes or of
# their supplements differs from its FORMAT. Until the index is corrected, a column is
# widened to its FORMAT's width where its BYTES are fewer, which is decided from the label
# of each index. A corrected label gives BYTES = 9, which is left as it is.
# SETI/rms-data-projects#260 item 1
_FORMAT_WIDTH = re.compile(r'[A-Z](?P<width>\d+)')


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


def _is_spice_error(err):
    """True if an exception means that the SPICE kernels cannot supply what was asked.

    Parameters:
        err (Exception): An exception of one of the classes that cspyce raises.

    Returns:
        bool: True for what :meth:`oops.OopsSpiceError.is_spice_error` accepts, an
        exception whose text holds a SPICE error code, and for the
        :class:`oops.OopsSpiceError` that ``_Voyager.sclk_ticks()`` raises for a count
        that the clock kernel cannot place in one partition.
    """

    return isinstance(err, oops.OopsSpiceError) or oops.OopsSpiceError.is_spice_error(err)


def _is_null(value):
    """True if a label, index or header value means unknown.

    Parameters:
        value (Any): The value.

    Returns:
        bool: True for what :func:`oops.host.is_pds3_null` counts as null: None, a
        placeholder text ("UNK", "UNKNOWN", "N/A", "NULL", "NOT APPLICABLE", "---" or an
        empty string, in any case and with any surrounding blanks), and a non-empty list
        of nothing but these. True also for the -99.9999 of an index. No VGISS label
        gives a list with only some elements null.
    """

    return (oops.host.is_pds3_null(value)
            or (isinstance(value, float) and value == _NULL_NUMBER))


def _without_nulls(dict_):
    """A copy of a dictionary without the keys whose values mean unknown: a null, as
    :func:`_is_null` defines it, or a placeholder of `_PLACEHOLDERS` for that key. A
    value that is itself a dictionary, such as the IMAGE object of a label, is copied
    the same way."""

    return {key: _without_nulls(value) if isinstance(value, dict) else value
            for (key, value) in dict_.items()
            if not (_is_null(value) or (key in _PLACEHOLDERS and isinstance(value, str)
                                        and _PLACEHOLDERS[key].fullmatch(value.strip())))}


def _plain_label_dict(dict_):
    """A PDS3 label dictionary in the forms that JSON can hold.

    Parameters:
        dict_ (dict[str, Any]): The dictionary from :meth:`~pdsparser.Pds3Label.as_dict`,
            or one of its OBJECTs.

    Returns:
        dict[str, Any]: A copy holding only the label's keywords, in which each date or
        time is its ISO 8601 string as the parser formats it (its "_fmt" entry, such as
        "1980-10-29T05:57:22.000"), each set is its ordered list (its "_list" entry), and
        each OBJECT is converted the same way. The keys whose values are None, such as
        END, are left out, as is every entry that the parser adds beside the keywords,
        whose name holds lower-case letters: "STOP_TIME_fmt", "STOP_TIME_day",
        "EXPOSURE_DURATION_unit", "^IMAGE_offset", "DESCRIPTION_unwrap", "objects", and
        the "_source", "_type" and "_detail" entries of the parser's options.
        EDIT_MODE_ID is text, such as "1:32", even where the parser gives a time.
    """

    plain = {}
    for (key, value) in dict_.items():
        # The parser spells every entry it adds with lower-case letters, as its own
        # `old_format` option of as_dict() recognizes them
        if key != key.upper():
            continue
        # The fast parser reads the quoted EDIT_MODE_ID "1:32" (472 raw images of the
        # VGISS_8xxx volumes, each with four labels) as the time 01:32, where the strict
        # parser, the index and the label give the text "1:32". Until the parser is
        # fixed, the text is rebuilt from the time; "1:32" is the only edit mode in the
        # VGISS indexes that looks like a time. SETI/rms-pdsparser#29
        if key == 'EDIT_MODE_ID' and isinstance(value, dt.time):
            plain[key] = f'{value.hour}:{value.minute:02d}'
        elif isinstance(value, (dt.date, dt.time)):     # datetime is a date
            plain[key] = dict_[key + '_fmt']
        elif isinstance(value, set):
            plain[key] = dict_[key + '_list']
        elif isinstance(value, dict):
            plain[key] = _plain_label_dict(value)
        elif value is not None:
            plain[key] = value
    return plain


def _plain_header_dict(vic):
    """A VICAR header as a dictionary that keeps every parameter.

    Parameters:
        vic (VicarLabel | VicarImage): The VICAR header.

    Returns:
        dict[str, Any]: Every parameter of the header, in order. A parameter that occurs
        more than once, such as the TASK, USER and DAT_TIM of each step of the processing
        history, is keyed "NAME[n]" for its nth occurrence, counting from 0, as in
        "TASK[0]" and "TASK[1]"; a parameter that occurs once is keyed by its name.
    """

    return {(key if isinstance(key, str) else f'{key[0]}[{key[1]}]'): value
            for (key, value) in vic.as_dict(unique=True).items()}


def _plain_row(row_dict):
    """A row of an index with each NumPy value as the Python value it holds.

    Parameters:
        row_dict (dict[str, Any]): The row.

    Returns:
        dict[str, Any]: A copy of the row in which each NumPy string, integer, float or
        boolean is a Python str, int, float or bool, without the "_mask" entry that the
        table reader adds for each column. The masks are not needed: the only null
        constants that the VGISS indexes declare, the EXPOSURE_DURATION -99.9999 and the
        START_TIME and STOP_TIME "UNK", are left out as nulls, and none stands for a real
        value.
    """

    return {key: value.item() if isinstance(value, np.generic) else value
            for (key, value) in row_dict.items()
            if not (key.endswith('_mask') and key[:-5] in row_dict)}


def _header_time(match, fmt):
    """The time of a SCET or ERT field of a VICAR header.

    Parameters:
        match (re.Match | None): The match of the field, with groups "year", "doy" and
            "hms"; None if the field did not match, as when it is filled with asterisks.
        fmt (str): The format of the result, that of the PDS3 label's time.

    Returns:
        str | None: The time as an ISO 8601 string in the form that the PDS3 label's time
        takes, such as "1980-10-29T05:57:22.000"; None if there is no match or it is not
        a valid time in the years of the mission.
    """

    if match is None or int(match['year']) not in _HEADER_YEARS:
        return None
    year = _HEADER_CENTURY + int(match['year'])
    try:
        time = dt.datetime.strptime(f'{year}.{match["doy"]} {match["hms"]}',
                                    '%Y.%j %H:%M:%S')
    except ValueError:      # an hour of 24 or more, a day of 367 or more, ...
        return None
    if time.year != year:   # day 366 of a common year rolls into the next
        return None
    return time.strftime(fmt)

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
# comparison could not parse, and of 8,319 products read directly in a second survey.
# One issue in this repository records every difference for a final decision (#14).
#
# Each item has one source, "label" or "header", whose value is used for every image
# where the two differ: the source that is accurate for that item where they disagree.
# Where the winner is wrong for some images, the rule still stands, and #14 lists those
# images. A value that is missing from the winner, as a null in the label or an invalid
# field in the header, is not a difference: the other source's value is used.
_RESOLUTION = {
    # 2 differ, C1630038 and C1630041, whose headers are corrupt (their label NOTEs say
    # "HEADER CORRUPT") and say VGR-2 (#14)
    'INSTRUMENT_HOST_NAME'       : 'label',

    # 5 differ, all narrow angle in the label and wide angle in the header. The label
    # NOTEs record the corrections: C1570449 "CAMERA UPDATED BY PDS RINGS NODE", C1984746
    # "CAMERA AND FILTER NAME UPDATED BY PDS RINGS NODE"; C1630038 and C1630042 have
    # corrupt headers. The two choices point 60-75 pixels apart (#14)
    'INSTRUMENT_ID'              : 'label',

    # No difference among the products read (#14)
    'PRODUCT_TYPE'               : 'label',

    # The FDS count: 11 headers give the fill value FDS 00000.00 or 0.00, and 63 give
    # another count, 62 of them one or two FDS minutes later than the label's. For
    # every one checked, the C kernels lack a record at the header's count and hold the
    # image's pointing at the label's (#14)
    'IMAGE_NUMBER'               : 'label',
    'SPACECRAFT_CLOCK_STOP_COUNT': 'label',

    # The picture number: of the products read, 13 headers give the placeholder
    # "0000Z1+000", missing from the header, where the label gives the real one, and none
    # gives another real one (#14)
    'IMAGE_ID'                   : 'label',

    # The header's SCET is to the second and the label's time to 1/100 s. 3 differ by
    # more: C1941239 and C2066403 by one FDS minute (48 s), where the label NOTEs say
    # "TIME CORRECTED BY PDS RINGS" and the clock count gives the label's time, and
    # C4375222 by 60 s, whose clock count gives the header's time. The count can fall
    # after the shutter closed, so it does not settle C4375222, which #14 lists. A
    # further 93 SCETs are asterisks or invalid, so missing from the header.
    'IMAGE_TIME'                 : 'label',
    'STOP_TIME'                  : 'label',

    # About 900 differ. The label's errors are small: about 800 labels, all in
    # VGISS_5xxx, give the exposure truncated to 0.01 s, such as 0.04 s for 45 ms
    # (C2037439) and 0.0 s for 7.5 ms (C1565612), and 12 are plainly wrong, the 11 wide
    # angle plaque images C1565616-C1565706, whose brightness follows the header, and
    # C4070710. The header's errors are large: about 90 long exposures of 15-672 s, such
    # as 96 s for C2049737, written as 5 or 240 ms, while the header's own shutter line
    # says LONG END and the label NOTEs say LONG EXPOSURE. #14 lists the images on which
    # the label is wrong (#14)
    'EXPOSURE_DURATION'          : 'label',

    # 5 differ, such as C1984746, ORANGE in the label and VIOLET in the header, whose
    # label NOTE records the correction (#14)
    'FILTER_NAME'                : 'label',
    'FILTER_NUMBER'              : 'label',

    # About 1,940 differ: besides the spellings of _HEADER_TARGET_SPELLINGS, the header
    # names another target for about 1,170, such as SATURN for the label's S RINGS or
    # STAR for VEGA. About 40 headers name a more specific target, such as THEBE for
    # the label's JUPITER, which #14 lists (#14)
    'TARGET_NAME'                : 'label',

    # Computed from the SCET for a header; no difference where both give it (#14)
    'MISSION_PHASE_NAME'         : 'label',

    # The header's ERT in LAB04 agrees with the label to the second in 25,580 of 25,586
    # raw images read, and the other 6 garble the field, so it is missing from them. Of
    # 353 images read for their header problems, 27 garble it and one differs: C2066403
    # by 23 s, where the label NOTE says "TIME CORRECTED BY PDS RINGS" (#14)
    'EARTH_RECEIVED_TIME'        : 'label',
}


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

    # Every supplement lists the same images in the same order as its raw image index, so
    # the rows are paired by position. A survey of all 182 raw image indexes in the
    # holdings (2026-11-19: the 91 in metadata/VGISS_[5-8]xxx, the cumulative *999
    # indexes included, and their copies in metadata/VGISS_[5-8]xxx_v1.0), reading the
    # VOLUME_NAME, FILE_SPECIFICATION_NAME, and PRODUCT_ID columns of both tables, found
    # every pair of tables equal in length and in agreement on every row, 306,608 rows.
    MERGE_SUPPLEMENT_BY_FILE = False

    _FOVS = {}
    _initialized = False

    @staticmethod
    def from_file(fileinfo, *, return_all_planets=False, pds3_options=None,
                  astrometry=False, select=None, target=None, lightsource=None,
                  path=None, frame=None, cadence=None, fov=None, calibrations=None,
                  timeshift=None, navigation=None, tracker=None, parallel=None):
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
            select (None): Must be None. An image file holds a single observation, so
                there is nothing to select from; any other value raises an error.
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

        Returns:
            Snapshot: The observation, with subfields `dict`, `host`, `instrument`,
            `detector`, `planet`, `spacecraft`, `time_from_sclk`, `calibrations`,
            `spice_kernels`, `spice_to_frame`, `spice_frame_name` and `spice_frame_id`
            inserted; `data` unless `astrometry` is True; and each of these when its
            source gives it: `label_dict`,
            `header_dict`, `filter`, `label_target`, `if_scale`,
            `spacecraft_clock_start_count`, `spacecraft_clock_stop_count`, `filepath`,
            `basename`, `abspath` and `image_url`. A subfield whose value no source read
            gives is absent, never None.

            - `dict` is the metadata merged from every source read. The items that both
              the label and the VICAR header give are keyed by their PDS3 names
              (INSTRUMENT_HOST_NAME, INSTRUMENT_ID, PRODUCT_TYPE, IMAGE_NUMBER,
              SPACECRAFT_CLOCK_STOP_COUNT, IMAGE_ID, IMAGE_TIME, STOP_TIME,
              EXPOSURE_DURATION, FILTER_NAME, FILTER_NUMBER, TARGET_NAME,
              MISSION_PHASE_NAME and EARTH_RECEIVED_TIME), with the header's values
              converted to the label's forms and spellings. Where only one source gives
              an item, its value is used; where both give it and they differ, the
              label's value is used. The header lines LAB02, LAB03 and LAB05, from which
              these items come, appear in `dict` only as the items; LAB04, which holds
              more than the earth received time, appears under its own name too. Every
              other key of each source is kept under its own name, such as LABEL3 of the
              header. A key whose value means unknown is left out, also inside an
              OBJECT such as IMAGE: a placeholder text ("UNK", "UNKNOWN", "N/A", "NULL",
              "NOT APPLICABLE", "---" or an empty string), None, such as the label's
              END, or the placeholder picture number "0000Z1+000" or "0000Z2+000" of
              IMAGE_ID.
              Times are ISO 8601 strings in the form "1980-10-29T05:57:22.000", sets are
              lists, and every value can be written as JSON.
            - `label_dict` is the PDS3 label as a dictionary of its own keywords, with
              its times as ISO 8601 strings and its sets as lists, and without the keys
              whose values are None and the entries that the label parser adds beside
              the keywords, whose names hold lower-case letters, such as
              "STOP_TIME_fmt", "^IMAGE_offset" and "objects"; present only when the
              label was read.
            - `header_dict` is the VICAR header as a dictionary of every parameter, as
              the file gives it, also after the image array is read, which leaves the
              HOST of the machine that wrote the file. A parameter that occurs more than
              once, such as the TASK of each step of the processing history, is keyed
              "NAME[n]" for its nth occurrence, counting from 0, as in "TASK[0]";
              present only when the image file was read.
            - `filter` is the FILTER_NAME as a PDS3 label spells it, such as "CLEAR" or
              "CH4_JS".
            - `label_target` is the TARGET_NAME, as a PDS3 label spells it.
            - `spacecraft` is the int 1 for Voyager 1 or 2 for Voyager 2, from
              INSTRUMENT_HOST_NAME (or SPACECRAFT_NAME), which a VICAR header gives as
              "VGR-1" or "VGR-2" in its LAB02 line. It is present on every input and is
              the way to tell the images of the two spacecraft apart.
            - `if_scale` is the factor that converts the data of a calibrated product
              (CALIB or GEOMED) to I/F, from the line "FOR (I/F)*10000., MULTIPLY DN
              VALUE BY x" of its VICAR header, so x / 10000. It is absent for a product
              whose header has no such line (RAW or CLEANED) and whenever the header
              was not read. The PDS3 label's REFLECTANCE_SCALING_FACTOR is not always
              the same: for Voyager 1 images of Saturn it is 3.345 times larger.
            - `calibrations` is ``[oops.calibration.FlatCalib('I/F', if_scale)]``, which
              converts the data to I/F, when `if_scale` is present, and otherwise an
              empty list; the `calibrations` override replaces it.
            - `time_from_sclk` is True if no source read gives a time, so that the time
              comes from the spacecraft clock count, and False otherwise.
            - `spacecraft_clock_start_count` and `spacecraft_clock_stop_count` are the
              SPACECRAFT_CLOCK_START_COUNT and SPACECRAFT_CLOCK_STOP_COUNT as the label
              spells them, such as "34502:01:001". A VICAR header gives the stop count
              alone, as its FDS image number "hhhhh.mm", which is spelled the same way.
            - `filepath`, `basename`, `abspath` and `image_url` name the image file,
              even when the PDS3 label is given; with `astrometry`, as the label names
              it. `abspath`, the local path of the image file, is present only if this
              call retrieved the file to read its data: never with `astrometry`, and
              never for a VicarImage given. For a remote file it names the copy in the
              local cache. All four are absent if the parsed content given has no file
              path.

            The time of the image, the close of its shutter, is STOP_TIME, else
            IMAGE_TIME, and its exposure is EXPOSURE_DURATION. If no source read gives
            a time, the time is that of the spacecraft clock stop count in the SPICE
            clock kernel, in the clock partition that the supplemental index gives, else
            in the one where the count falls nearest EARTH_RECEIVED_TIME, else in the one
            where it falls within the periods in which the spacecraft's cameras took the
            VGISS images, because the count recurs about every six years. The count
            is SPACECRAFT_CLOCK_STOP_COUNT, else IMAGE_NUMBER, else the FDS count with
            which the name of the image file begins, such as C3450201 for FDS 34502.01.
            For the VGISS raw images whose labels give both, the count's time is a
            median of 0.9 s after STOP_TIME, but whole FDS minutes of 48 s after it for
            some of them and up to 7,625 s before it for a few. The planet is that of
            MISSION_PHASE_NAME, else that of the encounter in progress at the time. A
            geometrically corrected image has a 1000x1000 FOV and every other product an
            800x800 one.

        Raises:
            oops.OopsSpiceError: If no source read gives a time and the clock kernel
                cannot convert the clock count, or cannot place it in one partition, with
                a message of the form "no Voyager 1 clock time for FDS count
                34502:01:001 for <image file>: <SPICE error code>"; or if `frame` is not
                given and the SPICE kernels cannot supply the image's pointing: the C
                kernels lack a record for its spacecraft clock stop count, the clock
                kernel cannot encode the count, or the frame kernel does not connect the
                scan platform to the camera. Its message has the form "no Voyager 1 ISS
                NAC pointing at <time> for <image file>: <SPICE error code>", in which
                the time is that at which the Snapshot evaluates its frame: the midtime
                after any `cadence` override, moved by any `timeshift`, once for "T" and
                once more for "F"; its cause is the exception that SPICE raised.
            oops.host.HostFormatError: If no source read gives the spacecraft, the
                camera or the exposure; neither a time nor a clock count; or, unless
                `frame` is given, no spacecraft clock stop count. The message names the
                missing items and the file. Also if the image file must be read and no
                file path is known for the parsed content given.
            oops.host.PdsHostError: If the PDS3 label cannot be parsed by any method, or
                lacks an IMAGE pointer.
            oops.host.VicarHostError: If the VICAR file cannot be read as an image: its
                header cannot be parsed, its data are cut short, or it holds a table
                rather than an image, as the RESLOC and GEOMA files do.
            oops.host.WrongHostError: If the file, or the parsed content given, is not a
                Voyager ISS image or label.
            oops.OopsValueError: If `select` is not None, or if `pds3_options` holds an
                option that the PDS3 label parser does not accept while the method parses
                a PDS3 label file; the options are not checked for a VICAR file or for a
                Pds3Label passed in.
            OSError: If the file, or unless `astrometry` is True the image file a PDS3
                label names, cannot be read.
        """

        oops.Host._check_select(select, unit='Voyager ISS image file')

        VoyagerISS.initialize()     # define everything the first time through

        # The parser's source text of each value is never wanted, in label_dict or
        # anywhere else
        pds3_options = (oops.Host._pds3_options_with_defaults(pds3_options)
                        | {'sources': False})
        parsed = VoyagerISS._parse_fileinfo(fileinfo, astrometry=astrometry,
                                            pds3_options=pds3_options)

        # Given a PDS3 label, the image file it names is the observation's file, and it
        # is read too unless only timing and astrometry are needed. Given the image file,
        # it alone is read. A VicarImage, read here or given, has already replaced the
        # HOST and BHOST of its header with the reading machine, so the header is taken
        # from the file as written whenever the VicarImage has a file path, whether or
        # not the data are wanted.
        image = None
        header = None
        if isinstance(parsed, Pds3Label):
            label = parsed
            filepath = VoyagerISS._image_path(label, find=not astrometry)
        else:
            label = None
            filepath = None if parsed.filepath is None else FCPath(parsed.filepath)
            image = VoyagerISS._check_image_type(parsed, filepath)
            header = VoyagerISS._header_as_written(image)

        # Read the data, if needed, where the VICAR header places them, whichever file
        # was given; the labels of some GEOMED products place the image a record too
        # early or too late, such as C2673426_GEOMED of VGISS_7205 and C3489611_GEOMED
        # of VGISS_6112. The image file has a local path, `abspath`, only if this call
        # retrieved it to read the data: a VicarImage that the caller gives was read
        # elsewhere.
        data = None
        abspath = None
        if not astrometry:
            if not isinstance(image, VicarImage):
                if filepath is None:
                    raise oops.host.HostFormatError('no file path is known for the '
                                                    'Voyager ISS content given, so its '
                                                    'image file cannot be read')
                (image, abspath) = VoyagerISS._read_vicar_image(filepath)
                image = VoyagerISS._check_image_type(image, filepath)
            elif isinstance(fileinfo, (str, pathlib.Path, FCPath)):
                # oops read the image file through the cache; this finds its copy there
                abspath = filepath.retrieve()
            data = image.data_2d
            if header is None:
                header = VoyagerISS._header_as_written(image)

        # Consumers write the dictionaries out as JSON, and a header's processing history
        # repeats its TASK, USER and DAT_TIM parameters (#18). A label that
        # oops.Host.from_file() parsed arrives with its "_source" entries, which
        # _plain_label_dict() leaves out with the parser's other entries.
        label_dict = None if label is None else _plain_label_dict(label.as_dict())
        header_dict = None if header is None else _plain_header_dict(header)
        merged = VoyagerISS._merge_items(
            {} if label is None else _without_nulls(label_dict),
            {} if header is None else VoyagerISS._header_items(header))
        unconverted = {key: value for (key, value) in (header_dict or {}).items()
                       if key not in _CONVERTED_HEADER_LINES}
        dict_ = _without_nulls(unconverted | merged)

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
                                         abspath=abspath, sources=sources,
                                         return_all_planets=return_all_planets,
                                         check_pointing=True, overrides=overrides)

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
            oops.host.PdsHostError: If the PDS3 label cannot be parsed by any method.
            oops.host.VicarHostError: If the VICAR file cannot be read: its header cannot
                be parsed or, unless `astrometry` is True, its data are cut short.
            oops.host.WrongHostError: If the file, or the parsed content given, is not a
                Voyager ISS image or label.
            oops.OopsValueError: If `pds3_options` holds an option, or names a method,
                that the PDS3 label parser does not accept.
            OSError: If the file cannot be read.
        """

        if not isinstance(fileinfo, (str, pathlib.Path, FCPath)):
            return VoyagerISS._check_parsed(fileinfo)

        # The fast parser rejects some valid labels, such as C2673426_GEOMED.LBL of
        # VGISS_7205, in which the quoted value of NOTE starts on the next line, and the
        # strict parser rejects some that "loose" reads, so oops tries each later method
        # in turn. A VICAR file whose header or data cannot be read raises an error that
        # names it (#17). The fast parser also reads the quoted EDIT_MODE_ID "1:32" as a
        # time, which _plain_label_dict() turns back into the text. SETI/rms-pdsparser#29
        try:
            return VoyagerISS._read_fileinfo(fileinfo, formats='PV',
                                             astrometry=astrometry,
                                             pds3_options=pds3_options,
                                             pds3_fallback=True)
        except oops.host.VicarHostError as err:
            raise oops.host.VicarHostError(f'cannot parse the VICAR header of '
                                           f'{fileinfo}: {err}') from err
        except (oops.host.HostError, oops.OopsValueError):  # ValueErrors kept as they are
            raise
        except ValueError as err:   # numpy, for an array cut short
            raise oops.host.VicarHostError(f'cannot read the VICAR image {fileinfo}: '
                                           f'{err}') from err

    @staticmethod
    def _check_parsed(parsed):
        """Parsed content given to :meth:`from_file`, checked to be this host's.

        Parameters:
            parsed (Pds3Label | VicarLabel | VicarImage): The parsed content.

        Returns:
            Pds3Label | VicarLabel | VicarImage: `parsed`.

        Raises:
            oops.host.WrongHostError: If `parsed` is not a Voyager ISS label or header.
        """

        name = (f'{type(parsed).__name__} without a file path' if parsed.filepath is None
                else parsed.filepath)
        if isinstance(parsed, Pds3Label):
            if not VoyagerISS._detect_in_pds3(parsed):
                raise oops.Host._no_host_error([VoyagerISS], 'PDS3 label', name)
        elif not VoyagerISS._detect_in_vicar(parsed):
            raise oops.Host._no_host_error([VoyagerISS], 'VICAR file', name)
        return parsed

    @staticmethod
    def _check_image_type(vic, filepath):
        """A VICAR header, checked to be that of an image.

        Parameters:
            vic (VicarLabel | VicarImage): The VICAR header.
            filepath (FCPath | None): The file, for the message.

        Returns:
            VicarLabel | VicarImage: `vic`.

        Raises:
            oops.host.VicarHostError: If the header's TYPE is not "IMAGE", as for the
                tables in the RESLOC and GEOMA files.
        """

        if vic.get('TYPE', _VICAR_IMAGE_TYPE) != _VICAR_IMAGE_TYPE:
            raise oops.host.VicarHostError(f'Voyager ISS VICAR file {filepath} is not an '
                                           f'image: TYPE is {vic["TYPE"]}')
        return vic

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

        Raises:
            oops.host.PdsHostError: If the label has no IMAGE pointer, as the labels of
                the RESLOC and GEOMA tables have none.
        """

        if '^IMAGE' not in label:
            raise oops.host.PdsHostError(f'Voyager ISS label lacks an ^IMAGE pointer: '
                                         f'{label.filepath}')
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
        """The VICAR image and header in an image file, and the file's local path.

        Parameters:
            filepath (FCPath): The path to the image file.

        Returns:
            tuple[VicarImage, pathlib.Path]: The image with its header, and the local
            path of the file, which a remote file is retrieved to.

        Raises:
            oops.host.VicarHostError: If the header cannot be parsed or the data are cut
                short.
            OSError: If the file cannot be read.
        """

        local = filepath.retrieve()
        try:
            return (VicarImage(local, strict=False), local)
        except VicarError as err:
            raise oops.host.VicarHostError(f'cannot parse the VICAR header of '
                                           f'{filepath}: {err}') from err
        except ValueError as err:   # numpy, for an array cut short
            raise oops.host.VicarHostError(f'cannot read the VICAR image {filepath}: '
                                           f'{err}') from err

    @staticmethod
    def _header_as_written(vic):
        """The VICAR header of an image file as the file gives it.

        Reading the image array sets the header's HOST, and BHOST if there is a binary
        header, to the machine that reads it, so a VicarImage read from a file no longer
        gives the machine that wrote the file, as its VicarLabel does.

        Parameters:
            vic (VicarLabel | VicarImage): The VICAR header.

        Returns:
            VicarLabel | VicarImage: The header of the file of a VicarImage read from a
            file, read again without its array; otherwise `vic`.

        Raises:
            OSError: If the file cannot be read.
        """

        if not isinstance(vic, VicarImage) or vic.filepath is None:
            return vic
        # The same parser has just read this header, as part of the VicarImage. The
        # second read stays until rms-vicar keeps the header as the file writes it, or
        # gives the VicarLabel it parsed. SETI/rms-vicar#34
        return VicarLabel(vic.filepath, strict=False)

    @staticmethod
    def _if_scale(header):
        """The factor that converts a calibrated product's data to I/F.

        Parameters:
            header (VicarLabel | VicarImage): The VICAR header of the image.

        Returns:
            float | None: The factor from the header line "FOR (I/F)*10000., MULTIPLY DN
            VALUE BY x", which FICOR77 writes as LABEL3, so x / 10000; None if the
            header has no such line, or its x is not a number, such as N/A.
        """

        for value in header.as_dict(unique=False).values():
            match = _IF_LINE.search(value) if isinstance(value, str) else None
            if match:
                return float(match['scale']) / _IF_LINE_UNITS
        return None

    @staticmethod
    def from_index(filepath, *, supplement=oops.Host.SUPPLEMENT, label=None,
                   row_dicts=None, pds3_options=None, filter=None,
                   return_all_planets=False, select=None, target=None, lightsource=None,
                   path=None, frame=None, cadence=None, fov=None, calibrations=None,
                   timeshift=None, navigation=None, tracker=None, parallel=None):
        """An iterator over Snapshots, one for each row of a Voyager ISS raw image index.

        It reads a Ring-Moon Systems Node metadata raw image index and its supplement.
        The call checks the arguments and reads the labels of the index and its
        supplement; only for an index label without DATA_SET_ID, which every VGISS
        metadata index label gives, does it also read the first row, to check that the
        index is Voyager ISS. The iteration then reads the rows of both tables a few at a
        time, in step, and builds each row's Snapshot when it reaches that row, so the
        tables are never held in memory whole.

        With `row_dicts`, the given rows take the place of the tables, and the call leaves
        both labels unread. The errors below that come from reading a label or a table
        are then never raised: the labels need not exist, the index need not be Voyager
        ISS, and `pds3_options` is not checked. What is still checked is `select`, the
        name of `filepath` when `supplement` is the default, `filter` against the first
        row, and each row when the iteration reaches it.

        Parameters:
            filepath (str | pathlib.Path | FCPath): The path to the PDS label of a VGISS
                raw image index, such as "VGISS_6109_raw_image_index.lbl", which has one
                row per raw image, as the PDS Ring-Moon Systems Node metadata holds it.
                The index of every product, "VGISS_6109_index.lbl", is not accepted.
            supplement (str | pathlib.Path | FCPath | object, optional): The path to the
                PDS label of the supplemental index, whose columns are merged into each
                row; a raw image index needs its supplement. Its rows must list the same
                files as the index's, in the same order, as every VGISS supplement does:
                each index row is merged with the supplement row in the same position.
                Where both tables give a column, the index value is kept. The default,
                `oops.Host.SUPPLEMENT`, reads the file in the directory of `filepath`
                whose name matches except that "_raw_image_index" is replaced by
                "_supplemental_index" in upper or lower case.
            label (Pds3Label, optional): The PDS label of `filepath` if it has already
                been read.
            row_dicts (collections.abc.Iterable[dict[str, Any]], optional): The rows of
                the index already merged with the supplement, one dictionary per row, or
                some of those rows. If they are given, they are used as they are, in place
                of reading the tables and their labels, and each is filtered and built
                when the iteration reaches it. A row read from the index without the
                supplement lacks STOP_TIME and the clock partition, so its time is the
                index's IMAGE_TIME, to the second.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser. They
                are checked when the call parses the index label, which it does only if
                neither `label` nor `row_dicts` is given.
            filter (dict[str, Any], optional): A dictionary of index column names and
                their values or a set or tuple of values. If provided, only rows of the
                index in which each named column equals the value, falls in the set of
                values, or falls in a range defined by a tuple of two values, will be
                included.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            select (None): Must be None. Each row describes a single observation, so
                there is nothing to select from; any other value raises an error.
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

        Returns:
            collections.abc.Iterator[Snapshot]: One raw 800x800 observation per filtered
            row of the index, in index order, each with subfields `dict`, `host`,
            `instrument`, `detector`, `planet`, `spacecraft`, `time_from_sclk`,
            `spice_kernels`, `filepath`, `basename`, `image_url`, `spice_to_frame`,
            `spice_frame_name` and `spice_frame_id` inserted, and `filter`,
            `label_target`, `spacecraft_clock_start_count` and
            `spacecraft_clock_stop_count` when available, as described for
            :meth:`from_file`. A Snapshot never has `abspath`, because reading an index
            does not retrieve the image files.

            `dict` is the row, merged with its row of the supplement, without the columns
            whose values mean unknown, as :meth:`from_file` lists them, the indexes'
            EXPOSURE_DURATION of -99.9999 included, and with each value a Python str,
            int, float or bool. The labels of the raw image indexes give the
            EXPOSURE_DURATION column two bytes fewer than the table holds; the column is
            read at its full width, so that an exposure of 0.0075 s is not read as 0.0 s.
            A row without a time gets one from its spacecraft clock stop count, as
            :meth:`from_file` describes; the supplement gives every row its clock
            partition. `filepath`, `basename` and `image_url` name the raw image file,
            PRODUCT_ID, in the directory of the FILE_SPECIFICATION_NAME within the volume
            VOLUME_NAME, which is found from the location of `filepath`: for an index
            where the PDS Ring-Moon Systems Node holdings keep it,
            "<holdings>/metadata/VGISS_6xxx/VGISS_6109/", the volume is
            "<holdings>/volumes/VGISS_6xxx/VGISS_6109"; for an index anywhere else, it is
            the directory VOLUME_NAME beside the index. Reading an index never creates
            a directory. A row that raises an error ends the iteration, so the rows
            after it are reached only with a `filter` that leaves it out. The pointing
            is not checked: if `frame` is not given and the SPICE kernels cannot supply
            an image's pointing, evaluating its frame raises :class:`oops.OopsSpiceError`,
            as :meth:`from_file` describes.

            The tables are checked only for the errors listed below. If the table reader
            cannot read a value as the number its column describes, it issues a warning
            and gives the value as text, and the row is built with it. A row shorter
            than the label's row length is read as it is.

        Raises:
            oops.OopsSpiceError: If a row lacks a time and the clock kernel cannot
                convert its clock count, as :meth:`from_file` describes; raised when the
                iteration reaches that row.
            ValueError: When the iteration reaches a row that gives as text a number the
                Snapshot needs, such as an EXPOSURE_DURATION that holds a letter.
            oops.OopsTypeError: When the iteration reaches the row, if a range in
                `filter` cannot be compared with the row's value.
            oops.OopsValueError: If `select` is not None, `filter` uses a column that
                neither table has, or `pds3_options` holds an option that the PDS3 label
                parser does not accept while neither `label` nor `row_dicts` is given.
                With `row_dicts`, `filter` is checked against the
                first row, when the call is made if `row_dicts` is a sequence and
                otherwise when the iteration reads that row.
            oops.host.HostFormatError: If a row lacks the exposure, the camera or the
                spacecraft; both the time and the clock count; or, unless `frame` is
                given, the spacecraft clock stop count. The message names the missing
                item and the image file. It is raised when the iteration reaches that
                row.
            FileNotFoundError: If the supplement's label does not exist, when the call is
                made; if a table file does not exist, when the iteration starts.
            oops.host.HostIndexError: When the call is made: if `supplement` is None or
                an empty string while `row_dicts` is not given; if `supplement` is
                `oops.Host.SUPPLEMENT` and the name of `filepath` lacks
                "_raw_image_index", also when `row_dicts` is given; or if a label does
                not describe a table that can be read, as when it lacks the table it
                points to. During the iteration, only if `row_dicts` is not given,
                counting rows from 0: at a row where the index row and the supplement row
                in the same position give different files; at the row where the
                supplement ends before the index; after the last row a table holds, if it
                holds fewer rows than its label says; at the first of the 100 rows read
                with a row longer than the label's row length, none of which is yielded;
                and after the last row, if the supplement has more rows than the index or
                a table more rows than its label says.
            oops.host.PdsHostError: If the label of `filepath` or of the supplement cannot
                be parsed.
            oops.host.WrongHostError: If `filepath` is not a Voyager ISS index.
            OSError: If the label of `filepath` or of a `supplement` that is specified
                cannot be read.
        """

        oops.Host._check_select(select, unit='row of a Voyager ISS raw image index')

        # The supplement is required, so None and the empty string, which oops takes to
        # mean "read the index alone", are refused
        filepath = FCPath(filepath)
        no_supplement = supplement is None or (isinstance(supplement, str)
                                               and not supplement)
        if no_supplement and row_dicts is None:
            raise oops.host.HostIndexError(f'Voyager ISS raw image index needs its '
                                           f'supplemental index: {filepath}')

        VoyagerISS.initialize()

        # The rows are read, paired by position with the supplement's, and filtered as
        # the iteration proceeds; the call reads only the labels, and the first row only
        # if the index label lacks DATA_SET_ID. Given rows are used as they are:
        # _index_rows() returns them before it reads either label, so the label checks
        # are not made. With the default supplement, the name of the index is still
        # checked, below.
        if row_dicts is None:
            label = VoyagerISS._index_label(filepath, label, pds3_options)
        rows = VoyagerISS._index_rows(filepath, supplement=supplement, label=label,
                                      row_dicts=row_dicts, pds3_options=pds3_options,
                                      filter=filter)
        if row_dicts is not None and supplement is oops.Host.SUPPLEMENT:
            VoyagerISS._supplement_path(filepath)

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

        # Build each row's Snapshot when the iteration reaches it. Every row gives a
        # Snapshot or raises; an image whose pointing SPICE cannot supply is kept, and
        # raises only when its frame is evaluated (#16). A row that raises ends the
        # iteration: C3940530 of VGISS_6201 does so for lack of an exposure (#29), and
        # whether an iteration should go on past such a row is SETI/rms-oops#249.
        return (VoyagerISS._row_snapshot(row_dict, filepath, return_all_planets,
                                         overrides)
                for row_dict in rows)

    @staticmethod
    def _row_snapshot(row_dict, index, return_all_planets, overrides):
        """The Snapshot of one row of a raw image index.

        Parameters:
            row_dict (dict[str, Any]): The row, merged with its supplement row.
            index (FCPath): The path to the label of the raw image index.
            return_all_planets (bool): Include kernels for all planets, not just the
                planet of the encounter.
            overrides (dict[str, Any]): The values of the standard overrides.

        Returns:
            Snapshot: The observation, as :meth:`from_index` describes it.

        Raises:
            oops.host.HostFormatError: If the row lacks an item that the Snapshot needs.
            oops.OopsSpiceError: If the row lacks a time and the clock kernel cannot
                convert its clock count.
        """

        row_dict = _plain_row(row_dict)
        return VoyagerISS._make_snapshot(_without_nulls(row_dict),
                                         filepath=VoyagerISS._index_image_path(index,
                                                                               row_dict),
                                         return_all_planets=return_all_planets,
                                         check_pointing=False, overrides=overrides)

    @staticmethod
    def _index_image_path(index, row_dict):
        """The image file that a row of a raw image index describes.

        Parameters:
            index (FCPath): The path to the label of the raw image index.
            row_dict (dict[str, Any]): The row.

        Returns:
            FCPath: The raw image file, PRODUCT_ID, in the directory of the row's
            FILE_SPECIFICATION_NAME within its volume, VOLUME_NAME. The volume is found
            from the location of the index: for an index where the PDS Ring-Moon Systems
            Node holdings keep it, "<holdings>/metadata/VGISS_6xxx/VGISS_6109/", the
            volume is "<holdings>/volumes/VGISS_6xxx/VGISS_6109"; for an index anywhere
            else, it is the directory VOLUME_NAME beside the index.
        """

        label = oops.Host._index_file_path(oops.Host._volumes_directory(index), row_dict,
                                           volume_column='VOLUME_NAME')
        return label.with_name(str(row_dict['PRODUCT_ID']).strip())

    @classmethod
    def _supplement_path(cls, filepath):
        """The supplemental index beside a raw image index.

        The supplement of "VGISS_6109_raw_image_index.lbl" is
        "VGISS_6109_supplemental_index.lbl", which the default rule of
        :meth:`oops.Host._supplement_path` would not find.

        Parameters:
            filepath (FCPath): The path to the label of the raw image index.

        Returns:
            FCPath: The path to the label of the supplemental index, in the same
            directory, with "_raw_image_index" in its name replaced by
            "_supplemental_index", in lower or upper case.

        Raises:
            oops.host.HostIndexError: If the name of `filepath` contains no
                "_raw_image_index", as for the index of every product,
                "VGISS_6109_index.lbl".
        """

        name = filepath.name
        for (index, supp) in _SUPPLEMENT_NAMES:
            if index in name:
                return filepath.with_name(name.replace(index, supp))
        raise oops.host.HostIndexError('Voyager ISS needs a raw image index, '
                                       f'"VGISS_xxxx_raw_image_index.lbl": {filepath}')

    @staticmethod
    def _index_label(filepath, label, pds3_options):
        """The content of a raw image index label, with each column as wide as its FORMAT.

        Parameters:
            filepath (FCPath): The path to the label.
            label (Pds3Label | None): The parsed label, if it has been read.
            pds3_options (dict[str, Any] | None): The options for the PDS3 label parser.

        Returns:
            dict[str, Any]: The label as a dictionary, in which the BYTES of each COLUMN
            whose FORMAT, such as "F9.4", is wider are the FORMAT's width. A column whose
            BYTES already match its FORMAT keeps them.

        Raises:
            oops.host.PdsHostError: If the label cannot be parsed.
            oops.OopsValueError: If `pds3_options` holds an option that the parser does
                not accept.
            OSError: If the label cannot be read.
        """

        label = VoyagerISS._read_index_label(filepath, label=label,
                                             pds3_options=pds3_options)
        contents = label.as_dict()      # a copy
        for table in contents.values():
            if not isinstance(table, dict):
                continue
            for column in table.values():
                if not isinstance(column, dict) or 'FORMAT' not in column:
                    continue
                match = _FORMAT_WIDTH.match(column['FORMAT'])
                if match and int(match['width']) > column['BYTES']:
                    column['BYTES'] = int(match['width'])
        return contents

    @staticmethod
    def _make_snapshot(dict_, filepath, data=None, *, abspath=None, sources=None,
                       return_all_planets=False, check_pointing, overrides):
        """A Snapshot built from merged metadata or a row of an index.

        Parameters:
            dict_ (dict): The metadata merged from the PDS3 label and the VICAR header, or
                an index row, in either case without null values.
            filepath (FCPath | str | None): The path to the image file; None if it is
                not known.
            data (numpy.ndarray, optional): The image array; None to omit the `data`
                subfield.
            abspath (pathlib.Path, optional): The local path of the image file, given
                only when this call retrieved the file to read `data`; None to omit the
                `abspath` subfield.
            sources (dict[str, Any], optional): The values of the subfields `label_dict`,
                `header_dict` and `if_scale`, each None if it is absent.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            check_pointing (bool): True to require, if `frame` is not overridden, that
                the SPICE kernels supply the pointing; False to leave a frame that raises
                when it is evaluated.
            overrides (dict[str, Any]): The values of the standard overrides, `target`,
                `lightsource`, `path`, `frame`, `cadence`, `fov`, `calibrations`,
                `timeshift`, `navigation`, `tracker` and `parallel`, as described for
                :meth:`from_file`.

        Returns:
            Snapshot: The observation, with the subfields listed for :meth:`from_file`.

        Raises:
            oops.host.HostFormatError: If `dict_` lacks the spacecraft, the camera or
                the exposure; both the time and the clock count; or, if `frame` is not
                overridden, the clock stop count.
            oops.OopsSpiceError: If `dict_` lacks a time and the clock kernel cannot
                convert the clock count or place it in one partition; or if
                `check_pointing` is True, `frame` is not overridden and the SPICE kernels
                cannot supply the pointing, as described for :meth:`from_file`.
        """

        name = dict_.get('PRODUCT_ID', '') if filepath is None else filepath
        vgr = VoyagerISS._spacecraft(dict_, name)
        camera = VoyagerISS._camera(dict_, name)
        geomed = dict_.get('PRODUCT_TYPE') == _GEOMED
        texp = float(VoyagerISS._required(dict_, 'EXPOSURE_DURATION', name))
        (tstop, time_from_sclk) = VoyagerISS._tstop(dict_, vgr, filepath, name)

        # The planet of the encounter, which the label and index name in
        # MISSION_PHASE_NAME and the VICAR header gives only through its time
        if 'MISSION_PHASE_NAME' in dict_:
            planet = dict_['MISSION_PHASE_NAME'].split()[0]
        else:
            planet = _Voyager.encounter(tstop)

        # A calibrated product's data times if_scale are I/F (#22)
        sources = sources or {}
        calibrations = []
        if sources.get('if_scale') is not None:
            calibrations = [oops.calibration.FlatCalib('I/F', sources['if_scale'])]

        params = {
            'cadence'     : oops.cadence.SnapCadence(tstop - texp, texp),
            'fov'         : VoyagerISS._FOVS[camera, geomed],
            'path'        : _Voyager.PATH_IDS[vgr],
            'frame'       : None,
            'lightsource' : 'SUN',
            'calibrations': calibrations,
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
            params['frame'] = VoyagerISS._camera_frame(dict_, vgr, camera, tstop,
                                                       filepath, name)

        # This sets the path subfields of the image file too, `abspath` only if given;
        # oops never creates a directory here
        oops.Host._apply_overrides(params, filepath, abspath=abspath, **overrides)

        # A frame without pointing raises OopsSpiceError when it is evaluated, so evaluate
        # the frame as the observation will, after the overrides. The check of oops,
        # oops.Host._check_spice_frame, is for a frame that reads the C kernels when it is
        # evaluated; this one read them when it was built, at the image's own clock count,
        # and given the SpiceErrorFrame the check would wrap its error in another one.
        if using_cks and check_pointing:
            frame = oops.Frame.as_frame(params['frame'])
            frame.wrt(oops.Frame.J2000).transform_at_time(params['cadence'].midtime)

        obs = oops.observation.Snapshot(('v', 'u'), **params)
        if data is not None:
            obs.insert_subfield('data', data)

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
        obs.insert_subfield('time_from_sclk', time_from_sclk)
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
    def _required(dict_, key, name):
        """The value of an item that the Snapshot cannot be built without.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            key (str): The name of the item.
            name (FCPath | str): The image file or product, for the message.

        Returns:
            Any: The value.

        Raises:
            oops.host.HostFormatError: If `dict_` does not give the item.
        """

        if key not in dict_:
            raise oops.host.HostFormatError(f'Voyager ISS image {name} has no {key}')
        return dict_[key]

    @staticmethod
    def _spacecraft(dict_, name):
        """The spacecraft number, 1 or 2, from INSTRUMENT_HOST_NAME or SPACECRAFT_NAME.

        Raises:
            oops.host.HostFormatError: If `dict_` gives neither.
        """

        for key in ('INSTRUMENT_HOST_NAME', 'SPACECRAFT_NAME'):
            if key in dict_:
                return int(dict_[key][-1])
        raise oops.host.HostFormatError(f'Voyager ISS image {name} has no '
                                        'INSTRUMENT_HOST_NAME or SPACECRAFT_NAME')

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
    def _tstop(dict_, vgr, filepath, name):
        """The close of the shutter in seconds TDB.

        STOP_TIME, which a label and the supplemental index give to 1/100 s, is used if
        known; otherwise IMAGE_TIME, which the index gives to the second and which is
        otherwise the same. If neither is known, the time is that of the image's
        spacecraft clock stop count (see :meth:`_clock_count`) in the clock kernel: in
        the clock partition SPACECRAFT_CLOCK_PARTITION_NUMBER, which the supplemental
        index gives, or else in the partition where the count falls nearest
        EARTH_RECEIVED_TIME, because the FDS count recurs about every six years, or else
        in the one partition where it falls within the periods in which the spacecraft's
        cameras took the VGISS images (`_Voyager.IMAGING_PERIODS`). The
        clock kernel converts the count to TDB at the spacecraft, the same time scale as
        the labels' times, which are UTC at the spacecraft: for the 76,635 VGISS raw
        images that give both, the count's time is a median of 0.9 s after STOP_TIME
        and within 3 s of it for 79% of them. For most of the rest it is whole FDS
        minutes of 48 s later, because a count can be stamped minutes after the shutter
        closed, and for 12 it is more than a second earlier, by up to 7,625 s.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            vgr (int): The spacecraft number.
            filepath (FCPath | str | None): The image file, named by the error of a
                count that the clock kernel cannot convert; None if it is not known.
            name (FCPath | str): The image file or product, for the message.

        Returns:
            tuple[float, bool]: The time, and True if it came from the clock count.

        Raises:
            oops.host.HostFormatError: If `dict_` lacks both the time and the clock count.
            oops.OopsSpiceError: If the time comes from the clock count and the clock
                kernel cannot convert it, or, without a partition or an earth received
                time, the count is not in exactly one partition within the imaging
                periods.
        """

        for key in ('STOP_TIME', 'IMAGE_TIME'):
            if key in dict_:
                return (julian.tdb_from_tai(julian.tai_from_iso(dict_[key])), False)

        # The labels of 17 raw images give UNK for every time, and so do their index rows
        # until the index is corrected. The labels keep it, so their time comes from the
        # clock count. A corrected index row gives a time, which is used as it is.
        # SETI/rms-data-projects#260 item 2
        count = VoyagerISS._clock_count(dict_, name)
        if count is None:
            raise oops.host.HostFormatError(f'Voyager ISS image {name} has no STOP_TIME, '
                                            'IMAGE_TIME, SPACECRAFT_CLOCK_STOP_COUNT or '
                                            'IMAGE_NUMBER')
        partition = dict_.get('SPACECRAFT_CLOCK_PARTITION_NUMBER')
        near = None
        if partition is None and 'EARTH_RECEIVED_TIME' in dict_:
            near = julian.tdb_from_tai(julian.tai_from_iso(dict_['EARTH_RECEIVED_TIME']))

        try:
            ticks = _Voyager.sclk_ticks(vgr, count, partition, near=near)
            tstop = cspyce.sct2e(_Voyager.SPACECRAFT_IDS[vgr], ticks)
        except oops.OopsSpiceError.CSPYCE_ERRORS as err:
            if not _is_spice_error(err):
                raise
            raise oops.OopsSpiceError.from_spice_error(
                err, description=f'no Voyager {vgr} clock time for FDS count {count}',
                filepath=filepath) from err
        return (tstop, True)

    @staticmethod
    def _clock_count(dict_, name):
        """The image's spacecraft clock stop count, "hhhhh:mm:ddd".

        A label, a VICAR header and the supplemental index give
        SPACECRAFT_CLOCK_STOP_COUNT. Otherwise it is derived from IMAGE_NUMBER, the FDS
        count "hhhhh.mm" of the image, which matches the stop count for all but five of
        the 76,652 VGISS raw images, and otherwise from the name of the image file,
        which begins with the same count, such as C3450201 for FDS 34502.01. The name
        is the only source of the count for the 11 VICAR headers that give the fill
        value FDS 00000.00 or 0.00.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            name (FCPath | str): The image file or product.

        Returns:
            str | None: The count; None if no source gives it.
        """

        if 'SPACECRAFT_CLOCK_STOP_COUNT' in dict_:
            return dict_['SPACECRAFT_CLOCK_STOP_COUNT']
        if 'IMAGE_NUMBER' in dict_:
            (hours, minutes) = f'{float(dict_["IMAGE_NUMBER"]):.2f}'.split('.')
            return f'{int(hours)}:{minutes}:001'

        match = _NAME_COUNT.match(FCPath(name).name) if name else None
        if match is None:
            return None
        return f'{int(match["hours"])}:{match["minutes"]}:001'

    @staticmethod
    def _camera_frame(dict_, vgr, camera, tstop, filepath, name):
        """The fixed camera frame built from the image's own C-kernel record.

        Parameters:
            dict_ (dict): The merged metadata or index row.
            vgr (int): The spacecraft number.
            camera (str): "NAC" or "WAC".
            tstop (float): The close of the shutter in seconds TDB, which selects the
                clock partition when `dict_` does not give it.
            filepath (FCPath | str | None): The image file, named by the error of a
                missing pointing; None if it is not known.
            name (FCPath | str): The image file or product, for the message of a missing
                item.

        Returns:
            oops.frame.Cmatrix | oops.frame.SpiceErrorFrame: An unregistered frame
            relative to J2000; if the SPICE kernels cannot supply the pointing (the clock
            kernel cannot encode the count, the C kernels lack a record for it, or the
            frame kernel does not connect the scan platform to the camera), a frame that
            raises :class:`oops.OopsSpiceError` when it is evaluated.

        Raises:
            oops.host.HostFormatError: If neither `dict_` nor the name of the image file
                gives the clock stop count.
        """

        count = VoyagerISS._clock_count(dict_, name)
        if count is None:
            raise oops.host.HostFormatError(f'Voyager ISS image {name} has no '
                                            'SPACECRAFT_CLOCK_STOP_COUNT or IMAGE_NUMBER')
        partition = dict_.get('SPACECRAFT_CLOCK_PARTITION_NUMBER')
        platform_id = _SCAN_PLATFORM_ID[vgr]
        try:
            ticks = _Voyager.sclk_ticks(vgr, count, partition, near=tstop)
            (j2000_to_platform, _) = cspyce.ckgp(platform_id, ticks, _TICK_TOLERANCE,
                                                 'J2000')
            platform_to_camera = cspyce.pxform(f'VG{vgr}_SCAN_PLATFORM',
                                               f'VG{vgr}_ISS{camera[0]}A', 0.)
        # Only missing SPICE data gives this frame; anything else propagates (#19)
        except oops.OopsSpiceError.CSPYCE_ERRORS as err:
            if not _is_spice_error(err):
                raise
            return oops.frame.SpiceErrorFrame(
                err, description=f'no Voyager {vgr} ISS {camera} pointing',
                filepath=filepath)

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
            FILTER_NUMBER, TARGET_NAME, MISSION_PHASE_NAME and EARTH_RECEIVED_TIME. An
            item whose field is missing, filled with asterisks, or not valid is left out.
            The times are ISO 8601 strings in the label's forms, such as
            "1980-10-29T05:57:22.000" for IMAGE_TIME and STOP_TIME and
            "1980-10-29T07:35:06" for EARTH_RECEIVED_TIME, and the target and filter are
            spelled as a label spells them.
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

        scet = _header_time(_LAB02_SCET.search(lab02), _SCET_FORMAT)
        if scet is not None:
            items['IMAGE_TIME'] = scet
            items['STOP_TIME'] = scet
            tstop = julian.tdb_from_tai(julian.tai_from_iso(scet))
            items['MISSION_PHASE_NAME'] = _Voyager.encounter(tstop) + ' ENCOUNTER'

        # LAB04 holds more than the earth received time, so it stays in `dict` too
        ert = _header_time(_LAB04_ERT.search(str(dict_.get('LAB04', ''))), _ERT_FORMAT)
        if ert is not None:
            items['EARTH_RECEIVED_TIME'] = ert

        if match := _LAB03_EXPOSURE.search(lab03):
            items['EXPOSURE_DURATION'] = float(match['exp']) / _MSEC
        if match := _LAB03_FILTER.search(lab03):
            items['FILTER_NAME'] = _filter_name(match['name'])
            items['FILTER_NUMBER'] = match['number']

        target = str(dict_.get('LAB05', ''))[_LAB05_TARGET].strip().replace('_', ' ')
        if target:
            items['TARGET_NAME'] = _HEADER_TARGET_SPELLINGS.get(target, target)

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
            resolution rule in `_RESOLUTION` names the source whose value is used.
        """

        merged = header_items | label_items
        for key in label_items.keys() & header_items.keys():
            if _RESOLUTION[key] == 'header':
                merged[key] = header_items[key]
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
        """Whether the given parsed index label describes this host's data.

        A VGISS metadata index label names the host and instrument only in its
        DATA_SET_ID, such as
        "VG1/VG2-S-ISS-2/3/4/6-PROCESSED-V1.0".

        Returns:
            bool | None: True if DATA_SET_ID names the Voyager ISS images; False if it
            names another data set; None if the label lacks DATA_SET_ID, so that the
            host is identified from the rows by :meth:`_detect_in_row`.
        """

        if 'DATA_SET_ID' not in label:
            return None
        return _ISS_DATA_SET.match(str(label['DATA_SET_ID'])) is not None

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

        Loads the mission and ISS kernels and constructs the FOVs. :meth:`from_file` and
        :meth:`from_index` call it with the default values; call it before them only to
        choose other values. After the first call, later calls to this function are
        ignored.

        Parameters:
            planets (int | collections.abc.Sequence[int], optional): The number of a
                planet, from 1 for Mercury to 9 for Pluto, or a sequence of such numbers,
                such as (6,) for Saturn alone, to pass to
                :meth:`~oops.Body.define_solar_system`, which defines each planet named
                and its moons. None or 0 means all.
            asof (str, optional): A date in ISO format. Each kernel is chosen from
                those released no later than the start (00:00) of that date, the leap
                seconds kernel included, and a query of which every kernel was released
                after the start of the date silently uses the kernel it would choose
                without a date. A set of C kernels keeps only those released by the
                start of the date, so for any date up to and including 2007-06-05 the
                cameras' pointing is left out. The C kernels never fall back, and
                ValueError is raised instead. The merged cruise trajectories are left out
                for any date up to and including 2013-01-25. None to choose the newest
                kernels.
            irregulars (bool, optional): True to include the irregular satellites; False
                otherwise.

        Raises:
            RuntimeError: If Saturn is among the planets and `asof` is a date from
                2003-11-05 through 2004-04-26, because the frames kernel of Saturn's
                small moons chosen for those dates has a malformed line that SPICE
                rejects.
            ValueError: If none of a spacecraft's C kernels was released by the start
                of `asof`, as for any date before 1990-04-16.
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
