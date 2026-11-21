=====
Setup
=====

Installation
============

The package needs Python 3.11 or later and installs from PyPI:

.. code-block:: sh

   pip install rms-host-voyager

It brings in OOPS (``rms-oops``) and the readers that the host uses for PDS3 labels,
VICAR files, and PDS3 index tables.

SPICE kernels
=============

The geometry of every image comes from the Voyager SPICE kernels. The host reads them
through ``spicedb``, which is distributed with OOPS and finds them with two environment
variables:

``SPICE_PATH``
    The directory that holds the kernels, as a local path or a URL. If it is not set,
    ``$OOPS_RESOURCES/SPICE`` is used.

``SPICE_SQLITE_DB_NAME``
    The SQLite database that lists the kernels. If it is not set, ``SPICE.db`` in the
    ``SPICE_PATH`` directory is used.

The kernels and the database are the ones that OOPS itself uses. The
`rms-oops README <https://github.com/SETI/rms-oops#environment-variables>`_ describes
these variables and the ``SPICE`` directory of ``$OOPS_RESOURCES`` that holds them.
The Ring-Moon Systems Node publishes these resources, the kernels and ``SPICE.db``
included, at ``https://storage.googleapis.com/rms-node-oops-resources``, a URL that
``OOPS_RESOURCES`` can name directly.

For both spacecraft, the host furnishes the kernels that the database lists: the scan
platform C kernels, the spacecraft ephemeris of each planetary encounter, and the
instrument, frame, and spacecraft clock kernels. The encounter ephemerides cover only a
few weeks around each closest approach, while the cameras imaged each planet for months
before it, so the host also furnishes NAIF's merged trajectory of each spacecraft for the
whole mission. These two files are read from fixed paths under ``Voyager/SPK`` in the
``SPICE_PATH`` directory, ``voyager_1.ST+1991_a54418u.merged.bsp`` and
``voyager_2.ST+1992_m05208u.merged.bsp``, and need not be in the database. Where the
encounter and planetary ephemerides cover a time, they take precedence over the merged
trajectories.

Registering the host
====================

The host classes import lazily. ``import host_voyager`` gives access to
:class:`~host_voyager.oops.iss.VoyagerISS` without importing OOPS until the class is
first used. Importing ``host_voyager.oops`` registers the host with
:class:`~oops.host.Host`, so that :meth:`oops.host.Host.from_file` recognizes a Voyager
ISS image:

.. code-block:: python

   import oops
   import host_voyager.oops

   obs = oops.Host.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.IMG')

Initialization
==============

The first call to :meth:`~host_voyager.oops.iss.VoyagerISS.from_file` or
:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` loads the kernels and defines the
cameras' fields of view. To choose the bodies that OOPS defines, or to use only the
kernels that existed before a given date, call
:meth:`~host_voyager.oops.iss.VoyagerISS.initialize` before opening the first image.
Its ``planets`` argument is one planet number, from 1 for Mercury to 9 for Pluto, or a
sequence of them. This call defines only Saturn, planet 6, and its moons:

.. code-block:: python

   from host_voyager import VoyagerISS

   VoyagerISS.initialize(planets=(6,))

Later calls to :meth:`~host_voyager.oops.iss.VoyagerISS.initialize` have no effect.

Image files and indexes
=======================

Each image file, PDS3 label, or index is passed by its path or URL, so the archive
itself is found without an environment variable. A remote file is retrieved and cached
when it is read.
