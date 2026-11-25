##########################################################################################
# tests/oops/test_voyager.py
##########################################################################################
"""Tests for the Voyager mission support, host_voyager.oops._voyager."""

from typing import Any

import julian
import oops
import pytest
import spicedb
from conftest import CLOCK_COUNT, FakePrefix, FakeSpice, FakeSpiceDB, fake_scencd
from oops.body import Body

from host_voyager.oops import _voyager
from host_voyager.oops._voyager import TIMELINE, _Voyager

##########################################################################################
# Mission timeline
##########################################################################################


def test_timeline_times_follow_their_dates() -> None:
    """Each phase's TDB is the time of its UTC date."""

    assert [phase['TDB'] for phase in TIMELINE] == pytest.approx(
        [julian.tdb_from_tai(julian.tai_from_iso(phase['UTC'])) for phase in TIMELINE],
        abs=1.e-3)


@pytest.mark.parametrize(('iso', 'planet'), [
    ('1977-09-05T12:56:00', 'JUPITER'),
    ('1979-12-31T23:59:59', 'JUPITER'),
    ('1980-01-01T00:00:00', 'SATURN'),
    ('1986-01-24T17:59:47', 'URANUS'),
    ('1989-08-25T03:56:36', 'NEPTUNE'),
], ids=['before-the-first', 'last-jupiter-second', 'saturn-boundary', 'uranus',
        'neptune'])
def test_encounter(iso: str, planet: str) -> None:
    """A time belongs to the encounter that began last before it."""

    assert _Voyager.encounter(julian.tdb_from_iso(iso)) == planet

##########################################################################################
# initialize() and reset()
##########################################################################################


def test_initialize_defines_the_solar_system(fake_spice: FakeSpice) -> None:
    """The Solar System is defined over the whole mission, with the caller's options and
    the asof date as given."""

    _Voyager.initialize(planets=[6], asof='2000-01-01', irregulars=False)
    assert fake_spice.solar_system == [(('1978-12-01', '1989-10-02'),
                                        {'asof': '2000-01-01', 'planets': [6],
                                         'irregulars': False})]


def test_initialize_loads_the_leap_seconds(fake_spice: FakeSpice) -> None:
    """The leap seconds are loaded."""

    _Voyager.initialize()
    assert fake_spice.leap_seconds == 1


def test_initialize_defines_the_spacecraft_paths(fake_spice: FakeSpice) -> None:
    """A SpicePath is defined for each spacecraft."""

    _Voyager.initialize()
    assert fake_spice.spice_paths == [('VOYAGER 1', 'VOYAGER1'), ('VOYAGER 2', 'VOYAGER2')]


def test_initialize_furnishes_the_cks(fake_spicedb: FakeSpiceDB) -> None:
    """The C kernels of both spacecraft are furnished, with the asof date as given."""

    _Voyager.initialize(asof='1995-03-04')
    assert fake_spicedb.named('furnish_ck') == [((-31,), {'asof': '1995-03-04'}),
                                                ((-32,), {'asof': '1995-03-04'})]


def test_initialize_furnishes_the_encounter_spks(fake_spicedb: FakeSpiceDB) -> None:
    """The SP kernels of every encounter of each spacecraft are furnished."""

    _Voyager.initialize()
    assert fake_spicedb.named('furnish_spk') == [
        ((-31,), {'name': '%JUP%', 'asof': None}),
        ((-31,), {'name': '%SAT%', 'asof': None}),
        ((-32,), {'name': '%JUP%', 'asof': None}),
        ((-32,), {'name': '%SAT%', 'asof': None}),
        ((-32,), {'name': '%URA%', 'asof': None}),
        ((-32,), {'name': '%NEP%', 'asof': None}),
    ]


def test_initialize_closes_the_database(fake_spicedb: FakeSpiceDB) -> None:
    """The database is opened before the kernels are furnished and closed after."""

    _Voyager.initialize()
    names = [name for (name, _, _) in fake_spicedb.calls]
    assert names.index('open_db') < names.index('furnish_ck')
    assert names[-1] == 'close_db'


CRUISE_1 = 'Voyager/SPK/voyager_1.ST+1991_a54418u.merged.bsp'
CRUISE_2 = 'Voyager/SPK/voyager_2.ST+1992_m05208u.merged.bsp'


