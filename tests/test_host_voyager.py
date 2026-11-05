##########################################################################################
# tests/test_host_voyager.py
##########################################################################################
"""Tests of the top-level :mod:`host_voyager` package."""

import subprocess
import sys

import pytest

import host_voyager


def test_unknown_attribute_raises() -> None:
    """An unexported name raises AttributeError naming the module and the attribute."""

    with pytest.raises(AttributeError,
                       match="module 'host_voyager' has no attribute 'NoSuchHost'"):
        _ = host_voyager.NoSuchHost


def test_import_does_not_import_oops() -> None:
    """Importing host_voyager leaves oops unimported."""

    code = 'import sys, host_voyager; print("oops" in sys.modules)'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                            check=True)
    assert result.stdout.strip() == 'False'

##########################################################################################
