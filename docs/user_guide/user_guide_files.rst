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

What is read
============

What the host reads depends on the file it is given and on the ``astrometry`` option.
An astrometry read is for work that needs only the image's time and geometry, so it
reads no image array.

.. list-table::
   :header-rows: 1

   * - Input
     - Normal read
     - With ``astrometry=True``
   * - VICAR image file
     - The image file alone: its header and its array.
     - The image file's header alone.
   * - PDS3 label
     - The label, and the whole image file beside it.
     - The label alone. The image file need not exist.

The metadata come from whatever was read, as :doc:`user_guide_metadata` describes. The
``data`` subfield holds the image array except in an astrometry read.

The host always finds the image array through the VICAR file's own header, never
through the record number in the label's ``^IMAGE`` pointer. Some labels give that
number wrongly, so this keeps the array identical from either input. The label of
``C2673426_GEOMED`` in VGISS_7205 points one record early, and that of
``C3489611_GEOMED`` in VGISS_6112 one record late.

Finding the image file
======================

Given a PDS3 label, the host looks in the label's directory for the image file that the
label's ``^IMAGE`` pointer gives. It tries the name as the label spells it, then in upper
and in lower case, so that a copy of the archive with lower-case file names still works.
An astrometry read does not look for the file, so its path subfields give the name as
the label spells it.

The ``filepath``, ``basename``, ``abspath``, and ``image_url`` subfields always give the
image file, also when the input is the label. After an astrometry read of a label,
``filepath``, ``basename``, and ``image_url`` give the file that the label points to,
whether or not it exists.

``abspath`` is the file's local path. It is present only when the image file is on the
local file system or is a remote file that is already in the local cache, and it never
gives a file that does not exist. An astrometry read of a remote label does not retrieve
the image file, so ``abspath`` is then present only if an earlier read put the file in
the cache. Reading an image never creates a directory.

Parsed input
============

A label or image that has already been parsed can be passed in its place: a
:class:`~pdsparser.Pds3Label` is read as a PDS3 label, and a :class:`~vicar.VicarLabel`
or :class:`~vicar.VicarImage` as the image file. Parsed content that was not read from a
file has no path, so the four path subfields are absent. A :class:`~vicar.VicarImage`
holds its array, so both reads of one work without a path. A
:class:`~pdsparser.Pds3Label` or :class:`~vicar.VicarLabel` holds only the metadata, so
a full read of one without a path cannot find the image file, and it raises
:exc:`~oops.host.HostFormatError`, saying that no file path is known. An astrometry read
of either works without a path.

The header of a :class:`~vicar.VicarImage` gives the reading machine in ``HOST`` (and
``BHOST``) once its array has been read. When the :class:`~vicar.VicarImage` was read
from a file, the host reads that file's header again, so ``header_dict`` gives the
machine that wrote the file, as every other input does.

Options
=======

These options are particular to the Voyager host:

``return_all_planets``
    True lists in the ``spice_kernels`` subfield the kernels of every planet, and not
    only those of the planet of the encounter.

``pds3_options``
    Options for the PDS3 label parser. The host starts with the parser's fast method and,
    if that cannot read a label, tries the strict method and then the loose one. A method
    given in ``pds3_options`` starts that sequence at the method given.

``astrometry``
    True for an astrometry read, as described above.

``select``
    Must be None. An image file holds one image, so there is nothing to select, and any
    other value raises :exc:`~oops.OopsValueError`.

The standard OOPS overrides, ``target``, ``lightsource``, ``path``, ``frame``,
``cadence``, ``fov``, ``calibrations``, ``timeshift``, ``navigation``, ``tracker``, and
``parallel``, replace what the host would otherwise build. They are listed with
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file`. Any other keyword raises
:exc:`~oops.OopsTypeError`, so that a misspelled argument is not silently ignored.

Reading a label through oops.Host
=================================

:meth:`oops.host.Host.from_file` recognizes a Voyager ISS image file or PDS3 label and
passes the file's path, with its other arguments, to
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file`, so the two give the same Snapshot.
To recognize a label, it tries the parser's fast, strict, and loose methods in turn, as
the host does. This matters for a few Voyager labels that only the strict or the loose
method can read, such as ``C2673426_GEOMED.LBL`` in VGISS_7205, whose quoted ``NOTE``
value starts on the line after its keyword. A calibrated product keeps its I/F
calibration unless the ``calibrations`` override replaces it.