def test_initialize_reads_the_cruise_spk_coverage(fake_spice: FakeSpice) -> None:
    """Each merged cruise file is read locally for its spacecraft's coverage."""

    _Voyager.initialize()
    assert fake_spice.spkcov == [(str(FakePrefix.ROOT / CRUISE_1), -31),
                                 (str(FakePrefix.ROOT / CRUISE_2), -32)]


def test_initialize_furnishes_the_cruise_spks(fake_spicedb: FakeSpiceDB) -> None:
    """Each merged file is furnished by its relative path with a record for its
    spacecraft alone, over the file's coverage."""

    _Voyager.initialize()
    (args, _) = fake_spicedb.named('furnish_kernels')[0]
    assert [(spice_id, filespec, start[:10], stop[:10])
            for (spice_id, filespec, start, stop) in args[0]] == [
        (-31, CRUISE_1, '1977-09-08', '2021-01-01'),
        (-32, CRUISE_2, '1977-08-23', '2021-01-01')]


def test_initialize_furnishes_the_cruise_spks_first(fake_spicedb: FakeSpiceDB) -> None:
    """The encounter kernels are furnished after the cruise ones, and so take precedence
    over them."""

    _Voyager.initialize()
    names = [name for (name, _, _) in fake_spicedb.calls]
    assert names.index('furnish_kernels') < names.index('furnish_spk')


def spk(name: str, filespec: str, spice_id: int) -> spicedb.KernelInfo:
    """A spicedb record of an SP kernel; `filespec` may name several files, separated by
    commas."""

    return spicedb.KernelInfo([name, None, 'SPK', filespec, '1950-01-01T00:00:00',
                               '2050-01-01T00:00:00', '2020-01-01', spice_id, 100, None])


# Two planetary files and the spacecraft file of an encounter, whose spicedb record names
# both the encounter's planetary file and its spacecraft file
SAT286 = 'Saturn/SPK/old/sat286.bsp'
SAT441 = 'Saturn/SPK/sat441.bsp'
VGR1_SAT286 = 'Voyager/SPK/old/vgr1_sat286.bsp'
EARLIER = [SAT286, SAT441, VGR1_SAT286]


def furnish_earlier_spks() -> None:
    """Furnish SAT286, SAT441 and the encounter record of SAT286 and VGR1_SAT286, so that
    SAT441 lies between the two files of one record."""

    spicedb.furnish_kernels([spk('SAT286', SAT286, 699)])
    spicedb.furnish_kernels([spk('SAT441', SAT441, 699)])
    spicedb.furnish_kernels([spk('VG1-SPK-SAT', f'{SAT286},{VGR1_SAT286}', -31)])


def test_initialize_raises_earlier_spks_above_the_cruise_spks(
        fake_spice: FakeSpice) -> None:
    """SP kernel files furnished before the cruise ones are furnished again after them, in
    their own load order, so that they keep precedence over them, also where another file
    was furnished between two files of one record."""

    furnish_earlier_spks()
    _Voyager.initialize()
    loaded = [path for (action, path) in fake_spice.loads if action == 'furnsh']
    assert loaded[-3:] == [FakePrefix.ROOT / filespec for filespec in EARLIER]


def test_initialize_keeps_the_spicedb_load_order(fake_spicedb: FakeSpiceDB) -> None:
    """spicedb's list of the furnished SP kernels follows the order in which they were
    loaded."""

    furnish_earlier_spks()
    _Voyager.initialize()
    assert fake_spicedb.furnished_spks == [FakePrefix.ROOT / filespec for filespec
                                           in [CRUISE_1, CRUISE_2, *EARLIER]]


def test_initialize_replays_nothing_without_a_new_cruise_spk(
        fake_spice: FakeSpice) -> None:
    """When both cruise files are already furnished, no file is furnished again."""

    spicedb.furnish_kernels([spk('CRUISE-1', CRUISE_1, -31), spk('CRUISE-2', CRUISE_2, -32),
                             spk('SAT441', SAT441, 699)])
    fake_spice.loads.clear()
    _Voyager.initialize()
    assert fake_spice.loads == []


