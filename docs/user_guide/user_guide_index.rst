====================
Reading a PDS3 Index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` makes one
:class:`~oops.observation.Snapshot` for each row of a PDS3 index. It reads only the
index tables and opens none of the images, which makes it much faster than opening a
volume's images one at a time:

.. code-block:: python

   from host_voyager import VoyagerISS

   snapshots = VoyagerISS.from_index('metadata/VGISS_6xxx/VGISS_6109/'
                                     'VGISS_6109_raw_image_index.lbl')

The index and its supplement
============================

The host reads the raw image index of a VGISS volume, such as
``VGISS_6109_raw_image_index.lbl``, which has one row per raw image, and the
supplemental index, ``VGISS_6109_supplemental_index.lbl``, whose rows give further
values for the same images. The host needs both. The volume index,
``VGISS_6109_index.lbl``, has a row for every product of every image, and the host does
not accept it. Each row of the supplement is merged into the matching row of the index;
where both tables give a column, the index's value is kept.

The ``supplement`` argument chooses the supplemental index:

- By default, the host reads the file beside the index whose name has
  ``_supplemental_index`` in place of ``_raw_image_index``, in upper or lower case. That
  file must exist.
- A path or URL reads that file instead.
- None raises :exc:`~oops.host.HostIndexError`, unless ``row_dicts`` is given.

The two tables must list the same images in the same order. The host checks each pair of
rows by its ``VOLUME_NAME`` and ``FILE_SPECIFICATION_NAME`` and raises
:exc:`~oops.host.HostIndexError` at the first pair that differs.

The labels of the raw image indexes give the ``EXPOSURE_DURATION`` column a width of 7
bytes, while its format and the table itself give it 9. Read as the labels say, an
exposure of 0.0075 s would become 0.0 s. The host reads the column at its full width.

Other arguments
===============

``label``
    The index's PDS3 label, if it has already been parsed.

``row_dicts``
    The rows themselves, one dictionary per row, if the tables have already been read.
    Rows that hold the supplement's ``STOP_TIME`` column are used as they are, and so is
    any row when ``supplement`` is None. Rows without that column, as
    :meth:`oops.host.Host.from_index` passes them, are read again from the index and its
    supplement, matched by ``VOLUME_NAME`` and ``FILE_SPECIFICATION_NAME``, so that they
    too have the supplement's columns and the whole exposure.

``filter``
    A dictionary of column names and the values to keep. A row is kept when each column
    equals its value, is one of a set of values, or falls in a range given as a tuple of
    two values.

``select``
    Must be None. Each row is one image, so there is nothing to select, and any other
    value raises :exc:`~oops.OopsValueError`.

``return_all_planets``, ``pds3_options``, and the standard overrides
    The same as for :meth:`~host_voyager.oops.iss.VoyagerISS.from_file`, applied to every
    row.

Any other keyword raises :exc:`~oops.OopsTypeError`, so that a misspelled argument is
not silently ignored.

What one row gives
==================

Each row gives a :class:`~oops.observation.Snapshot` of the raw image, with its 800 by
800 field of view and no ``data``. Its ``dict`` subfield holds the merged row, with each
value as a Python string, integer, float, or Boolean, and with the same treatment of
missing values as a label (see :doc:`user_guide_metadata`). The tables' null constants,
an exposure of -99.9999 and a time of ``UNK``, are left out, since no real value shares
them.

The subfields are those of an image read from its label (see
:doc:`user_guide_metadata`), except ``data``, ``label_dict``, ``header_dict``, and
``if_scale``, so an index row has no calibration. The supplement gives every row its
clock partition, which a label does not give.

Every row gives a Snapshot, unless a row lacks an item that the host cannot do without
(see :doc:`user_guide_errors`). One row does: the dark frame ``C3940530`` of VGISS_6201
gives no exposure, so reading that volume's index raises
:exc:`~oops.host.HostFormatError`. The host does not check the C kernels for the
pointing of an index row. If the kernels hold no pointing for an image, its row still
gives a Snapshot, and :exc:`oops.OopsSpiceError` is raised only when the Snapshot's
frame is evaluated.

File paths
==========

A row's ``FILE_SPECIFICATION_NAME`` is the path of the PDS3 label, but each Snapshot
refers to the raw image file, ``PRODUCT_ID``, in the same directory of the volume.
``filepath``, ``basename``, and ``image_url`` give it as a full path or URL, formed from
the location of the index:

- For an index in the ``metadata`` tree of the Ring-Moon Systems Node holdings, such as
  ``.../metadata/VGISS_6xxx/VGISS_6109/VGISS_6109_raw_image_index.lbl``, the volume is
  in the matching ``volumes`` tree, ``.../volumes/VGISS_6xxx/VGISS_6109``.
- For an index anywhere else, the volume is the directory beside the index that has the
  row's ``VOLUME_NAME``.

``abspath`` is present only when the image file is local or already in the local cache.

Reading an index through oops.Host
==================================

:meth:`oops.host.Host.from_index` reads the rows of a raw image index itself, without
the supplement, and passes them to the host, which reads them again with the supplement
as described for ``row_dicts``. The Snapshots are those that
:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` gives, except that a ``filter`` is
applied to the rows as first read, whose exposures may lack their last two digits.
