##########################################################################################
# host_voyager/__init__.py
##########################################################################################
"""The Voyager mission hosts for the OOPS observation geometry toolkit.

The package exports :class:`~host_voyager.oops.iss.VoyagerISS`. Importing
``host_voyager`` does not import oops; the first access to the class imports
:mod:`host_voyager.oops`, which registers the host with :class:`~oops.host.Host`.
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
