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
from vicar     import VicarImage

import oops
from ._voyager import _Voyager

__all__ = ['VoyagerISS']

# The scan platform C kernels hold one pointing record per image, stamped at the image's
# own spacecraft clock stop count, which can be whole FDS minutes (48 s each) after the
# true close of the shutter, STOP_TIME. The record is therefore found by the count, never
# by the time; see docs/pointing.rst. The tolerance is in clock ticks of 60 ms.
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
_LAB02 = re.compile(r'VGR-(?P<vgr>[12])\s+FDS\s+(?P<fds>[\d.]+)\s+PICNO\s+(?P<picno>\S+)'
                    r'\s+SCET\s+(?P<year>\d\d)\.(?P<doy>\d{3})\s+(?P<hms>\d\d:\d\d:\d\d)')
_LAB03 = re.compile(r'(?P<camera>[NW])A CAMERA\s+EXP\s+(?P<exp>[\d.]+)\s+MSEC'
                    r'\s+FILT\s+(?P<filter_number>\d)\((?P<filter>[^)]*)\)')

# The target name occupies these columns of LAB05, with underscores for blanks
_LAB05_TARGET = slice(31, 43)


class _UnusableImageError(oops.host.HostError):
    """An image whose time or pointing is unavailable.

    :meth:`VoyagerISS.from_file` raises it as an :class:`oops.host.HostError`, and
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
                as a Pds3Label, VicarLabel, or VicarImage. The metadata of a VICAR file
                are read from its own header; no PDS3 label is needed.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            pds3_options (dict[str, Any], optional): Any keyword arguments and their
                values to be passed to the :class:`~pdsparser.Pds3Label` parser.
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
            `detector`, `filter`, `planet`, `label_target`, `spice_kernels`, `filepath`,
            `basename`, `spice_to_frame`, `spice_frame_name`, `spice_frame_id`, `abspath`
            and `image_url` inserted, and
            `data` unless `astrometry` is True. `dict` is the PDS3 label, or the VICAR
            header with its values also given under their PDS3 keyword names. A
            geometrically corrected image has a 1000x1000 FOV and every other product an
            800x800 one.

        Raises:
            oops.host.HostError: If `frame` is not given and the C kernels hold no record
                for the image's spacecraft clock stop count.
            oops.host.HostFormatError: If a VICAR header lacks the Voyager LAB02 or LAB03
                line, or if the label gives neither STOP_TIME nor IMAGE_TIME.
            oops.host.PdsHostError: If the PDS3 label cannot be parsed.
            oops.host.VicarHostError: If the VICAR header cannot be parsed.
            oops.host.WrongHostError: If the file is not a Voyager ISS image.
            OSError: If the file cannot be read.
        """

        VoyagerISS.initialize()     # define everything the first time through

        pds3_options = oops.Host._pds3_options_with_defaults(pds3_options)
        fileinfo = VoyagerISS._read_fileinfo(fileinfo, formats='PV',
                                             astrometry=astrometry,
                                             pds3_options=pds3_options)

        # Read the data if necessary; harmonize the label content
        data = None
        if isinstance(fileinfo, Pds3Label):
            if not astrometry:
                data = oops.host.read_pds3_image_array(fileinfo)
            dict_ = fileinfo.as_dict()
        else:
            if not astrometry:
                if not isinstance(fileinfo, VicarImage):
                    # A VicarLabel has no array; read the file
                    fileinfo = VicarImage(fileinfo.filepath, strict=False)
                data = fileinfo.data_2d
            dict_ = VoyagerISS._vicar_to_pds3(fileinfo)

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

        return VoyagerISS._make_snapshot(dict_, filepath=FCPath(fileinfo.filepath),
                                         data=data, return_all_planets=return_all_planets,
                                         overrides=overrides)

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
                row of the index. Include if `filepath` and `supplement` have already been
                read. If provided, `supplement` is ignored.
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
            subfields `dict`, `host`, `instrument`, `detector`, `filter`, `planet`,
            `label_target`, `spice_kernels`, `filepath`, `basename`, `spice_to_frame`,
            `spice_frame_name` and `spice_frame_id` inserted. `dict` is the row, merged
            with its row of the supplement, and `filepath` is the VOLUME_NAME and
            FILE_SPECIFICATION_NAME joined by a slash. A row is left out if it gives
            neither STOP_TIME nor IMAGE_TIME, or if `frame` is not given and the C kernels
            hold no record for the image's spacecraft clock stop count.

        Raises:
            FileNotFoundError: If the supplement does not exist.
            oops.host.HostIndexError: If `supplement` is not specified and the name of
                `filepath` contains no "_raw_image_index", if the supplement does not
                have the same number of rows as the index, or if a row's VOLUME_NAME or
                FILE_SPECIFICATION_NAME differs from the index's.
            oops.host.WrongHostError: If `filepath` is not a Voyager ISS index.
            OSError: If `filepath`, or a `supplement` that is specified, does not exist or
                cannot be read.
        """

        VoyagerISS.initialize()

        # Read and filter the index table
        if row_dicts is None:
            filepath = FCPath(filepath)
            if supplement is None:
                supplement = VoyagerISS._supplement_path(filepath, pds3_options)
            row_dicts = VoyagerISS._read_index_rows(filepath, supplement=supplement,
                                                    validate=_INDEX_KEYS,
                                                    pds3_options=pds3_options)
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
            fpath = row_dict['VOLUME_NAME'] + '/' + row_dict['FILE_SPECIFICATION_NAME']
            try:
                obs = VoyagerISS._make_snapshot(row_dict, filepath=fpath, data=None,
                                                return_all_planets=return_all_planets,
                                                overrides=overrides)
            except _UnusableImageError:
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
    def _make_snapshot(dict_, filepath, data=None, *, return_all_planets=False,
                       overrides):
        """A Snapshot built from a PDS3 label or a row of an index.

        Parameters:
            dict_ (dict): The PDS3 label as a dictionary, or an index row.
            filepath (FCPath | str): The path to the file, or for an index row, the
                VOLUME_NAME and FILE_SPECIFICATION_NAME joined by a slash.
            data (numpy.ndarray, optional): The image array; None to omit the `data`
                subfield.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter.
            overrides (dict[str, Any]): The values of the standard overrides, `target`,
                `lightsource`, `path`, `frame`, `cadence`, `fov`, `calibrations`,
                `timeshift`, `navigation`, `tracker` and `parallel`, as described for
                :meth:`from_file`.

        Returns:
            Snapshot: The observation, with the subfields listed for :meth:`from_file`.

        Raises:
            _UnusableImageError: If `dict_` gives neither STOP_TIME nor IMAGE_TIME, or if
                `frame` is not overridden and the C kernels hold no record for the
                image's spacecraft clock stop count.
        """

        vgr = VoyagerISS._spacecraft(dict_)
        camera = VoyagerISS._camera(dict_)
        geomed = dict_.get('PRODUCT_TYPE') == _GEOMED

        # The index of volume VGISS_6201 gives the null value -99.9999 for the exposure of
        # one dark frame
        texp = max(float(dict_['EXPOSURE_DURATION']), 0.)
        tstop = VoyagerISS._tstop(dict_)

        planet = dict_['MISSION_PHASE_NAME'].split()[0]
        label_target = dict_['TARGET_NAME']
        if label_target in _TARGET_STARS:
            label_lightsource = oops.lightsource.star_lookup(_TARGET_STARS[label_target])
            target = 'NONE'
        else:
            label_lightsource = 'SUN'
            if label_target in _PLANET_TARGETS:
                target = planet
            else:
                target = _TARGET_NAME_REPAIRS.get(label_target, label_target)

        # Look up the pointing unless a custom frame was given
        using_cks = overrides['frame'] is None
        frame = VoyagerISS._camera_frame(dict_, vgr, camera, tstop) if using_cks else None

        params = {
            'cadence'     : oops.cadence.SnapCadence(tstop - texp, texp),
            'fov'         : VoyagerISS._FOVS[camera, geomed],
            'path'        : _Voyager.PATH_IDS[vgr],
            'frame'       : frame,
            'target'      : target,
            'lightsource' : label_lightsource,
            'calibrations': [],
        }

        oops.Host._apply_overrides(params, filepath, **overrides)

        obs = oops.observation.Snapshot(('v', 'u'), **params)
        if data is not None:
            obs.insert_subfield('data', data)

        obs.insert_subfield('dict', dict_)
        obs.insert_subfield('host', 'Voyager')
        obs.insert_subfield('instrument', 'ISS')
        obs.insert_subfield('detector', camera)
        obs.insert_subfield('filter', dict_['FILTER_NAME'])
        obs.insert_subfield('planet', planet)
        obs.insert_subfield('label_target', label_target)

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
    def _spacecraft(dict_):
        """The spacecraft number, 1 or 2, from INSTRUMENT_HOST_NAME or SPACECRAFT_NAME."""

        name = dict_.get('INSTRUMENT_HOST_NAME') or dict_['SPACECRAFT_NAME']
        return int(name[-1])

    @staticmethod
    def _camera(dict_):
        """The camera, "NAC" or "WAC", from INSTRUMENT_ID or INSTRUMENT_NAME.

        A label gives INSTRUMENT_ID "ISSN" or "ISSW"; an index row gives only
        INSTRUMENT_NAME, "NARROW ANGLE CAMERA" or "WIDE ANGLE CAMERA".
        """

        if 'INSTRUMENT_ID' in dict_:
            return dict_['INSTRUMENT_ID'][-1] + 'AC'
        return dict_['INSTRUMENT_NAME'][0] + 'AC'

    @staticmethod
    def _tstop(dict_):
        """The close of the shutter in seconds TDB.

        STOP_TIME, which a label and the supplemental index give to 1/100 s, is used if
        known; otherwise IMAGE_TIME, which the index gives to the second and which is
        otherwise the same. The spacecraft clock count is never used, because it can fall
        whole FDS minutes after the shutter closed.

        Parameters:
            dict_ (dict): The PDS3 label or index row.

        Returns:
            float: The time.

        Raises:
            _UnusableImageError: If `dict_` gives neither STOP_TIME nor IMAGE_TIME.
        """

        for key in ('STOP_TIME', 'IMAGE_TIME'):
            value = dict_.get(key, 'UNK')
            if value != 'UNK':
                return julian.tdb_from_tai(julian.tai_from_iso(value))

        raise _UnusableImageError('no STOP_TIME or IMAGE_TIME is given for Voyager ISS '
                                  f'image {dict_.get("PRODUCT_ID", "")}')

    @staticmethod
    def _clock_count(dict_):
        """The image's spacecraft clock stop count, "hhhhh:mm:ddd".

        A label and the supplemental index give SPACECRAFT_CLOCK_STOP_COUNT. Otherwise it
        is derived from IMAGE_NUMBER, the FDS count "hhhhh.mm" of the image, which matches
        the stop count for all but five of the 76,652 VGISS raw images.
        """

        if 'SPACECRAFT_CLOCK_STOP_COUNT' in dict_:
            return dict_['SPACECRAFT_CLOCK_STOP_COUNT']

        (hours, minutes) = f'{float(dict_["IMAGE_NUMBER"]):.2f}'.split('.')
        return f'{int(hours):05d}:{minutes}:001'

    @staticmethod
    def _camera_frame(dict_, vgr, camera, tstop):
        """The fixed camera frame built from the image's own C-kernel record.

        Parameters:
            dict_ (dict): The PDS3 label or index row.
            vgr (int): The spacecraft number.
            camera (str): "NAC" or "WAC".
            tstop (float): The close of the shutter in seconds TDB, which selects the
                clock partition when `dict_` does not give it.

        Returns:
            oops.frame.Cmatrix: An unregistered frame relative to J2000.

        Raises:
            _UnusableImageError: If the C kernels hold no record for the clock count.
        """

        count = VoyagerISS._clock_count(dict_)
        partition = dict_.get('SPACECRAFT_CLOCK_PARTITION_NUMBER')
        platform_id = _SCAN_PLATFORM_ID[vgr]
        try:
            ticks = _Voyager.sclk_ticks(vgr, count, partition, near=tstop)
            (j2000_to_platform, _) = cspyce.ckgp(platform_id, ticks, _TICK_TOLERANCE,
                                                 'J2000')
        except (ValueError, LookupError, OSError) as err:
            raise _UnusableImageError(f'no Voyager {vgr} scan platform pointing for '
                                      f'clock count {count}') from err

        platform_to_camera = cspyce.pxform(f'VG{vgr}_SCAN_PLATFORM',
                                           f'VG{vgr}_ISS{camera[0]}A', 0.)
        return oops.frame.Cmatrix(oops.Matrix3(platform_to_camera)
                                  * oops.Matrix3(j2000_to_platform))

    @staticmethod
    def _vicar_to_pds3(vic):
        """A VICAR header converted to match the content of a PDS3 label.

        Parameters:
            vic (VicarLabel | VicarImage): The VICAR header.

        Returns:
            dict[str, Any]: The header as a dictionary, with these PDS3 keywords added:
            INSTRUMENT_HOST_NAME, INSTRUMENT_ID, PRODUCT_TYPE, IMAGE_NUMBER, IMAGE_ID,
            IMAGE_TIME, STOP_TIME, EXPOSURE_DURATION, FILTER_NAME, FILTER_NUMBER,
            TARGET_NAME and MISSION_PHASE_NAME.

        Raises:
            oops.host.HostFormatError: If the header lacks a LAB02 or LAB03 line in the
                Voyager form.
        """

        dict_ = vic.as_dict()
        lab02 = _LAB02.search(str(dict_.get('LAB02', '')))
        lab03 = _LAB03.search(str(dict_.get('LAB03', '')))
        if lab02 is None or lab03 is None:
            raise oops.host.HostFormatError('VICAR header has no Voyager LAB02 or LAB03 '
                                            'line')

        scet = (dt.datetime(1900 + int(lab02['year']), 1, 1)
                + dt.timedelta(days=int(lab02['doy']) - 1)
                + dt.timedelta(seconds=julian.sec_from_iso(lab02['hms'])))
        scet = scet.isoformat()

        product_type = 'DECOMPRESSED_RAW_IMAGE'
        for (task, product) in _VICAR_PRODUCT_TASKS:
            if task in vic.get('TASK+', []):
                product_type = product

        target = str(dict_.get('LAB05', ''))[_LAB05_TARGET].strip().replace('_', ' ')

        dict_['INSTRUMENT_HOST_NAME'] = 'VOYAGER ' + lab02['vgr']
        dict_['INSTRUMENT_ID'] = 'ISS' + lab03['camera']
        dict_['PRODUCT_TYPE'] = product_type
        dict_['IMAGE_NUMBER'] = lab02['fds']
        dict_['IMAGE_ID'] = lab02['picno']
        dict_['IMAGE_TIME'] = scet
        dict_['STOP_TIME'] = scet
        dict_['EXPOSURE_DURATION'] = float(lab03['exp']) / 1000.
        dict_['FILTER_NAME'] = lab03['filter']
        dict_['FILTER_NUMBER'] = int(lab03['filter_number'])
        dict_['TARGET_NAME'] = target
        tstop = julian.tdb_from_tai(julian.tai_from_iso(scet))
        dict_['MISSION_PHASE_NAME'] = _Voyager.encounter(tstop) + ' ENCOUNTER'
        return dict_

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

        return (_LAB02.search(str(label.get('LAB02', ''))) is not None
                and _LAB03.search(str(label.get('LAB03', ''))) is not None)

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