@pytest.mark.parametrize(('asof', 'furnished'), [
    ('2013-01-01', []),
    ('2014-01-01', [CRUISE_1, CRUISE_2]),
])
def test_initialize_cruise_spks_honor_asof(fake_spicedb: FakeSpiceDB, asof: str,
                                           furnished: list[str]) -> None:
    """A merged file released after `asof` is skipped."""

    _Voyager.initialize(asof=asof)
    ((args, _),) = fake_spicedb.named('furnish_kernels')
    assert [filespec for (_, filespec, _, _) in args[0]] == furnished


def test_initialize_closes_the_database_on_failure(fake_spicedb: FakeSpiceDB,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure to furnish a kernel still closes the database."""

    def no_ck(*args: Any, **kwargs: Any) -> list[str]:
        raise ValueError('no C kernel')

    monkeypatch.setattr('spicedb.furnish_ck', no_ck)
    with pytest.raises(ValueError, match='no C kernel'):
        _Voyager.initialize()
    assert fake_spicedb.calls[-1][0] == 'close_db'


def test_initialize_runs_once(fake_spice: FakeSpice) -> None:
    """A second call does nothing."""

    _Voyager.initialize()
    _Voyager.initialize()
    assert len(fake_spice.solar_system) == 1


def test_reset_clears_state() -> None:
    """reset() forgets the initialization and the loaded instruments."""

    _Voyager.initialize()
    _Voyager.load_instruments()
    _Voyager.reset()
    assert not _Voyager.initialized
    assert _Voyager.loaded_instruments == []

##########################################################################################
# load_instruments()
##########################################################################################


def test_load_instruments_includes_iss_on_the_first_call(fake_spicedb: FakeSpiceDB) -> None:
    """The first call furnishes the ISS kernels of both spacecraft, with asof as given."""

    _Voyager.load_instruments(asof='2000-02-03')
    assert fake_spicedb.named('furnish_inst') == [(([-31, -32],),
                                                   {'inst': ['ISS'],
                                                    'asof': '2000-02-03'})]


def test_load_instruments_closes_the_database_on_failure(
        fake_spicedb: FakeSpiceDB, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure to furnish the kernels still closes the database."""

    def no_inst(*args: Any, **kwargs: Any) -> list[str]:
        raise ValueError('no instrument kernel')

    monkeypatch.setattr('spicedb.furnish_inst', no_inst)
    with pytest.raises(ValueError, match='no instrument kernel'):
        _Voyager.load_instruments()
    assert fake_spicedb.calls[-1][0] == 'close_db'


def test_load_instruments_skips_loaded_instruments(fake_spicedb: FakeSpiceDB) -> None:
    """A call naming only loaded instruments furnishes nothing."""

    _Voyager.load_instruments()
    _Voyager.load_instruments(['ISS', 'ISS'])
    assert len(fake_spicedb.named('furnish_inst')) == 1


def test_load_instruments_adds_new_instruments(fake_spicedb: FakeSpiceDB) -> None:
    """A later call furnishes only the instruments not yet loaded."""

    _Voyager.load_instruments()
    _Voyager.load_instruments(['UVS', 'ISS'])
    assert fake_spicedb.named('furnish_inst')[1][1]['inst'] == ['UVS']


def test_load_instruments_leaves_the_callers_list_alone() -> None:
    """The caller's list is not changed."""

    instruments = ['UVS']
    _Voyager.load_instruments(instruments)
    assert instruments == ['UVS']

##########################################################################################
# used_kernels()
##########################################################################################

_SATURN_TDB = julian.tdb_from_iso('1980-11-12T23:46:00')


def _used_kwargs(fake_spicedb: FakeSpiceDB, tdb: float = _SATURN_TDB,
                 **kwargs: Any) -> dict[str, Any]:
    """The keywords that used_kernels() passes to spicedb.used_basenames()."""

    assert _Voyager.used_kernels((tdb, tdb + 1.), 1, 'issna', **kwargs) == fake_spicedb.used
    return fake_spicedb.named('used_basenames')[-1][1]


def test_used_kernels_encounter_bodies(fake_spicedb: FakeSpiceDB) -> None:
    """The bodies are the encounter planet, its barycenter and its moons."""

    assert _used_kwargs(fake_spicedb)['bodies'] == [6, 699, *Body.SATURN_MOONS_LOADED]


def test_used_kernels_all_planets(fake_spicedb: FakeSpiceDB) -> None:
    """With return_all_planets, every planet is included."""

    bodies = _used_kwargs(fake_spicedb, return_all_planets=True)['bodies']
    assert [body for body in bodies if body < 10 or body % 100 == 99] == [
        1, 199, 2, 299, 3, 399, 4, 499, 5, 599, 6, 699, 7, 799, 8, 899]


def test_used_kernels_passes_the_query(fake_spicedb: FakeSpiceDB) -> None:
    """The instrument, spacecraft and kernel types are passed on."""

    kwargs = _used_kwargs(fake_spicedb)
    assert kwargs['types'] is None
    assert kwargs['inst'] == 'issna'
    assert kwargs['sc'] == -31


def test_used_kernels_passes_tai(fake_spicedb: FakeSpiceDB) -> None:
    """The times are converted to TAI."""

    time = _used_kwargs(fake_spicedb)['time']
    # TDB - TAI is about 32 s, so the tolerance must be far tighter than that
    assert time == pytest.approx((julian.tai_from_tdb(_SATURN_TDB),
                                  julian.tai_from_tdb(_SATURN_TDB + 1.)), abs=1.e-3)


def test_used_kernels_without_cks(fake_spicedb: FakeSpiceDB) -> None:
    """With ck=False, the C kernels are left out of the types."""

    types_ = _used_kwargs(fake_spicedb, ck=False)['types']
    assert 'CK' not in types_

##########################################################################################
# sclk_ticks()
##########################################################################################


def test_sclk_ticks_in_the_imaging_periods() -> None:
    """The count is encoded in the one partition that puts it in an imaging period."""

    # Partition 1 refuses the count; partition 3 puts it six years later
    assert _Voyager.sclk_ticks(1, CLOCK_COUNT) == fake_scencd(-31, f'2/{CLOCK_COUNT}')


def test_sclk_ticks_outside_the_imaging_periods() -> None:
    """A count in no imaging period gives None."""

    # About 480 days before STOP_TIME in partition 2, and 6 years later in partition 3
    assert _Voyager.sclk_ticks(1, '20000:00:001') is None


def test_sclk_ticks_in_two_imaging_periods(monkeypatch: pytest.MonkeyPatch) -> None:
    """A count that two partitions put in imaging periods gives None."""

    monkeypatch.setitem(_voyager._IMAGING_PERIODS_TDB, 1, [(-1.e10, 1.e10)])
    assert _Voyager.sclk_ticks(1, CLOCK_COUNT) is None


@pytest.mark.parametrize('error', [ValueError, RuntimeError],
                         ids=['exception-action', 'runtime-action'])
def test_sclk_ticks_skips_a_partition_without_the_count(
        monkeypatch: pytest.MonkeyPatch, error: type[Exception]) -> None:
    """A partition that refuses the count is skipped under either error action of
    cspyce."""

    def scencd(naif_id: int, count: str) -> float:
        if count.startswith('1/'):
            raise error(f'SPICE(NOTINPART) -- scencd -- {count}')
        return fake_scencd(naif_id, count)

    monkeypatch.setattr('cspyce.scencd', scencd)
    assert _Voyager.sclk_ticks(1, CLOCK_COUNT) == fake_scencd(-31, f'2/{CLOCK_COUNT}')


def test_sclk_ticks_raises_other_spice_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any other error of the clock kernel propagates."""

    def scencd(naif_id: int, count: str) -> float:
        raise RuntimeError('SPICE(KERNELVARNOTFOUND) -- scencd')

    monkeypatch.setattr('cspyce.scencd', scencd)
    with pytest.raises(RuntimeError, match='KERNELVARNOTFOUND'):
        _Voyager.sclk_ticks(1, CLOCK_COUNT)


def test_paths_are_registered_after_initialize() -> None:
    """The spacecraft Paths are registered under their IDs."""

    _Voyager.initialize()
    assert oops.Path.as_path('VOYAGER2').path_id == 'VOYAGER2'

##########################################################################################
