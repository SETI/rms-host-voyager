##########################################################################################
# host_voyager/oops/__init__.py
##########################################################################################
"""The Voyager hosts for oops.

The host is :class:`~host_voyager.oops.iss.VoyagerISS`, documented with its module below.
Importing this package registers it with :class:`~oops.host.Host`, so that its
``from_file()`` and ``from_index()`` recognize Voyager ISS images and raw image
indexes.
"""

# Importing each instrument module registers its host with oops.Host
from .iss import VoyagerISS

__all__ = ['VoyagerISS']

##########################################################################################
