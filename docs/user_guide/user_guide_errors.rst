======
Errors
======

The host reports each kind of failure with one exception class. The classes come from
OOPS, so code that reads images from several missions can catch them the same way.

Reading an image file
=====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_file` raises:

:exc:`~oops.host.PdsHostError`
    The PDS3 label cannot be parsed, or it has no ``^IMAGE`` pointer, as the labels of
    the ``_RESLOC`` and ``_GEOMA`` tables have none. The message names the label.

:exc:`~oops.host.VicarHostError`
    The VICAR file cannot be read: its header cannot be parsed, its data are cut short,
    or its image has more than one band. The message names the file.

:exc:`~oops.host.HostFormatError`
    The image file's array does not have the number of lines and samples that its PDS3
    label describes; no file read gives a clock count and the name of the image file does
    not begin with one; or no file read gives a time and the clock count cannot be
    placed in one partition of the clock (see :doc:`user_guide_geometry`). The message
    names the file.

:exc:`~oops.host.WrongHostError`
    The file is not a Voyager ISS image or label.

:exc:`TypeError`
    A keyword argument is not one that the method accepts. This is Python's own error
    for an unexpected keyword argument. It is also raised for an index or a slice in
    ``select``.

:exc:`~oops.OopsValueError`
    ``pds3_options`` holds an option that the parser does not accept, or ``select`` is a
    name.

:exc:`OSError`
    A file cannot be found or read, including the image file that a PDS3 label points
    to, in a read that needs it.

The error of the SPICE toolkit
    The clock kernel cannot convert a clock count, when no file read gives a time.

Reading an image never checks the pointing. An image whose pointing the C kernels lack
gives a Snapshot whose frame raises the error of the SPICE toolkit when it is used.

Reading a PDS3 index
====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_index` raises these when the call is made:

:exc:`~oops.host.HostIndexError`
    A label does not describe a table that can be read.

:exc:`~oops.host.PdsHostError`
    The PDS3 label of the index or of the supplement cannot be parsed.

:exc:`~oops.host.WrongHostError`
    The index is not a Voyager ISS index.

:exc:`FileNotFoundError`
    A supplement named by the ``supplement`` argument does not exist.

:exc:`~oops.OopsValueError`
    ``pds3_options`` holds an option that the parser does not accept.

:exc:`TypeError`
    A keyword argument is not one that the method accepts.

:exc:`OSError`
    The label of the index cannot be read.

It raises these when the iteration reaches the row concerned:

:exc:`FileNotFoundError`
    A table file does not exist, when the iteration starts.

:exc:`~oops.host.HostIndexError`
    The index row and the supplement row in the same position give different files or
    different ``PRODUCT_ID`` values, or the supplement ends before the index. After the
    last row, the supplement has more rows than the index. A table file holds fewer
    rows than its label says, after every row it holds, or more, after the last row.

:exc:`~oops.host.HostFormatError`
    A row has no time, and its clock count cannot be placed in one partition of the
    clock. Ten rows of VGISS_6101 and VGISS_6201 raise it, so the iteration over those
    two volumes ends at the first of them (see :doc:`user_guide_geometry`).

:exc:`KeyError`
    A row lacks a column that ``filter`` names.

The error of the SPICE toolkit
    A row has no time, and the clock kernel cannot convert its clock count.

The index tables are checked only for these errors. Reading an index never checks the
pointing.
