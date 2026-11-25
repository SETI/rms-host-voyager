=====================
Reading an Image File
=====================

:meth:`~host_voyager.oops.iss.VoyagerISS.from_file` makes one
:class:`~oops.observation.Snapshot` from one ISS image. It accepts either file of the
product, the VICAR image file or its PDS3 label:

.. code-block:: python

   from host_voyager import VoyagerISS

   snapshot = VoyagerISS.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.IMG')
   snapshot = VoyagerISS.from_file('VGISS_6109/DATA/C34502XX/C3450201_GEOMED.LBL')

A file whose name ends in ``.LBL``, in any case, is read as a PDS3 label, and any other
file as a VICAR image file.

What is read
============

What the host reads depends on the file it is given and on the ``astrometry`` option.
An astrometry read is for work that needs only the image's time and geometry, so it
leaves the image array unread.

.. list-table::
   :header-rows: 1

   * - Input
     - Normal read
     - With ``astrometry=True``
   * - VICAR image file
     - The image file alone: its header and its array.
     - The image file's header alone.
   * - PDS3 label
     - The label, and the image file that its ``^IMAGE`` pointer names, in the label's
       directory.
     - The label alone. The image file need not exist.

The metadata come from whatever was read, as :doc:`user_guide_metadata` describes. The
``data`` subfield holds the image array except in an astrometry read.

The host always reads the image array through the VICAR file, where the file's own
header places it, and never at the record number in the label's ``^IMAGE`` pointer. Some
labels give that number wrongly, so this keeps the array identical from either input.
The labels of 208 of the 4,616 products of VGISS_7205 and 55 of the 3,776 of VGISS_7206
point one record early, such as ``C2673426_GEOMED``, and that of ``C3489611_GEOMED``
points one record late.

The labels are parsed with the strict method of the PDS3 label parser, which reads every
label of the VGISS volumes. ``pds3_options`` can name another method.

File subfields
==============

``filepath`` and ``basename`` always name the image file, also when the input is the
label, and ``image_url`` gives its absolute path or URL. After an astrometry read of a
label, they name the file that the label points to, whether or not it exists.

``image_abspath`` is the image file's path on the local disk, present when the image
file was read, its header alone counting. A remote file is retrieved into the local
cache when it is read, and ``image_abspath`` names that copy. An astrometry read of a
label never gives ``image_abspath``, because it leaves the image file unread.

``label_url`` and ``label_abspath`` give the label's absolute path or URL and its path
on the local disk, when the label was read. A read of the image file alone gives
neither.

Reading through oops.Host
=========================

:meth:`oops.host.Host.from_file` recognizes a Voyager ISS image file or PDS3 label and
passes it, with its other arguments, to
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file`. A label is passed as its path, and
an image file as the VICAR image or header that OOPS read to recognize it, so the host
reads neither file twice.

Options
=======

These options are particular to the Voyager host:

``return_all_planets``
    True lists in the ``spice_kernels`` subfield the kernels of every planet, and not
    only those of the planet of the encounter.

``pds3_options``
    Options for the PDS3 label parser. The method is strict unless the options name
    another.

``astrometry``
    True for an astrometry read, as described above.

``select``
    An image file holds one image, which the default, None, returns. Any other value is
    applied to a tuple holding that one Snapshot, as OOPS applies a selection to the
    observations of a file. The index 0 returns the Snapshot, a slice returns a tuple, an
    index out of range raises :exc:`~oops.OopsIndexError`, and a name raises
    :exc:`~oops.OopsValueError`.

The standard OOPS overrides, ``target``, ``lightsource``, ``path``, ``frame``,
``cadence``, ``fov``, ``calibrations``, ``timeshift``, ``navigation``, ``tracker``, and
``parallel``, replace what the host would otherwise build. They are listed with
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file`. A ``frame`` override, such as a
:class:`~oops.frame.Cmatrix`, is used as it is given.

To start from a pointing offset in pixels, *(du, dv)*, convert it to angles with the
field of view and pass it as ``navigation``:

.. code-block:: python

   from host_voyager import VoyagerISS

   path = 'VGISS_6109/DATA/C34502XX/C3450201_GEOMED.LBL'
   (du, dv) = (3.5, -2.0)      # the offset in pixels

   obs = VoyagerISS.from_file(path)
   obs = VoyagerISS.from_file(path, navigation=obs.fov.offset_angles_from_duv((du, dv)))

Any other keyword, ``offset`` included, raises Python's :exc:`TypeError` for an
unexpected keyword argument, so that a misspelled argument is not silently ignored.
