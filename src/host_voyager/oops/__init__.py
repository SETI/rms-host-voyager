##########################################################################################
# host_voyager/oops/__init__.py
##########################################################################################
"""The Voyager hosts for oops; importing each instrument module registers its host."""

# Importing each instrument module registers its host with oops.Host
from .iss import VoyagerISS

__all__ = ['VoyagerISS']

##########################################################################################
