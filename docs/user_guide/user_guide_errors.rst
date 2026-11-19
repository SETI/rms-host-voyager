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
    clock count; or, unless ``frame`` is given, the clock stop count. Also raised when
    the image array is needed and parsed content passed in has no file path from which to
    find the image file. The message gives the missing items and the file.

:exc:`~oops.host.PdsHostError`
    None of the fast, strict, and loose methods of the parser can read the PDS3 label,
    or the label has no ``^IMAGE`` pointer, as the labels of the ``_RESLOC`` and
    ``_GEOMA`` tables have none.

:exc:`~oops.host.VicarHostError`
    The VICAR file cannot be read as an image: its header cannot be read, its data are
    cut short, or it holds a table, as the ``_RESLOC`` and ``_GEOMA`` files do.

:exc:`~oops.host.WrongHostError`
    The file, or the parsed label or image passed in, is not of a Voyager ISS image.

:exc:`~oops.OopsValueError`
    ``pds3_options`` holds an option that the parser does not accept.

:exc:`OSError`
    A file cannot be read, including the image file that a PDS3 label points to, in a
    read that needs it.

Reading a PDS3 index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` raises:

:exc:`~oops.host.HostIndexError`
    ``supplement`` is None and ``row_dicts`` is not given; the supplement does not have
    the same rows as the index, in the same order; the index's file name holds no
    ``_raw_image_index`` from which to find the supplement; or, for ``row_dicts`` without
    the supplement's columns, the index has no row for one of them.

:exc:`FileNotFoundError`
    The supplemental index does not exist.

:exc:`~oops.host.HostFormatError`
    A row gives no exposure, camera, or spacecraft; neither a time nor a clock count; or,
    unless ``frame`` is given, no clock stop count. The message gives the missing item
    and the image file.

:exc:`oops.OopsSpiceError`
    A row gives no time, and the clock kernel cannot convert its clock count.

:exc:`~oops.OopsTypeError`
    A keyword argument is not one that the method accepts.

:exc:`~oops.OopsValueError`
    ``select`` is not None.

:exc:`~oops.host.PdsHostError`
    The index's PDS3 label cannot be parsed.

:exc:`~oops.host.WrongHostError`
    The file is not a Voyager ISS index.

:exc:`OSError`
    The index or a named supplement does not exist or cannot be read.

The method does not check the pointing. An image whose pointing the SPICE kernels cannot
supply raises :exc:`oops.OopsSpiceError` only when its Snapshot's frame is evaluated.
