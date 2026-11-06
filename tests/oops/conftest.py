##########################################################################################
# tests/oops/conftest.py
##########################################################################################
"""Shared fixtures that let the Voyager host run without SPICE kernels or spicedb.

Every spicedb function the package calls is replaced by :class:`FakeSpiceDB`, and the
oops calls that would read SPICE kernels are replaced by kernel-free equivalents.
Everything else, including oops itself, runs for real.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import oops
import pytest
import spicedb

from host_voyager.oops._voyager import _Voyager


@dataclass
class FakeSpiceDB:
    """Records every spicedb call and returns configurable results.

    Attributes:
        calls (list[tuple[str, tuple, dict]]): Each call as (name, args, kwargs).
        used (list[str]): Returned by `used_basenames`.
    """

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    used: list[str] = field(default_factory=lambda: ['vg1_issna_v02.ti', 'vg100019.tsc'])

    def _record(self, func: str, /, *args: Any, **kwargs: Any) -> None:
        self.calls.append((func, args, kwargs))

    def named(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        """The (args, kwargs) of every recorded call to the named function."""

        return [(args, kwargs) for (n, args, kwargs) in self.calls if n == name]

    def open_db(self, *args: Any, **kwargs: Any) -> None:
        self._record('open_db', *args, **kwargs)

    def close_db(self, *args: Any, **kwargs: Any) -> None:
        self._record('close_db', *args, **kwargs)

    def furnish_inst(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_inst', *args, **kwargs)
        return []

    def furnish_ck(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_ck', *args, **kwargs)
        return []

    def furnish_spk(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('furnish_spk', *args, **kwargs)
        return []

    def used_basenames(self, *args: Any, **kwargs: Any) -> list[str]:
        self._record('used_basenames', *args, **kwargs)
        return self.used


_SPICEDB_FUNCTIONS = ('open_db', 'close_db', 'furnish_inst', 'furnish_ck', 'furnish_spk',
                      'used_basenames')


@dataclass
class FakeSpice:
    """Records the oops calls that would otherwise read SPICE kernels.

    Attributes:
        leap_seconds (int): The number of calls to `oops.spice.load_leap_seconds`.
        solar_system (list[tuple[tuple, dict]]): The (args, kwargs) of each call to
            `Body.define_solar_system`.
        spice_paths (list[tuple[str, str]]): The (SPICE name, path ID) of each SpicePath.
    """

    leap_seconds: int = 0
    solar_system: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list)
    spice_paths: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def fake_spicedb(monkeypatch: pytest.MonkeyPatch) -> FakeSpiceDB:
    """Replace every spicedb function the package calls with a recording fake."""

    fake = FakeSpiceDB()
    for name in _SPICEDB_FUNCTIONS:
        monkeypatch.setattr(spicedb, name, getattr(fake, name))
    return fake


@pytest.fixture(autouse=True)
def fake_spice(monkeypatch: pytest.MonkeyPatch) -> FakeSpice:
    """Keep SPICE kernels out of every test and isolate the oops registries.

    Loading the leap seconds and defining the Solar System only record the call, and a
    SpicePath becomes a FixedPath at the Solar System barycenter. The Path and Frame
    registries are copied so that anything registered by one test never reaches another.
    """

    fake = FakeSpice()

    def load_leap_seconds() -> None:
        fake.leap_seconds += 1

    def define_solar_system(*args: Any, **kwargs: Any) -> None:
        fake.solar_system.append((args, kwargs))

    def spice_path(spice_name: str, *, path_id: str) -> oops.Path:
        fake.spice_paths.append((spice_name, path_id))
        return oops.path.FixedPath((0., 0., 0.), oops.Path.SSB, oops.Frame.J2000,
                                   path_id=path_id)

    monkeypatch.setattr(oops.spice, 'load_leap_seconds', load_leap_seconds)
    monkeypatch.setattr(oops.Body, 'define_solar_system', staticmethod(define_solar_system))
    monkeypatch.setattr(oops.path, 'SpicePath', spice_path)

    monkeypatch.setattr(oops.Path, '_PATH_REGISTRY', dict(oops.Path._PATH_REGISTRY))
    monkeypatch.setattr(oops.Path, '_PATH_CACHE', dict(oops.Path._PATH_CACHE))
    monkeypatch.setattr(oops.Frame, '_FRAME_REGISTRY', dict(oops.Frame._FRAME_REGISTRY))
    monkeypatch.setattr(oops.Frame, '_FRAME_CACHE', dict(oops.Frame._FRAME_CACHE))
    return fake


@pytest.fixture(autouse=True)
def reset_host() -> Iterator[None]:
    """Start and end every test with the Voyager class state reset."""

    _Voyager.reset()
    yield
    _Voyager.reset()

##########################################################################################
