##########################################################################################
# tests/oops/test_voyager.py
##########################################################################################
"""Tests for the Voyager mission support, host_voyager.oops._voyager."""

from typing import Any

import julian
import oops
import pytest
from conftest import (CLOCK_COUNT, CYCLE_SECONDS, STOP_TIME, FakeSpice, FakeSpiceDB,
                      fake_scencd)
from oops.body import Body

from host_voyager.oops._voyager import TIMELINE, _Voyager

##########################################################################################
# Mission timeline
##########################################################################################


def test_timeline_times_follow_their_dates() -> None:
    assert [phase['TDB'] for phase in TIMELINE] == pytest.approx(
        [julian.tdb_from_tai(julian.tai_from_iso(phase['UTC'])) for phase in TIMELINE])


def test_timeline_is_in_order() -> None:
    times = [phase['TDB'] for phase in TIMELINE]
    assert times == sorted(times)


@pytest.mark.parametrize(('iso', 'planet'), [
    ('1977-09-05T12:56:00', 'JUPITER'),
    ('1979-03-05T12:05:26', 'JUPITER'),
    ('1979-12-31T23:59:59', 'JUPITER'),
    ('1980-01-01T00:00:00', 'SATURN'),
    ('1981-08-26T03:24:05', 'SATURN'),
    ('1986-01-24T17:59:47', 'URANUS'),
    ('1989-08-25T03:56:36', 'NEPTUNE'),
], ids=['before-launch-window', 'vg1-jupiter', 'last-jupiter-second', 'saturn-boundary',
        'vg2-saturn', 'uranus', 'neptune'])
def test_encounter(iso: str, planet: str) -> None:
    assert _Voyager.encounter(julian.tdb_from_iso(iso)) == planet

##########################################################################################
# initialize() and reset()
##########################################################################################


def test_initialize_defines_the_solar_system(fake_spice: FakeSpice) -> None:
    _Voyager.initialize(planets=[6], asof='2000-01-01', irregulars=False)
    assert fake_spice.solar_system == [(('1979-01-06', '1989-10-02'),
                                        {'asof': '2000-01-01T00:00:00', 'planets': [6],
                                         'irregulars': False})]
    assert fake_spice.leap_seconds == 1


def test_initialize_defines_the_spacecraft_paths(fake_spice: FakeSpice) -> None:
    _Voyager.initialize()
    assert fake_spice.spice_paths == [('VOYAGER 1', 'VOYAGER1'), ('VOYAGER 2', 'VOYAGER2')]
    assert oops.Path.as_path('VOYAGER2').path_id == 'VOYAGER2'


