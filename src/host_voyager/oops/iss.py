##########################################################################################
# host_voyager/oops/iss.py
##########################################################################################
"""The Voyager ISS host, which makes a Snapshot from each image of the ISS cameras.

The :doc:`user guide </user_guide/user_guide>` describes the products it reads, how it
merges the two labels of each product, and the errors it raises.
"""

import collections
import datetime as dt
import re

import cspyce
import julian
import numpy as np

from filecache import FCPath
from vicar     import VicarImage, VicarLabel

import oops
from ._voyager import _Voyager

__all__ = ['VoyagerISS']

# The scan platform C kernels hold one pointing record per image, stamped at the image's
# own spacecraft clock stop count, which can be whole FDS minutes (48 s each) after the
# true close of the shutter, STOP_TIME. The record is therefore found by the count, never
# by the time. A lookup by time gives another image's record on 132 of the 691 rows of
# VGISS_6109, up to 0.053 rad off, and finds none on 486; C3450201 gets the record of
# C3450145. The tolerance is in clock ticks of 60 ms.
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

# The default PDS3 parse method. A full scan of every label of the current VGISS volumes
# found none that "strict" refuses, while "fast" refuses 29,725, such as C2673426_GEOMED
# of VGISS_7205, whose quoted NOTE starts on the line after the keyword, and reads the
# quoted EDIT_MODE_ID "1:32" as a time on 472 raw images. No label needs a correction of
# its text, so the host keeps the default _correct_label_text().
_PDS3_DEFAULTS = {'method': 'strict'}

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
# knows; without it, about 770 reads of an image file alone name no oops body (#15).
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
_MSEC = 1000.

# The target name occupies these columns of LAB05, with underscores for blanks
_LAB05_TARGET = slice(31, 43)

# The header lines whose fields are converted to PDS3 items; they appear in `dict` only
# under the names of those items, and as they are in `header_dict`
_CONVERTED_HEADER_LINES = ('LAB02', 'LAB03', 'LAB05')

# The two-digit years of a SCET: VGISS images date from 1978 to 1989. A garbled LAB02
# such as "SCET 08.272 77:05:04" is rejected by its year or its time of day.
_HEADER_YEARS = range(77, 100)
_HEADER_CENTURY = 1900

# The name of a Voyager image file begins with "C" and its FDS count, as in C3450201_RAW
# for FDS 34502.01
_NAME_COUNT = re.compile(r'C(?P<hours>\d{5})(?P<minutes>[0-5]\d)')

# The line of a calibrated product's VICAR header that gives its I/F scale, as in
#   LABEL3='FOR (I/F)*10000., MULTIPLY DN VALUE BY               1.00000'
# A factor that is not a number, such as N/A, is missing.
_IF_LINE = re.compile(r'FOR \(I/F\)\*10000\., MULTIPLY DN VALUE BY\s+'
                      r'(?P<scale>[-+]?(\d+\.?\d*|\.\d+)([Ee][-+]?\d+)?)\s*$')
_IF_LINE_UNITS = 10000.

# The corrections that VGISS_6110 DOCUMENT/TUTORIAL.TXT documents for the I/F of
# Voyager 1 images, which the calibration software that wrote the header's I/F line
# lacks: item 4, a factor of 3.345 for the solar distance at Saturn, since every Voyager 1
# Saturn image was calibrated as if at Jupiter; and item 3, factors of 40 for SODIUM and
# 12.5 for CH4_U, filters never calibrated for Voyager 1 and calibrated with GREEN
# instead. The labels' REFLECTANCE_SCALING_FACTOR includes them: a survey of every CALIB
# and GEOMED product found the label's factor 3.345, 40, 133.8 or 41.812 times the
# header's on exactly these images, and the brightest pixels of the 19 CH4_U and the
# SODIUM images agree with the label.
_V1_SATURN_IF_FACTOR = 3.345
_V1_FILTER_IF_FACTORS = {'SODIUM': 40., 'CH4_U': 12.5}

# What one read of a Voyager ISS product gives: the parsed PDS3 label and VICAR header,
# each None if it was not read; the image array, None if it was not read; and the paths
# of the image file and of the label, the latter None if the label was not read
_Files = collections.namedtuple('_Files', ['label', 'header', 'data', 'image_path',
                                           'label_path'])


def _without_nulls(dict_):
    """A copy of a dictionary without the keys whose values are PDS3 null texts.

    Parameters:
        dict_ (dict): A dictionary from a PDS3 label or a VICAR header.

    Returns:
        dict: A copy without every key whose value :func:`oops.host.is_pds3_null` accepts.
        A value that is itself a dictionary, such as the IMAGE object of a label, is
        copied the same way.
    """

    return {key: _without_nulls(value) if isinstance(value, dict) else value
            for (key, value) in dict_.items() if not oops.host.is_pds3_null(value)}


def _filter_name(text):
    """A filter name as a PDS3 label spells it.

    The LAB03 line of a VICAR header pads the name with blanks and writes "CH4/JS" and
    "CH4/U" for the label's "CH4_JS" and "CH4_U". On the image file path, 37 of 64
    products read had differed from their labels, such as C3448723_GEOMED.

    Parameters:
        text (str): The name as a LAB03 line gives it.

    Returns:
        str: The name, such as "CLEAR" or "CH4_JS".
    """

    return text.strip().replace('/', '_')


