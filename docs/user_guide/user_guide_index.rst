====================
Reading a PDS3 Index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` makes one
:class:`~oops.observation.Snapshot` for each row of a PDS3 index. It reads only the
index tables and leaves the images unopened, which makes it much faster than opening a
volume's images one at a time.

It returns an iterator, which gives the Snapshots row by row, in the order of the index.
The call itself checks the arguments and reads the PDS3 labels of the index and its
supplement, so a wrong argument or a label that cannot be read raises an error at once.
The iteration then reads the rows of the two tables a few at a time and builds each
row's Snapshot when it reaches that row. Only those few rows of the tables are held in
memory, however long the index. The whole process stays the same size from the first
row to the last only with the change to OOPS that frees each Snapshot's camera frame
with the Snapshot (SETI/rms-oops pull request 252); without it, each Snapshot leaves a
few kilobytes behind. An error in a row is raised when the iteration reaches it, after the
rows before it. To build every row at once, pass the iterator to ``list()``:

.. code-block:: python

   from host_voyager import VoyagerISS

   index = 'metadata/VGISS_6xxx/VGISS_6109/VGISS_6109_raw_image_index.lbl'
   for snapshot in VoyagerISS.from_index(index):
       print(snapshot.basename, snapshot.time)

   snapshots = list(VoyagerISS.from_index(index))

An error in a row ends the iteration there. To reach the rows that follow, leave that
row out with the ``filter`` argument, described below.

The index and its supplement
============================

``from_index()`` reads a metadata index of the Ring-Moon Systems Node holdings and its
supplemental index. For each VGISS volume, these are the raw image index, such as
``VGISS_6109_raw_image_index.lbl``, which has one row per raw image, and the
supplemental index, ``VGISS_6109_supplemental_index.lbl``, whose rows give further
values for the same images. The host needs both. Each row of the supplement is merged
into the matching row of the index; where both tables give a column, the index's value
is kept.

The metadata index of every product, ``VGISS_6109_index.lbl``, which has a row for each
product of each image, is not accepted. With the default supplement, it raises
:exc:`~oops.host.HostIndexError` when the call is made, also when ``row_dicts`` is
given. With a supplement path and without ``row_dicts``, the iteration raises
:exc:`~oops.host.HostIndexError` at the first row, whose file differs from the
supplement's. Rows given in ``row_dicts`` are not compared with the supplement's.

The ``supplement`` argument chooses the supplemental index:

- By default, the host reads the file beside the index whose name has
  ``_supplemental_index`` in place of ``_raw_image_index``, in upper or lower case. That
  file must exist.
- A path or URL reads that file instead.
- None or an empty string raises :exc:`~oops.host.HostIndexError`, unless ``row_dicts``
  is given.

Every VGISS supplement lists the same images as its index, in the same order, so the
host reads the two tables in step and merges each index row with the supplement row in
the same position. As it does, it checks that the two rows give the same file. If they
do not, the iteration raises :exc:`~oops.host.HostIndexError` at that row, giving the row
number, counted from 0, the file that each table gives, and both tables. The same error
is raised if the supplement ends before the index, and, after the last row, if the
supplement has more rows than the index.

The labels of the raw image indexes give the ``EXPOSURE_DURATION`` column a width of 7
bytes, while its format and the table itself give it 9. Read as the labels say, an
exposure of 0.0075 s would become 0.0 s. Until the labels are corrected, the host widens
any column whose format is wider than its width in the label, so it reads this column at
its full width. A corrected label is read as it is.

Other arguments
===============

``label``
    The index's PDS3 label, if it has already been parsed.

``row_dicts``
    The rows themselves, one dictionary per row, already merged with the supplement, if
    the tables have already been read. Any iterable works, a generator included. The
    rows are used as they are, and the tables are not read. Each row is filtered and
    built when the iteration reaches it. A row read from the index alone has no
    ``STOP_TIME`` and no clock partition, so its time is the index's ``IMAGE_TIME``,
    which is to the second.

``filter``
    A dictionary of column names and the values to keep. A row is kept when each column
    equals its value, is one of a set of values, or falls in a range given as a tuple of
    two values. Each row is tested when the iteration reaches it. A column that neither
    table has raises an error that gives the column, when the call is made. When
    ``row_dicts`` is given, the columns are those of the first row, so the error comes
    when the call is made if the rows are a list or another sequence, and otherwise when
    the iteration reads the first row.

``select``
    Must be None. Each row is one image, so there is nothing to select, and any other
    value raises :exc:`~oops.OopsValueError`.

``pds3_options``
    Options for the PDS3 label parser. The options are checked when the call is made,
    unless ``row_dicts`` is given, when they are not used. The index's label is parsed
    with all of them, and the supplement's label with the method alone. Each label is
    parsed with one method, the fast method unless the options give another, and no
    later method is tried.

``return_all_planets`` and the standard overrides
    The same as for :meth:`~host_voyager.oops.iss.VoyagerISS.from_file`, applied to every
    row.

Any other keyword raises Python's :exc:`TypeError` for an unexpected keyword argument,
so that a misspelled argument is not silently ignored.

What one row gives
==================

Each row gives a :class:`~oops.observation.Snapshot` of the raw image, with its 800 by
800 field of view and no ``data``. Its ``dict`` subfield holds the merged row, with each
value as a Python string, integer, float, or Boolean, and with the same treatment of
missing values as a label (see :doc:`user_guide_metadata`). The tables also give an
exposure of -99.9999 when it is unknown, and that value is left out too. Where a label
writes ``UNK``, the raw image indexes sometimes write ``UNKNOWN``: for the earth
received time of 1,524 images and for the picture number of 250. Both mean that no value
is known.

The subfields are those of an image read from its label (see
:doc:`user_guide_metadata`), except ``data``, ``label_dict``, ``header_dict``, and
``if_scale``, so the ``calibrations`` of an index row is an empty list. The supplement
gives every row its clock partition, which a label does not give.

Every row gives a Snapshot, unless a row lacks an item that the host cannot do without
(see :doc:`user_guide_errors`). One row does: the dark frame ``C3940530`` of VGISS_6201
lacks an exposure, so the iteration over that volume's index raises
:exc:`~oops.host.HostFormatError` at that row and ends there. To read the
rest of that volume, use ``filter`` to keep only the rows you want, such as a range of
``IMAGE_NUMBER`` values that starts after the dark frame.

The host does not check the C kernels for the pointing of an index row. If the kernels
lack the pointing of an image, its row still gives a Snapshot, and
:exc:`oops.OopsSpiceError` is raised only when the Snapshot's frame is evaluated.

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

A Snapshot read from an index never has ``abspath``, because reading an index does not
retrieve the image files, even those on the local file system. Reading an index never
creates a directory, even when the volume is not where the index places it.

Reading an index through oops.Host
==================================

:meth:`oops.host.Host.from_index` recognizes a VGISS index from the ``DATA_SET_ID`` in its
PDS3 label, such as ``VG1/VG2-S-ISS-2/3/4/6-PROCESSED-V1.0``, and passes the index, with
all of its other arguments, to :meth:`~host_voyager.oops.iss.VoyagerISS.from_index`.
Recognizing the index reads only its label. The host then reads the rows itself, a few
at a time, with the supplement and the full width of the exposure column, so the two
give the same Snapshots, also when ``supplement`` gives a path.