def test_initialize_furnishes_the_cks(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.initialize()
    assert fake_spicedb.named('furnish_ck') == [((-31,), {'asof': None}),
                                                ((-32,), {'asof': None})]


def test_initialize_furnishes_the_encounter_spks(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.initialize()
    assert fake_spicedb.named('furnish_spk') == [
        ((-31,), {'name': '%JUP%', 'asof': None}),
        ((-31,), {'name': '%SAT%', 'asof': None}),
        ((-32,), {'name': '%JUP%', 'asof': None}),
        ((-32,), {'name': '%SAT%', 'asof': None}),
        ((-32,), {'name': '%URA%', 'asof': None}),
        ((-32,), {'name': '%NEP%', 'asof': None}),
    ]


def test_initialize_formats_asof_for_spicedb(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.initialize(asof='1995-03-04 12:00')
    asofs = {kwargs['asof'] for (name, _, kwargs) in fake_spicedb.calls
             if name.startswith('furnish')}
    assert asofs == {'1995-03-04T12:00:00'}


def test_initialize_opens_and_closes_the_database(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.initialize()
    names = [name for (name, _, _) in fake_spicedb.calls]
    assert names[0] == 'open_db'
    assert names[-1] == 'close_db'


def test_initialize_runs_once(fake_spice: FakeSpice, fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.initialize()
    _Voyager.initialize()
    assert len(fake_spice.solar_system) == 1
    assert len(fake_spicedb.named('furnish_ck')) == 2
    assert _Voyager.initialized


def test_reset_clears_state() -> None:
    _Voyager.initialize()
    _Voyager.load_instruments()
    _Voyager.reset()
    assert not _Voyager.initialized
    assert _Voyager.loaded_instruments == []

##########################################################################################
# load_instruments()
##########################################################################################


def test_load_instruments_includes_iss_on_the_first_call(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.load_instruments()
    assert fake_spicedb.named('furnish_inst') == [(([-31, -32],),
                                                   {'inst': ['ISS'], 'asof': None})]
    assert _Voyager.loaded_instruments == ['ISS']


def test_load_instruments_skips_loaded_instruments(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.load_instruments()
    _Voyager.load_instruments(['ISS', 'ISS'])
    assert len(fake_spicedb.named('furnish_inst')) == 1


def test_load_instruments_adds_new_instruments(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.load_instruments()
    _Voyager.load_instruments(['UVS', 'ISS'])
    assert fake_spicedb.named('furnish_inst')[1][1]['inst'] == ['UVS']
    assert _Voyager.loaded_instruments == ['ISS', 'UVS']


def test_load_instruments_leaves_the_callers_list_alone() -> None:
    instruments = ['UVS']
    _Voyager.load_instruments(instruments)
    assert instruments == ['UVS']
    assert _Voyager.loaded_instruments == ['UVS', 'ISS']


def test_load_instruments_formats_asof(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.load_instruments(asof='2000-02-03')
    assert fake_spicedb.named('furnish_inst')[0][1]['asof'] == '2000-02-03T00:00:00'

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
    bodies = _used_kwargs(fake_spicedb)['bodies']
    assert bodies == [6, 699, *Body.SATURN_MOONS_LOADED]


def test_used_kernels_neptune_bodies(fake_spicedb: FakeSpiceDB) -> None:
    tdb = julian.tdb_from_iso('1989-08-25T03:56:36')
    bodies = _used_kwargs(fake_spicedb, tdb)['bodies']
    assert bodies == [8, 899, *Body.NEPTUNE_MOONS_LOADED]


def test_used_kernels_all_planets(fake_spicedb: FakeSpiceDB) -> None:
    bodies = _used_kwargs(fake_spicedb, return_all_planets=True)['bodies']
    assert bodies[:6] == [1, 199, 2, 299, 3, 399]
    assert 899 in bodies


def test_used_kernels_passes_the_query(fake_spicedb: FakeSpiceDB) -> None:
    kwargs = _used_kwargs(fake_spicedb)
    assert kwargs['types'] is None
    assert kwargs['inst'] == 'issna'
    assert kwargs['sc'] == -31


def test_used_kernels_passes_tai(fake_spicedb: FakeSpiceDB) -> None:
    time = _used_kwargs(fake_spicedb)['time']
    # TDB - TAI is about 32 s, so the tolerance must be far tighter than that
    assert time == pytest.approx((julian.tai_from_tdb(_SATURN_TDB),
                                  julian.tai_from_tdb(_SATURN_TDB + 1.)), abs=1.e-3)


def test_used_kernels_voyager_2(fake_spicedb: FakeSpiceDB) -> None:
    _Voyager.used_kernels((_SATURN_TDB, _SATURN_TDB), 2, 'isswa')
    assert fake_spicedb.named('used_basenames')[0][1]['sc'] == -32


def test_used_kernels_without_cks(fake_spicedb: FakeSpiceDB) -> None:
    types = _used_kwargs(fake_spicedb, ck=False)['types']
    assert 'CK' not in types
    assert 'SPK' in types
##########################################################################################
# sclk_ticks()
##########################################################################################

_STOP_TDB = julian.tdb_from_iso(STOP_TIME)


def test_sclk_ticks_in_a_given_partition() -> None:
    assert _Voyager.sclk_ticks(1, CLOCK_COUNT, 3) == fake_scencd(-31, f'3/{CLOCK_COUNT}')


@pytest.mark.parametrize(('near', 'partition'), [
    (_STOP_TDB, 2), (_STOP_TDB + CYCLE_SECONDS, 3), (_STOP_TDB + 0.6 * CYCLE_SECONDS, 3)],
    ids=['stop-time', 'one-cycle-later', 'nearer-the-later'])
def test_sclk_ticks_partition_nearest_a_time(near: float, partition: int) -> None:
    assert _Voyager.sclk_ticks(2, CLOCK_COUNT, near=near) == fake_scencd(
        -32, f'{partition}/{CLOCK_COUNT}')


def test_sclk_ticks_rejects_a_count_in_no_partition(monkeypatch: pytest.MonkeyPatch
                                                    ) -> None:
    def no_partition(naif_id: int, count: str) -> float:
        raise ValueError('SPICE(NOTINPART)')

    monkeypatch.setattr('cspyce.scencd', no_partition)
    with pytest.raises(ValueError, match=f'Voyager 1 clock count {CLOCK_COUNT} is in no '
                                         'partition'):
        _Voyager.sclk_ticks(1, CLOCK_COUNT, near=_STOP_TDB)

##########################################################################################