def _header_time(match):
    """The time of a SCET field of a VICAR header.

    Parameters:
        match (re.Match | None): The match of the field, with groups "year", "doy" and
            "hms"; None if the field did not match, as when it is filled with asterisks.

    Returns:
        datetime.datetime | None: The time, the type in which the PDS3 label parser gives
        a label's time; None if there is no match or it is not a valid time in the years
        of the mission.
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
    return time


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
    # the rows are paired by position. A survey of all 91 raw image indexes of the
    # metadata, the cumulative *999 indexes included, found every pair of tables equal in
    # length and in agreement on VOLUME_NAME, FILE_SPECIFICATION_NAME and PRODUCT_ID on
    # every row, 153,304 rows.
    MERGE_SUPPLEMENT_BY_FILE = False

    # The VGISS indexes name the volume in VOLUME_NAME and have no VOLUME_ID. The default
    # check of each pair of rows then compares PRODUCT_ID, the one other column the two
    # tables share, and every current Voyager index and its supplement pass that check.
    INDEX_FILE_KEY = ('VOLUME_NAME', 'FILE_SPECIFICATION_NAME')
    SUPPLEMENT_FILE_KEY = ('VOLUME_NAME', 'FILE_SPECIFICATION_NAME')

    _FOVS = {}
    _initialized = False

    @staticmethod
    def from_file(fileinfo, *, return_all_planets=False, pds3_options=None,
                  astrometry=False, select=None, target=None, lightsource=None,
                  path=None, frame=None, cadence=None, fov=None, calibrations=None,
                  timeshift=None, navigation=None, tracker=None, parallel=None):
        """A Snapshot object based on a given Voyager ISS image file or its label.

        Given the image file, only that file is read: its VICAR header supplies the
        metadata and, unless `astrometry` is True, the file supplies the data. Given the
        PDS3 label, the label is read and, unless `astrometry` is True, so is the image
        file that its IMAGE pointer names; the metadata are merged from the label and the
        image file's VICAR header. With `astrometry`, only the label is read and the
        image file need not exist. The data are always read through the VICAR file, where
        its header places them, whichever file is given.

        Parameters:
            fileinfo (str | pathlib.Path | FCPath | VicarLabel | VicarImage): The path to
                the PDS3 label (a name ending in ".LBL", in any case) or to the VICAR
                image file, or a VICAR header or image read from the image file, as
                :meth:`oops.Host.from_file` passes it, whose `filepath` names the file.
                Unless `astrometry` is True, a header is read again from that file for
                its array.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser. The
                method is "strict" unless they name another.
            astrometry (bool, optional): True to specify that the returned Observation
                will only be used for timing and astrometry. In this case, no data array
                is read and returned in the Observation.
            select (int | slice | str | tuple[int | slice | str, ...], optional): The
                selection that :class:`oops.Host` applies to the observations of a file.
                An image file holds one Snapshot, which None, the default, returns. Any
                other value is applied to that Snapshot alone, so an index, a slice or a
                tuple raises TypeError and a name raises :class:`oops.OopsValueError`.
            target (str | oops.Body, optional): Override for the default target Body of
                the observation. Use "NONE" for inertial pointing, indicating that no
                Solar System body was tracked.
            lightsource (oops.LightSource, optional): Override for the default LightSource
                of the observation.
            path (str | oops.Path, optional): Override for the Path of the observer.
            frame (str | oops.Frame, optional): Override for the Frame of the observing
                instrument, such as a :class:`~oops.frame.Cmatrix`, used as it is given.
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
                (0,0,0) as the input if you have no starting guess. For a pointing offset
                in pixels (du, dv), pass
                ``navigation=obs.fov.offset_angles_from_duv((du, dv))``.
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
            Snapshot: The observation, with the subfields that :meth:`from_index` lists
            and these:

            - `dict` is the metadata merged from every source read. The VICAR header's
              LAB02, LAB03 and LAB05 lines are converted to the PDS3 items
              INSTRUMENT_HOST_NAME, INSTRUMENT_ID, IMAGE_NUMBER,
              SPACECRAFT_CLOCK_STOP_COUNT, IMAGE_ID, IMAGE_TIME, STOP_TIME,
              EXPOSURE_DURATION, FILTER_NAME, FILTER_NUMBER and TARGET_NAME, with the
              types and spellings that a label gives them, such as a
              :class:`datetime.datetime` for a time and seconds for the exposure, and
              PRODUCT_TYPE comes from the processing tasks of the header. A field of the
              header filled with asterisks, a placeholder or garbage is left out. The
              three lines appear in `dict` only as these items; every other key of the
              header and of the label is kept under its own name. Where both give an
              item, the label's value is used; the header's is used where the label
              lacks the item. A key whose value is a PDS3 null text, as
              :func:`oops.host.is_pds3_null` defines it, is left out, also inside an
              OBJECT of the label.
            - `label_dict` is the PDS3 label as :meth:`~pdsparser.Pds3Label.as_dict`
              gives it, present only when the label was read.
            - `header_dict` is the VICAR header as :meth:`~vicar.VicarLabel.as_dict`
              gives it, present only when the image file was read.
            - `data` is the image array, unless `astrometry` is True.
            - `if_scale` is the label's REFLECTANCE_SCALING_FACTOR when the label was
              read and gives one. Otherwise, it is the factor x of the VICAR header's
              line "FOR (I/F)*10000., MULTIPLY DN VALUE BY x", which only CALIB and
              GEOMED products have, divided by 10000, and for a Voyager 1 image also
              multiplied by 3.345 at Saturn, by 40 for the SODIUM filter and by 12.5 for
              the CH4_U filter. It is absent if neither gives a factor.
            - `filepath` and `basename` name the image file, whichever file is given;
              `image_url` is its absolute path or URL. `image_abspath`, its path on the
              local disk, is present when the image file was read. `label_url` and
              `label_abspath` name the label when it was read.

        Raises:
            oops.host.HostFormatError: If no source read gives a spacecraft clock count
                and the name of the image file does not begin with one, or if no source
                read gives a time and the count does not fall in exactly one clock
                partition within the periods in which the spacecraft imaged its planets.
                Also if the array of the image file does not have the shape that the
                label describes.
            oops.host.PdsHostError: If the PDS3 label cannot be parsed or lacks an IMAGE
                pointer.
            oops.host.VicarHostError: If the VICAR file cannot be read: its header cannot
                be parsed, its data are cut short, or its image has more than one band.
            oops.host.WrongHostError: If the file is not a Voyager ISS image or label.
            oops.OopsValueError: If `pds3_options` holds an option that the PDS3 label
                parser does not accept.
            OSError: If the file, or unless `astrometry` is True the image file a PDS3
                label names, cannot be read.
        """

        VoyagerISS.initialize()

        files = VoyagerISS._read_files(fileinfo, astrometry=astrometry,
                                       pds3_options=pds3_options)
        label_dict = None if files.label is None else files.label.as_dict()
        header_dict = None if files.header is None else files.header.as_dict()
        dict_ = VoyagerISS._merge(label_dict, files.header)

        header_scale = (None if files.header is None
                        else VoyagerISS._header_if_scale(files.header))
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
        label_scale = dict_.get('IMAGE', {}).get('REFLECTANCE_SCALING_FACTOR')
        obs = VoyagerISS._make_snapshot(dict_, files.image_path, label_scale=label_scale,
                                        header_scale=header_scale,
                                        return_all_planets=return_all_planets,
                                        overrides=overrides)

        if files.data is not None:
            obs.insert_subfield('data', files.data)
        obs.insert_subfield('dict', dict_)
        if label_dict is not None:
            obs.insert_subfield('label_dict', label_dict)
        if header_dict is not None:
            obs.insert_subfield('header_dict', header_dict)
        oops.Host._set_path_attributes(obs, files.image_path, files.label_path)
        return oops.Host._apply_select(obs, select)

    @staticmethod
    def _read_files(fileinfo, *, astrometry, pds3_options):
        """Read a Voyager ISS product: the one function through which every label and
        image file is read.

        A failure to read or interpret a label or an image file, other than a file that
        cannot be found or read, raises a host error that names the file.

        Parameters:
            fileinfo (str | pathlib.Path | FCPath | VicarLabel | VicarImage): The path to
                the PDS3 label or to the image file, or the VICAR header or image that
                :meth:`oops.Host.from_file` read from the image file.
            astrometry (bool): True to read neither the data nor, given a label, the
                image file.
            pds3_options (dict[str, Any] | None): The options for the PDS3 label parser.

        Returns:
            _Files: What was read.

        Raises:
            oops.host.HostFormatError: If the array of the image file does not have the
                shape that the label describes.
            oops.host.PdsHostError: If the label cannot be parsed or lacks an IMAGE
                pointer.
            oops.host.VicarHostError: If the VICAR file cannot be read, or its image has
                more than one band.
            oops.host.WrongHostError: If the file is not a Voyager ISS image or label.
            oops.OopsValueError: If `pds3_options` holds an invalid option.
            OSError: If a file cannot be read.
        """

        label = None
        label_path = None
        if isinstance(fileinfo, (VicarLabel, VicarImage)):
            image_path = FCPath(fileinfo.filepath)
            header = fileinfo
            # A VicarLabel holds no array, so a full read takes it from the file
            if not astrometry and not isinstance(fileinfo, VicarImage):
                header = VoyagerISS._read_vicar(image_path, astrometry=False)
        else:
            path = FCPath(fileinfo)
            if path.suffix.lower() == '.lbl':
                label_path = path
                label = VoyagerISS._read_fileinfo(path, formats='P',
                                                  pds3_options=(_PDS3_DEFAULTS
                                                                | (pds3_options or {})))
                if '^IMAGE' not in label:
                    raise oops.host.PdsHostError(f'Voyager ISS label {path} has no '
                                                 '^IMAGE pointer')
                image_path = path.parent / label['^IMAGE']
                header = None if astrometry else VoyagerISS._read_vicar(image_path,
                                                                        astrometry=False)
            else:
                image_path = path
                header = VoyagerISS._read_vicar(path, astrometry=astrometry)
                if not VoyagerISS._detect_in_vicar(header):
                    raise oops.host.WrongHostError(f'not a Voyager ISS VICAR file: '
                                                   f'{path}')

        data = None
        if not astrometry:
            try:
                data = header.data_2d
            except ValueError as err:   # VicarError for an image of more than one band
                message = f'cannot read the VICAR image {image_path} as one band: {err}'
                raise oops.host.VicarHostError(message) from err
            if label is not None:
                shape = (label['IMAGE']['LINES'], label['IMAGE']['LINE_SAMPLES'])
                if data.shape != shape:
                    raise oops.host.HostFormatError(
                        f'Voyager ISS image {image_path} holds a {data.shape[0]}x'
                        f'{data.shape[1]} array, where its label {label_path} describes '
                        f'{shape[0]}x{shape[1]}')

        return _Files(label, header, data, image_path, label_path)

    @staticmethod
    def _read_vicar(path, *, astrometry):
        """The VICAR header, or the header and image, of an image file.

        Parameters:
            path (FCPath): The path to the image file.
            astrometry (bool): True to read only the header.

        Returns:
            VicarLabel | VicarImage: The header, or the image with its header.

        Raises:
            oops.host.VicarHostError: If the header cannot be parsed or the data are cut
                short.
            OSError: If the file cannot be read.
        """

        try:
            if astrometry:
                return VicarLabel(path, strict=False)
            return VicarImage.from_file(path, strict=False)
        # VicarError for a header that cannot be parsed; ValueError from NumPy for data
        # cut short
        except ValueError as err:
            raise oops.host.VicarHostError(f'cannot read the VICAR file {path}: '
                                           f'{err}') from err

    @staticmethod
    def _merge(label_dict, header):
        """The metadata merged from the PDS3 label and the VICAR header.

        Each item has one fixed source, the same for every image: the label wins every
        item, and a header value fills in only where the label lacks the item or gives a
        PDS3 null text for it.

        Parameters:
            label_dict (dict | None): The PDS3 label as a dictionary; None if it was not
                read.
            header (VicarLabel | VicarImage | None): The VICAR header; None if it was not
                read.

        Returns:
            dict: The merged metadata, as :meth:`from_file` describes `dict`.
        """

        # The label wins every item. Surveys of the VICAR headers of all 76,652 VGISS raw
        # images against the labels, and of about 11,000 products read directly, found
        # the header wrong where the two differ: its FDS count is another on 63 images,
        # for which the C kernels hold the pointing at the label's count; its SCET is
        # asterisks or garbled on 93, and is to the second where the label's time is to
        # 1/100 s; its exposure is wrong on 90 long exposures of 15-672 s, written as 5 or
        # 240 ms; and its 8-character target differs on 1,936, the label naming the
        # curated or more precise target. The label also wins where it truncates the
        # exposure: 137 labels, all in VGISS_5xxx, give 0.0 s for an exposure under
        # 10 ms, so those products get a zero exposure from a label, while the image file
        # alone gives the header's value. No per-image test is made. The issue that lists
        # every difference stays open (#14).
        merged = {}
        if header is not None:
            merged = _without_nulls({key: value for (key, value)
                                     in header.as_dict().items()
                                     if key not in _CONVERTED_HEADER_LINES})
            merged |= VoyagerISS._header_items(header)
        if label_dict is not None:
            merged |= _without_nulls(label_dict)
        return merged

    @staticmethod
    def _header_items(header):
        """The metadata items of a VICAR header, under their PDS3 names and in the forms
        of a PDS3 label.

        Parameters:
            header (VicarLabel | VicarImage): The VICAR header.

        Returns:
            dict[str, Any]: The items that the header gives: INSTRUMENT_HOST_NAME,
            INSTRUMENT_ID, PRODUCT_TYPE, IMAGE_NUMBER, SPACECRAFT_CLOCK_STOP_COUNT,
            IMAGE_ID, IMAGE_TIME, STOP_TIME, EXPOSURE_DURATION, FILTER_NAME,
            FILTER_NUMBER and TARGET_NAME. An item whose field is missing, filled with
            asterisks, a placeholder or garbage is left out.
        """

        dict_ = header.as_dict()
        lab02 = str(dict_.get('LAB02', ''))
        lab03 = str(dict_.get('LAB03', ''))
        items = {}

        if match := _LAB02_SPACECRAFT.search(lab02):
            items['INSTRUMENT_HOST_NAME'] = 'VOYAGER ' + match['vgr']
        if match := _LAB03_CAMERA.search(lab03):
            items['INSTRUMENT_ID'] = 'ISS' + match['camera']

        items['PRODUCT_TYPE'] = 'DECOMPRESSED_RAW_IMAGE'
        for (task, product) in _VICAR_PRODUCT_TASKS:
            if task in header.get('TASK+', []):
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

        scet = _header_time(_LAB02_SCET.search(lab02))
        if scet is not None:
            items['IMAGE_TIME'] = scet
            items['STOP_TIME'] = scet

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
    def _header_if_scale(header):
        """The I/F factor that a VICAR header gives.

        Parameters:
            header (VicarLabel | VicarImage): The VICAR header of the image.

        Returns:
            float | None: The factor x from the header line "FOR (I/F)*10000., MULTIPLY
            DN VALUE BY x", which the calibration software writes as LABEL3, divided by
            10000; None if the header has no such line, or its x is not a number, such as
            N/A.
        """

        for value in header.as_dict().values():
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

        It reads a raw image index of the Ring-Moon Systems Node metadata, such as
        "VGISS_6109_raw_image_index.lbl", and its supplement, 100 rows at a time as the
        iteration proceeds, and builds each row's Snapshot when the iteration reaches it.
        Every row gives a Snapshot, in index order.

        Parameters:
            filepath (str | pathlib.Path | FCPath): The path to the PDS label of a VGISS
                raw image index.
            supplement (str | pathlib.Path | FCPath | object | None, optional): The path
                to the PDS label of the supplemental index, in any directory, whose
                columns are merged into each row; None to read the index alone. Each
                index row is merged with the supplement row in the same position, and
                where both tables give a column, the index value is kept. The default,
                `oops.Host.SUPPLEMENT`, is the file in the directory of `filepath` whose
                name matches except that "_raw_image_index" is replaced by
                "_supplemental_index", in lower or upper case; if that file does not
                exist, or the name of `filepath` contains neither, the index is read
                alone.
            label (Pds3Label, optional): The PDS label of `filepath` if it has already
                been read.
            row_dicts (collections.abc.Iterable[dict[str, Any]], optional): The rows of
                the index already merged with the supplement. If they are given, the
                tables are not read, and each row that passes `filter` gives a Snapshot.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser. The
                method is "strict" unless they name another. The index's label is parsed
                with all of them, and the supplement's label with the method alone.
            filter (dict[str, Any], optional): Column names, each with a value, a set,
                list, or tuple of values, or a tuple of two values that bound a range. A
                row gives a Snapshot only if every column it lists equals the value, is
                one of the values, or lies within the range, bounds included.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            select (int | slice | str | tuple[int | slice | str, ...], optional): The
                selection applied to each row's Snapshot, as :meth:`from_file` describes
                it; None, the default, gives the Snapshot.
            target (str | oops.Body, optional): Override for the default target Body of
                every observation.
            lightsource (oops.LightSource, optional): Override for the default LightSource
                of every observation.
            path (str | oops.Path, optional): Override for the Path of the observer.
            frame (str | oops.Frame, optional): Override for the Frame of the observing
                instrument, such as a :class:`~oops.frame.Cmatrix`, used as it is given.
            cadence (oops.Cadence, optional): Override for the Cadence of the observation.
            fov (oops.FOV, optional): Override for the FOV of every observation.
            calibrations (oops.Calibration | list[oops.Calibration], optional): Override
                for the calibration or list of calibrations.
            timeshift (tuple[float, str], optional): A Fittable time shift, as
                :meth:`from_file` describes it.
            navigation (tuple[float, ...], optional): Two or three rotation angles in
                radians that wrap the frame in a fittable Navigation frame, as
                :meth:`from_file` describes them.
            tracker (str, optional): "start", "midtime", or "end", to assign a
                TrackerFrame, as :meth:`from_file` describes it.
            parallel (Observation, optional): A parallel Observation, as
                :meth:`from_file` describes it.

        Returns:
            collections.abc.Iterator[Snapshot]: One observation per filtered row, in
            index order. Each observation has these subfields:

            - `dict`: the row exactly as the table reader gives it, with the "_mask"
              entry of each column and the declared null values, such as the
              EXPOSURE_DURATION -99.9999.
            - `host` "Voyager", `instrument` "ISS", `detector` "NAC" or "WAC", and
              `spacecraft`, the int 1 or 2, from INSTRUMENT_HOST_NAME.
            - `filter`, the FILTER_NAME, when it is given.
            - `planet`: the planet of MISSION_PHASE_NAME, else of the encounter in
              progress at the time of the image.
            - `target`, `lightsource` and `calibrations`, the observation's defaults:
              the target named by TARGET_NAME, with the ring names "J RINGS", "S RINGS",
              "U RINGS" and "N RINGS" given as the ring planes, "SYSTEM" and "UNK SAT"
              as the planet, and the calibration lamps, dark frames, sky and star fields
              as "NONE"; a star target is the light source, and otherwise the light
              source is the Sun. `target` is absent if no TARGET_NAME is given.
              `calibrations` holds ``FlatCalib('I/F', if_scale)`` when `if_scale` is
              present, and is empty otherwise.
            - `spacecraft_clock_start_count` and `spacecraft_clock_stop_count`, as text
              such as "34502:01:001", when they are given.
            - `spice_kernels`: the basenames of the furnished kernels that apply to the
              observation, the C kernels left out if `frame` is given.
            - `spice_to_frame` (the identity matrix), `spice_frame_name` ("VG1_ISSNA",
              "VG1_ISSWA", "VG2_ISSNA" or "VG2_ISSWA") and `spice_frame_id`.
            - `ck_object_id` (-31100 or -32100, the scan platform), `ck_sclk_id` (-31 or
              -32) and `spice_frame_is_frozen`: True if the C kernels hold a record at
              the image's own spacecraft clock stop count, from which the frame is a
              fixed :class:`~oops.frame.Cmatrix`; False if they do not, so that the
              frame is the camera's :class:`~oops.frame.SpiceFrame`, and using it raises
              the error of SPICE that names its error code.
            - `filepath`: the relative path VOLUME_NAME/FILE_SPECIFICATION_NAME, as the
              index gives it; `basename`: the last component of that path.

            The time of the image, the close of its shutter, is STOP_TIME, else
            IMAGE_TIME; the exposure is EXPOSURE_DURATION, and 0 if no source gives it.
            If neither time is given, the time is that of the spacecraft clock stop
            count in the clock kernel, in the clock partition in which the count falls
            within the periods in which the spacecraft imaged its planets. The count is
            SPACECRAFT_CLOCK_STOP_COUNT, else IMAGE_NUMBER, else the count with which the
            file name begins, such as C3450201 for FDS 34502.01. A value is not given if
            its "_mask" entry is True or it is a PDS3 null text. Pointing is not checked.

        Raises:
            FileNotFoundError: If a `supplement` that is given does not exist, when the
                call is made; if a table file does not exist, when the iteration starts.
            oops.host.HostFormatError: When the iteration reaches a row without a time
                whose clock count does not fall in exactly one partition within those
                periods.
            oops.host.HostIndexError: When the call is made, for a label that does not
                describe a table that can be read. When the iteration reaches the row
                concerned, for a row whose files differ between the two tables, whose
                PRODUCT_ID differs, or which the supplement lacks; after the last row, for
                supplement rows left over; and for a table file that holds more or fewer
                rows than its label says.
            oops.host.PdsHostError: If a label cannot be parsed.
            oops.host.WrongHostError: If `filepath` is not a Voyager ISS index.
            oops.OopsValueError: If `pds3_options` holds an invalid option.
            OSError: If the label of `filepath` cannot be read.
        """

        VoyagerISS.initialize()

        # The supplement is optional, because rows load without it, but one named
        # explicitly must exist
        rows = VoyagerISS._index_rows(filepath, supplement=supplement,
                                      require_supplement=supplement
                                      is not oops.Host.SUPPLEMENT,
                                      label=label, row_dicts=row_dicts,
                                      pds3_options=_PDS3_DEFAULTS | (pds3_options or {}),
                                      filter=filter)
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
        return (VoyagerISS._row_snapshot(row, select=select,
                                         return_all_planets=return_all_planets,
                                         overrides=overrides)
                for row in rows)

    @staticmethod
    def _row_snapshot(row, *, select, return_all_planets, overrides):
        """The Snapshot of one row of a raw image index.

        Parameters:
            row (dict[str, Any]): The row, merged with its supplement row.
            select (int | slice | str | tuple | None): The `select` of :meth:`from_index`.
            return_all_planets (bool): Include kernels for all planets.
            overrides (dict[str, Any]): The values of the standard overrides.

        Returns:
            Snapshot: The observation, as :meth:`from_index` describes it.
        """

        # The observation is built from the values the row gives. A value whose mask is
        # set, as for the declared null exposure -99.9999, or that is a PDS3 null text,
        # as the "UNK" times of 17 rows, is not given; `dict` keeps the row as it is.
        items = {key: value for (key, value) in row.items()
                 if not row.get(f'{key}_mask', False)
                 and not oops.host.is_pds3_null(value)}
        filepath = f'{row["VOLUME_NAME"]}/{row["FILE_SPECIFICATION_NAME"]}'
        obs = VoyagerISS._make_snapshot(items, filepath,
                                        return_all_planets=return_all_planets,
                                        overrides=overrides)
        obs.insert_subfield('dict', row)
        oops.Host._set_path_attributes(obs, filepath, None)
        return oops.Host._apply_select(obs, select)

    @classmethod
    def _supplement_path(cls, index_path):
        """The supplemental index beside a raw image index.

        Parameters:
            index_path (FCPath): The path to the label of the raw image index.

        Returns:
            FCPath | None: The path in the same directory with "_raw_image_index" in its
            name replaced by "_supplemental_index", in lower or upper case; None if the
            name contains neither.
        """

        name = index_path.name
        for (index, supp) in _SUPPLEMENT_NAMES:
            if index in name:
                return index_path.with_name(name.replace(index, supp))
        return None

    @staticmethod
    def _make_snapshot(items, name, *, label_scale=None, header_scale=None,
                       return_all_planets=False, overrides):
        """A Snapshot built from the merged metadata of a product or the values of a row.

        Parameters:
            items (dict[str, Any]): The metadata, without null values.
            name (FCPath | str): The image file, or for an index row its relative path,
                for the clock count in its name and for messages.
            label_scale (float, optional): The label's REFLECTANCE_SCALING_FACTOR.
            header_scale (float, optional): The VICAR header's I/F factor, before the
                Voyager 1 corrections.
            return_all_planets (bool, optional): Include kernels for all planets.
            overrides (dict[str, Any]): The values of the standard overrides.

        Returns:
            Snapshot: The observation, without `dict`, `data`, `label_dict`,
            `header_dict` and the subfields that name its files.

        Raises:
            oops.host.HostFormatError: If no clock count is known, or if no time is known
                and the count does not fall in one partition within the imaging periods.
        """

        vgr = int(items['INSTRUMENT_HOST_NAME'][-1])
        if 'INSTRUMENT_ID' in items:            # "ISSN" or "ISSW" in a label
            camera = items['INSTRUMENT_ID'][-1] + 'AC'
        else:                                   # "NARROW ANGLE CAMERA" in an index
            camera = items['INSTRUMENT_NAME'][0] + 'AC'
        count = VoyagerISS._clock_count(items, name)
        if count is None:
            raise oops.host.HostFormatError(f'Voyager ISS image {name} has no '
                                            'spacecraft clock count')
        tstop = VoyagerISS._tstop(items, vgr, count, name)
        texp = float(items.get('EXPOSURE_DURATION', 0.))
        if 'MISSION_PHASE_NAME' in items:
            planet = str(items['MISSION_PHASE_NAME']).split()[0]
        else:
            planet = _Voyager.encounter(tstop)
        filter_ = str(items['FILTER_NAME']) if 'FILTER_NAME' in items else None

        if_scale = label_scale
        if if_scale is None and header_scale is not None:
            if_scale = header_scale
            if vgr == 1:
                if planet == 'SATURN':
                    if_scale *= _V1_SATURN_IF_FACTOR
                if_scale *= _V1_FILTER_IF_FACTORS.get(filter_, 1.)

        (frame, frozen) = VoyagerISS._camera_frame(vgr, camera, count)
        geomed = items.get('PRODUCT_TYPE') == _GEOMED
        params = {
            'cadence'     : oops.cadence.SnapCadence(tstop - texp, texp),
            'fov'         : VoyagerISS._FOVS[camera, geomed],
            'path'        : _Voyager.PATH_IDS[vgr],
            'frame'       : frame,
            'lightsource' : 'SUN',
            'calibrations': ([] if if_scale is None
                             else [oops.calibration.FlatCalib('I/F', if_scale)]),
        }
        if 'TARGET_NAME' in items:
            label_target = str(items['TARGET_NAME'])
            if label_target in _TARGET_STARS:
                params['lightsource'] = oops.lightsource.star_lookup(
                    _TARGET_STARS[label_target])
                params['target'] = 'NONE'
            elif label_target in _PLANET_TARGETS:
                params['target'] = planet
            else:
                params['target'] = _TARGET_NAME_REPAIRS.get(label_target, label_target)

        oops.Host._apply_overrides(params, **overrides)
        obs = oops.observation.Snapshot(('v', 'u'), **params)

        obs.insert_subfield('host', 'Voyager')
        obs.insert_subfield('instrument', 'ISS')
        obs.insert_subfield('detector', camera)
        if filter_ is not None:
            obs.insert_subfield('filter', filter_)
        obs.insert_subfield('planet', planet)
        obs.insert_subfield('spacecraft', vgr)
        if if_scale is not None:
            obs.insert_subfield('if_scale', if_scale)
        for key in ('SPACECRAFT_CLOCK_START_COUNT', 'SPACECRAFT_CLOCK_STOP_COUNT'):
            if key in items:
                obs.insert_subfield(key.lower(), str(items[key]))

        # With a custom frame, pointing never came from a CK, so any CK that happens to be
        # furnished is unrelated to this observation and must not be reported as used.
        kernels = _Voyager.used_kernels(obs.time, vgr, f'iss{camera[0].lower()}a',
                                        return_all_planets=return_all_planets,
                                        ck=overrides['frame'] is None)
        obs.insert_subfield('spice_kernels', kernels)
        obs.insert_subfield('spice_to_frame', oops.Matrix3.IDENTITY)
        obs.insert_subfield('spice_frame_name', f'VG{vgr}_ISS{camera[0]}A')
        obs.insert_subfield('spice_frame_id', (_SCAN_PLATFORM_ID[vgr]
                                               - _CAMERA_FRAME_OFFSET[camera]))
        obs.insert_subfield('ck_object_id', _SCAN_PLATFORM_ID[vgr])
        obs.insert_subfield('ck_sclk_id', _Voyager.SPACECRAFT_IDS[vgr])
        obs.insert_subfield('spice_frame_is_frozen', frozen)
        return obs

    @staticmethod
    def _clock_count(items, name):
        """The image's spacecraft clock stop count, "hhhhh:mm:ddd".

        A label, a VICAR header and the supplemental index give
        SPACECRAFT_CLOCK_STOP_COUNT. Otherwise it is derived from IMAGE_NUMBER, the FDS
        count "hhhhh.mm" of the image, which matches the stop count for all but five of
        the 76,652 VGISS raw images, and otherwise from the name of the image file,
        which begins with the same count, such as C3450201 for FDS 34502.01. The name is
        the only source of the count for the 11 VICAR headers that give the fill value
        FDS 00000.00 or 0.00.

        Parameters:
            items (dict[str, Any]): The metadata or the values of a row.
            name (FCPath | str): The image file, or the relative path of an index row.

        Returns:
            str | None: The count; None if no source gives it.
        """

        if 'SPACECRAFT_CLOCK_STOP_COUNT' in items:
            return str(items['SPACECRAFT_CLOCK_STOP_COUNT'])
        if 'IMAGE_NUMBER' in items:
            (hours, minutes) = f'{float(items["IMAGE_NUMBER"]):.2f}'.split('.')
            return f'{int(hours)}:{minutes}:001'

        match = _NAME_COUNT.match(FCPath(name).name)
        if match is None:
            return None
        return f'{int(match["hours"])}:{match["minutes"]}:001'

    @staticmethod
    def _tstop(items, vgr, count, name):
        """The close of the shutter in seconds TDB.

        STOP_TIME, which a label and the supplemental index give to 1/100 s, is used if
        known; otherwise IMAGE_TIME, which the index gives to the second and which is
        otherwise the same. If neither is known, the time is that of the clock count in
        the clock kernel. That is so for 17 index rows in VGISS_6101 and VGISS_6201, 76
        reads of an image file alone whose header lacks a valid time, and the 11 headers
        with FDS 00000.00. The count's time is a median of 0.9 s after STOP_TIME on the
        76,635 raw images that give both, but whole FDS minutes later for some and up to
        7,625 s earlier for a few.

        Parameters:
            items (dict[str, Any]): The metadata or the values of a row.
            vgr (int): The spacecraft number.
            count (str): The spacecraft clock stop count.
            name (FCPath | str): The image file or relative path, for the message.

        Returns:
            float: The time.

        Raises:
            oops.host.HostFormatError: If no time is given and the count does not fall in
                exactly one partition within the imaging periods.
        """

        for key in ('STOP_TIME', 'IMAGE_TIME'):
            if key in items:
                return julian.tdb_from_tai(julian.tai_from_iso(items[key]))

        ticks = _Voyager.sclk_ticks(vgr, count)
        if ticks is None:
            raise oops.host.HostFormatError(f'Voyager ISS image {name} has no time, '
                                            f'and its clock count {count} does not fall '
                                            'in one clock partition within the imaging '
                                            'periods')
        return cspyce.sct2e(_Voyager.SPACECRAFT_IDS[vgr], ticks)

    @staticmethod
    def _camera_frame(vgr, camera, count):
        """The camera frame of an image.

        Parameters:
            vgr (int): The spacecraft number.
            camera (str): "NAC" or "WAC".
            count (str): The spacecraft clock stop count.

        Returns:
            tuple[oops.frame.Cmatrix | oops.frame.SpiceFrame, bool]: The frame and True,
            if the scan platform C kernels hold a record within 20 ticks of the count: an
            unregistered Cmatrix relative to J2000 built from that record and the fixed
            rotation from the scan platform to the camera. Otherwise, the camera's
            SpiceFrame and False; using it raises the error of SPICE.
        """

        camera_frame = f'VG{vgr}_ISS{camera[0]}A'
        ticks = _Voyager.sclk_ticks(vgr, count)
        if ticks is not None:
            try:
                (j2000_to_platform, _) = cspyce.ckgp(_SCAN_PLATFORM_ID[vgr], ticks,
                                                     _TICK_TOLERANCE, 'J2000')
            except OSError:     # SPICE(CKINSUFFDATA): no record at this count
                pass
            else:
                platform_to_camera = cspyce.pxform(f'VG{vgr}_SCAN_PLATFORM', camera_frame,
                                                   0.)
                return (oops.frame.Cmatrix(oops.Matrix3(platform_to_camera)
                                           * oops.Matrix3(j2000_to_platform)), True)

        return (oops.frame.SpiceFrame.get(camera_frame), False)

    ######################################################################################
    # Detectors
    ######################################################################################

    @staticmethod
    def _detect_in_pds3(label):
        """True if the given parsed PDS3 label describes this host's data."""

        return (label.get('INSTRUMENT_HOST_NAME', '').startswith('VOYAGER')
                and label.get('INSTRUMENT_ID', '') in ('ISSN', 'ISSW'))

    @staticmethod
    def _detect_in_vicar(label):
        """True if the given VicarLabel describes this host's data."""

        return (_LAB02_SPACECRAFT.search(str(label.get('LAB02', ''))) is not None
                and _LAB03_CAMERA.search(str(label.get('LAB03', ''))) is not None)

    @staticmethod
    def _detect_in_index(label):
        """None, so that the host is identified from the rows by
        :meth:`_detect_in_row`.
        """

        return None

    @staticmethod
    def _detect_in_row(row_dict):
        """True if the given row of an index file refers to this host/instrument."""

        return (row_dict.get('INSTRUMENT_HOST_NAME', '').startswith('VOYAGER')
                and row_dict.get('INSTRUMENT_NAME', '').endswith('ANGLE CAMERA'))

    ######################################################################################
    # Initialization
    ######################################################################################

    @staticmethod
    def initialize(*, planets=None, asof=None, irregulars=True):
        """Initialize key information about the ISS instrument.

        Defines the Solar System bodies over the mission, the moons of Uranus and Neptune
        included; loads the mission and ISS kernels; and constructs the FOVs.
        :meth:`from_file` and :meth:`from_index` call it with the default values; call it
        before them only to choose other values. After the first call, later calls to
        this function are ignored. Importing the module loads nothing.

        Parameters:
            planets (int | collections.abc.Sequence[int], optional): The number of a
                planet, from 1 for Mercury to 9 for Pluto, or a sequence of such numbers,
                such as (6,) for Saturn alone, to pass to
                :meth:`~oops.Body.define_solar_system`, which defines each planet named
                and its moons. None or 0 means all.
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
