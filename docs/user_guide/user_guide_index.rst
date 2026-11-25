====================
Reading a PDS3 Index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` makes one
:class:`~oops.observation.Snapshot` for each row of a PDS3 index. It reads only the
index tables and leaves the images unopened, which makes it much faster than opening a
volume's images one at a time.

It returns an iterator, which gives the Snapshots row by row, in the order of the index,
one for every row. The call itself reads the PDS3 labels of the index and its
supplement, so a label that cannot be read raises an error at once. The iteration then
reads the rows of the two tables 100 at a time and builds each row's Snapshot when it
reaches that row, so only a few rows are held in memory, however long the index. A
caller that needs ``len()`` or indexing passes the iterator to ``list()``:

.. code-block:: python

   from host_voyager import VoyagerISS

   index = 'metadata/VGISS_6xxx/VGISS_6109/VGISS_6109_raw_image_index.lbl'
   for snapshot in VoyagerISS.from_index(index):
       print(snapshot.basename, snapshot.time)

   snapshots = list(VoyagerISS.from_index(index))

An error in a row is raised when the iteration reaches that row, after every row before
it, and the iteration ends there.

The index and its supplement
============================

``from_index()`` reads a raw image index of the Ring-Moon Systems Node metadata, such as
``VGISS_6109_raw_image_index.lbl``, which has one row per raw image, and its
supplemental index, ``VGISS_6109_supplemental_index.lbl``, whose rows give further
values for the same images, such as ``STOP_TIME`` to 1/100 s and the clock counts. Each
row of the supplement is merged into the matching row of the index; where both tables
give a column, the index's value is kept. The tables are read as their labels describe
them.

The ``supplement`` argument chooses the supplemental index:

- By default, the host reads the file beside the index whose name has
  ``_supplemental_index`` in place of ``_raw_image_index``, in upper or lower case. If
  that file does not exist, or the index's name lacks ``_raw_image_index``, the index is
  read alone.
- A path or URL, in any directory, reads that file instead. A file named this way must
  exist.
- None reads the index alone.

Without the supplement, a row's time is the index's ``IMAGE_TIME``, to the second.

Every VGISS supplement lists the same images as its index, in the same order, so the
host reads the two tables in step and merges each index row with the supplement row in
the same position. As it does, it checks that the two rows give the same
``VOLUME_NAME`` and ``FILE_SPECIFICATION_NAME``, and the same ``PRODUCT_ID``. If they do
not, the iteration raises :exc:`~oops.host.HostIndexError` at that row.

Other arguments
===============

``label``
    The index's PDS3 label, if it has already been parsed.

``row_dicts``
    The rows themselves, one dictionary per row, already merged with the supplement, if
    the tables have already been read. Any iterable works, a generator included. The
    rows are used as they are, and the tables are not read.

``filter``
    A dictionary of column names and the values to keep. A row is kept when each column
    equals its value, is one of a set of values, or falls in a range given as a tuple of
    two values. Each row is tested when the iteration reaches it.

``select``
    Applied to each row's Snapshot as :doc:`user_guide_files` describes for an image
    file; the default, None, gives the Snapshot.

``pds3_options``
    Options for the PDS3 label parser. The method is strict unless the options name
    another. The index's label is parsed with all of them, and the supplement's label
    with the method alone.

``return_all_planets`` and the standard overrides
    The same as for :meth:`~host_voyager.oops.iss.VoyagerISS.from_file`, applied to every
    row.

Any other keyword raises Python's :exc:`TypeError` for an unexpected keyword argument,
so that a misspelled argument is not silently ignored.

What one row gives
==================

Each row gives a :class:`~oops.observation.Snapshot` of the raw image, with its 800 by
800 field of view and no ``data``. Its ``dict`` subfield is the merged row exactly as the
table reader gives it. Every column is there, with its declared missing values, such as
the exposure -99.9999, and beside each column its ``_mask`` entry, which is True where the
value is a declared missing value or could not be read as its column's type.

To build the observation, the host treats a value as missing when its ``_mask`` entry is
True or it is a text such as ``UNK``, as :doc:`user_guide_metadata` lists them. A row
without an exposure gets a zero exposure, and a row without a time gets one from its
clock count, as :doc:`user_guide_geometry` describes. The camera comes from
``INSTRUMENT_NAME``, ``NARROW ANGLE CAMERA`` or ``WIDE ANGLE CAMERA``.

The labels of the raw image indexes give the ``EXPOSURE_DURATION`` column a width of 7
bytes, while its format and the table itself give it 9. Read as the labels describe it,
the column loses its last two digits, so an exposure of 0.0075 s reads as 0.0 s and
gives a zero exposure. This holds for 1,597 of the 76,652 rows until the labels are
corrected.

The subfields are those of an image read from its label (see
:doc:`user_guide_metadata`), except ``data``, ``label_dict``, ``header_dict``, and
``if_scale``, so the ``calibrations`` of an index row is an empty list. ``filepath`` is
the relative path ``VOLUME_NAME/FILE_SPECIFICATION_NAME`` as the index gives it, such as
``VGISS_6109/DATA/C34502XX/C3450201_RAW.LBL``, and ``basename`` its last component. A
caller that wants the file joins the path with its own copy of the archive. Reading an
index never opens or retrieves an image file.

A row whose image has no pointing record in the C kernels still gives a Snapshot, whose
frame raises the SPICE error when it is used (see :doc:`user_guide_geometry`).

Reading an index through oops.Host
==================================

:meth:`oops.host.Host.from_index` recognizes a VGISS raw image index from its first row,
which names a Voyager spacecraft and camera, and passes the index, with all of its other
arguments, to :meth:`~host_voyager.oops.iss.VoyagerISS.from_index`, so the two give the
same Snapshots.
