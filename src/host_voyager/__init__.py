##########################################################################################
# host_voyager/__init__.py
##########################################################################################
"""The Voyager mission hosts for oops.

The host classes are exported here but imported only on first access, so that importing
:mod:`host_voyager` does not import oops. Importing ``host_voyager.oops`` registers every
host with ``oops.Host``.
"""

import importlib

__all__ = ['VoyagerISS']

# Exported name -> the module that defines it
_LAZY_NAMES = {
    'VoyagerISS': 'host_voyager.oops',
}


def __getattr__(name):
    """Import an oops host class on first access (PEP 562).

    Parameters:
        name (str): The attribute name.

    Returns:
        type: The host class exported under `name`.

    Raises:
        AttributeError: If `name` is not an exported host class.
    """

    if name in _LAZY_NAMES:
        return getattr(importlib.import_module(_LAZY_NAMES[name]), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

##########################################################################################
