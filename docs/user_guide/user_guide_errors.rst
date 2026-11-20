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
    the missing items and the file. Also raised when the image array is needed and no
    file path is known for the parsed content passed in, so that the image file cannot
    be found.

:exc:`~oops.host.PdsHostError`
    None of the fast, strict, and loose methods of the parser can read the PDS3 label,
    or the label has no ``^IMAGE`` pointer, as the labels of the ``_RESLOC`` and
    ``_GEOMA`` tables have none.

:exc:`~oops.host.VicarHostError`
    The VICAR file cannot be read as an image: its header cannot be read, its data are
    cut short, or it holds a table, as the ``_RESLOC`` and ``_GEOMA`` files do.

:exc:`~oops.host.WrongHostError`
    The file, or the parsed label or image passed in, is not of a Voyager ISS image.

:exc:`~oops.OopsTypeError`
    A keyword argument is not one that the method accepts.

:exc:`~oops.OopsValueError`
    ``select`` is not None, or ``pds3_options`` holds an option that the parser does not
    accept.

:exc:`OSError`
    A file cannot be read, including the image file that a PDS3 label points to, in a
    read that needs it.

Reading a PDS3 index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` raises:

:exc:`~oops.host.HostIndexError`
    Any of these:

    - The index is one that a volume keeps in its own ``INDEX`` directory, such as
      ``RAWIMAGES.LBL``, which the host does not read. The message points to the
      metadata index.
    - The index's file name holds no ``_raw_image_index`` from which to find the
      supplement.
    - ``supplement`` is None and ``row_dicts`` is not given.
    - A table cannot be read as an index, because its label lacks the table it points
      to.
    - An image appears in only one of the index and the supplement, or the two tables
      list the images in different orders and one of them lists an image twice.
    - ``row_dicts`` lack the supplement's columns, and the index has no row for one of
      them.

:exc:`FileNotFoundError`
    The supplemental index does not exist.

:exc:`~oops.host.HostFormatError`
    A row gives no exposure, camera, or spacecraft; neither a time nor a clock count; or,
    unless ``frame`` is given, no clock stop count. The message gives the missing item
    and the image file. This error is raised when the iteration reaches that row.

:exc:`oops.OopsSpiceError`
    A row gives no time, and the clock kernel cannot convert its clock count. This error
    is raised when the iteration reaches that row.

:exc:`~oops.OopsTypeError`
    A keyword argument is not one that the method accepts, or a range in ``filter``
    cannot be compared with the values of its column.

:exc:`~oops.OopsValueError`
    ``select`` is not None, ``filter`` gives a column that the index does not have, or
    ``pds3_options`` holds an option that the parser does not accept.

:exc:`~oops.host.PdsHostError`
    The PDS3 label of the index or of the supplement cannot be parsed.

:exc:`~oops.host.WrongHostError`
    The file is not a Voyager ISS index.

:exc:`OSError`
    The index or a named supplement does not exist or cannot be read.

The method does not check the pointing. An image whose pointing the SPICE kernels cannot
supply raises :exc:`oops.OopsSpiceError` only when its Snapshot's frame is evaluated.

An error raised for one row ends the iteration: the iterator gives no rows after it.
:doc:`user_guide_index` describes how to leave such a row out.
