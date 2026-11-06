##########################################################################################
# host_voyager/oops/_voyager.py
##########################################################################################

import julian
import spicedb
import cspyce
import oops

from oops.body import Body

# The planetary encounters, each running from its start time until the next one begins.
# Voyager 1 flew past Jupiter and Saturn, Voyager 2 past all four planets; the boundaries
# fall in the quiet cruise between encounters, so they serve both spacecraft.
TIMELINE = [
    {'UTC': '1979-01-06T00:00:00', 'target': 'JUPITER'},
    {'UTC': '1980-01-01T00:00:00', 'target': 'SATURN'},
    {'UTC': '1983-01-01T00:00:00', 'target': 'URANUS'},
    {'UTC': '1987-01-01T00:00:00', 'target': 'NEPTUNE'},
]

for _phase in TIMELINE:
    _phase['TDB'] = julian.tdb_from_tai(julian.tai_from_iso(_phase['UTC']))

_ALL_PLANETS = ['MERCURY', 'VENUS', 'EARTH', 'MARS', 'JUPITER', 'SATURN', 'URANUS',
                'NEPTUNE']


class _Voyager:
    """An instance-free class to hold Voyager-specific parameters.

    Attributes:
        START_TIME (str): The start of the period covered, as an ISO date.
        STOP_TIME (str): The end of the period covered, as an ISO date.
        SPACECRAFT_IDS (dict[int, int]): The NAIF ID of each spacecraft, keyed by its
            number, 1 or 2.
        PATH_IDS (dict[int, str]): The ID of each spacecraft's registered Path, keyed by
            its number.
        ENCOUNTERS (dict[int, tuple[str, ...]]): The abbreviations of the planets each
            spacecraft encountered, as they appear in the names of its SP kernels.
        loaded_instruments (list[str]): The names of the instruments whose kernels have
            been loaded.
        initialized (bool): True after :meth:`initialize` has been called.
    """

    START_TIME = '1979-01-06'
    STOP_TIME  = '1989-10-02'

    SPACECRAFT_IDS = {1: -31, 2: -32}
    PATH_IDS       = {1: 'VOYAGER1', 2: 'VOYAGER2'}
    ENCOUNTERS     = {1: ('JUP', 'SAT'), 2: ('JUP', 'SAT', 'URA', 'NEP')}

    loaded_instruments = []
    initialized = False

    @staticmethod
    def initialize(planets=None, asof=None, irregulars=True):
        """Initialize the Voyager mission internals.

        Defines the Solar System bodies, furnishes the C kernels and the SP kernels of
        every encounter for both spacecraft, and defines the Paths of the spacecraft,
        :attr:`PATH_IDS`, relative to the Solar System barycenter. After the first call,
        later calls to this function are ignored.

        Parameters:
            planets (list, optional): A list of planets to pass to
                :meth:`~oops.Body.define_solar_system`. None or 0 means all.
            asof (str, optional): Only use SPICE kernels that existed before this date;
                None to ignore.
            irregulars (bool, optional): True to include the irregular satellites; False
                otherwise.
        """

        if _Voyager.initialized:
            return

        asof = _Voyager._format_asof(asof)

        # Make sure the leap seconds have been loaded
        oops.spice.load_leap_seconds()

        Body.define_solar_system(_Voyager.START_TIME, _Voyager.STOP_TIME, asof=asof,
                                 planets=planets, irregulars=irregulars)

        spicedb.open_db()
        for vgr, naif_id in _Voyager.SPACECRAFT_IDS.items():
            _ = spicedb.furnish_ck(naif_id, asof=asof)
            for planet in _Voyager.ENCOUNTERS[vgr]:
                _ = spicedb.furnish_spk(naif_id, name=f'%{planet}%', asof=asof)
        spicedb.close_db()

        for vgr, path_id in _Voyager.PATH_IDS.items():
            _ = oops.path.SpicePath(f'VOYAGER {vgr}', path_id=path_id)

        _Voyager.initialized = True

    @staticmethod
    def reset():
        """Reset the internal parameters.

        Can be useful for debugging.
        """

        _Voyager.loaded_instruments = []
        _Voyager.initialized = False

    @staticmethod
    def load_instruments(instruments=None, asof=None):
        """Furnish the instrument, frame and clock kernels of both spacecraft.

        Parameters:
            instruments (list[str], optional): The names of the instruments whose kernels
                to load. On the first call, 'ISS' is always included.
            asof (str, optional): If this specifies a date or date-time in ISO format,
                then only kernels that existed before the specified date are used.
                Otherwise, the most recent versions are always loaded.
        """

        # Copy so that the caller's list is never modified
        instruments = [] if instruments is None else list(instruments)

        # Load the default instruments on the first pass
        if _Voyager.loaded_instruments == []:
            instruments += ['ISS']

        # Skip instruments that are already loaded, and duplicates
        instruments = [inst for inst in dict.fromkeys(instruments)
                       if inst not in _Voyager.loaded_instruments]

        # On later calls, return quickly if there's nothing to do
        if instruments == []:
            return

        spicedb.open_db()
        _ = spicedb.furnish_inst(list(_Voyager.SPACECRAFT_IDS.values()), inst=instruments,
                                 asof=_Voyager._format_asof(asof))
        spicedb.close_db()

        _Voyager.loaded_instruments += instruments

    @staticmethod
    def used_kernels(time, vgr, inst, return_all_planets=False, ck=True):
        """The kernels associated with a Voyager observation.

        Parameters:
            time (tuple[float, float]): A (start, stop) tuple of times in seconds TDB.
            vgr (int): The spacecraft number, 1 or 2.
            inst (str): A fragment of the instrument kernel's file name that identifies
                the instrument, such as "issna" or "isswa"; case is ignored.
            return_all_planets (bool, optional): Include kernels for all planets, not just
                the planet of the encounter containing the start time.
            ck (bool, optional): True (default) to include CK (pointing) kernels; False to
                exclude them, e.g. for an observation whose pointing came from a custom
                frame rather than SPICE, where any furnished CK is unrelated to how the
                observation was actually pointed.

        Returns:
            list[str]: The basenames of the furnished kernels that apply to the
            observation.
        """

        if return_all_planets:
            targets = _ALL_PLANETS
        else:
            targets = [_Voyager.encounter(time[0])]

        # The planet, its barycenter, and its moons
        bodies = []
        for target in targets:
            body = cspyce.bodn2c(target)
            bodies += [body // 100, body]
            bodies += getattr(Body, target + '_MOONS_LOADED', [])

        types = None
        if not ck:
            types = [t for t in spicedb.KERNEL_TYPE_SORT_ORDER if t != 'CK']

        # spicedb compares times in seconds TAI
        time_tai = tuple(julian.tai_from_tdb(t) for t in time)
        return spicedb.used_basenames(types=types, time=time_tai, inst=inst,
                                      sc=_Voyager.SPACECRAFT_IDS[vgr], bodies=bodies)

    @staticmethod
    def encounter(tdb):
        """The planet of the encounter containing a time.

        Parameters:
            tdb (float): The time in seconds TDB.

        Returns:
            str: "JUPITER", "SATURN", "URANUS" or "NEPTUNE". A time before the first
            encounter is assigned to Jupiter.
        """

        target = TIMELINE[0]['target']
        for phase in TIMELINE:
            if tdb >= phase['TDB']:
                target = phase['target']
        return target

    @staticmethod
    def _format_asof(asof):
        """An "as of" date reformatted as the ISO date-time spicedb expects, or None."""

        if asof is None:
            return None
        (day, sec) = julian.day_sec_from_iso(asof)
        return julian.ymdhms_format_from_day_sec(day, sec)

##########################################################################################
