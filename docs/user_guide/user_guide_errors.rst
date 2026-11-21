======
Errors
======

The host reports each kind of failure with one exception class. The classes come from
OOPS, so code that reads images from several missions can catch them the same way.

Reading an image file
=====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_file` raises:

:exc:`oops.OopsSpiceError`
    The SPICE kernels cannot supply the image's pointing (see
    :doc:`user_guide_geometry`). The message reads "no Voyager 1 ISS NAC pointing at
    <time> for <image file>: <code>", ending with the SPICE error code, such as
    ``SPICE(CKINSUFFDATA)``. It is never raised for the pointing when the caller supplies
    it with the ``frame`` override. The same class is raised when no label read gives a
    time and the clock kernel cannot convert the clock count, or cannot place it in one
    partition; that message reads "no Voyager 1 clock time for FDS count 34502:01:001 for
    <image file>: <code>".

:exc:`~oops.host.HostFormatError`
    No label read gives the spacecraft, the camera, or the exposure; neither a time nor a
    clock count; or, unless ``frame`` is given, the clock stop count. The message gives
    the missing items and the file. Also raised by a full read of a parsed PDS3 label or
    VICAR label that lacks a file path, because the image file cannot then be found. A
    parsed VICAR image carries its own array, so it is read without its file.

:exc:`~oops.host.PdsHostError`
    None of the fast, strict, and loose methods of the parser can read the PDS3 label,
    or the label has no ``^IMAGE`` pointer, as the labels of the ``_RESLOC`` and
    ``_GEOMA`` tables have none.

:exc:`~oops.host.VicarHostError`
    The VICAR file cannot be read as an image: its header cannot be read, its data are
    cut short, or it holds a table, as the ``_RESLOC`` and ``_GEOMA`` files do.

:exc:`~oops.host.WrongHostError`
    The file, or the parsed label or image passed in, is not of a Voyager ISS image.

:exc:`TypeError`
    A keyword argument is not one that the method accepts. This is Python's own error
    for an unexpected keyword argument.

:exc:`~oops.OopsValueError`
    ``select`` is not None, or ``pds3_options`` holds an option that the parser does not
    accept.

:exc:`OSError`
    A file cannot be read, including the image file that a PDS3 label points to, in a
    read that needs it.

Reading a PDS3 index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` raises the errors listed below.
When the rows are given in ``row_dicts``, the call reads neither the tables nor their
labels, so the errors that come from reading a label or a table are never raised: the
labels need not exist or be readable, the index is not checked to be a Voyager ISS
index, and ``pds3_options`` is not checked. The checks of ``select``, ``supplement``,
and ``filter`` are still made, as their entries say, and so are those of each row.

:exc:`~oops.host.HostIndexError`
    When the call is made, any of these:

    - ``supplement`` is left at its default, and the index's file name lacks the
      ``_raw_image_index`` from which the host finds the supplement. This is checked
      when ``row_dicts`` is given too.
    - ``supplement`` is None or an empty string, and ``row_dicts`` is not given.
    - A table cannot be read as an index, because its label lacks the table it points
      to.

    During the iteration:

    - At a row where the index row and the supplement row in the same position give
      different files.
    - At the row where the supplement ends before the index.
    - After the last row of a table that holds fewer rows than its label says. Every
      row that the table holds is given first.
    - At a row that is longer than its label says. The rows are read 100 at a time, and
      none of the 100 read with that row is given.

    After the last row: the supplement has more rows than the index, or a table holds
    more rows than its label says.

    The tables are checked only for these errors. A value that cannot be read as the
    number its column describes gives a warning, and the row keeps the value as text.
    A row that is shorter than its label says is read as it is.

:exc:`ValueError`
    A row gives as text a number that the Snapshot needs, such as an exposure that holds
    a letter. This error is raised when the iteration reaches that row.

:exc:`FileNotFoundError`
    The label of the supplemental index does not exist, when the call is made; or a
    table file does not exist, when the iteration starts.

:exc:`~oops.host.HostFormatError`
    A row lacks the exposure, the camera, or the spacecraft; both the time and the clock
    count; or, unless ``frame`` is given, the clock stop count. The message gives the
    missing item and the image file. This error is raised when the iteration reaches that
    row.

:exc:`oops.OopsSpiceError`
    A row lacks a time, and the clock kernel cannot convert its clock count. This error
    is raised when the iteration reaches that row.

:exc:`TypeError`
    A keyword argument is not one that the method accepts, as Python reports an
    unexpected keyword argument.

:exc:`~oops.OopsTypeError`
    When the iteration reaches a row, a range in ``filter`` cannot be compared with that
    row's value.

:exc:`~oops.OopsValueError`
    ``select`` is not None, ``filter`` gives a column that neither table has, or
    ``pds3_options`` holds an option that the parser does not accept. When ``row_dicts``
    is given, ``filter`` is checked against the first row: when the call is made if the
    rows are a list or another sequence, and otherwise when the iteration reads that row.

:exc:`~oops.host.PdsHostError`
    The PDS3 label of the index or of the supplement cannot be parsed.

:exc:`~oops.host.WrongHostError`
    The file is not a Voyager ISS index.

:exc:`OSError`
    The label of the index or of a named supplement cannot be read.

The method does not check the pointing. An image whose pointing the SPICE kernels cannot
supply raises :exc:`oops.OopsSpiceError` only when its Snapshot's frame is evaluated.

An error raised for one row ends the iteration there. :doc:`user_guide_index` describes
how to leave such a row out.
