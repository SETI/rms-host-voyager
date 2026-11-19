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
image file, also when the input is the label. After an astrometry read of a label, they
give the file that the label points to, whether or not it exists. After an astrometry
read of a remote label, the image file has not been retrieved, so ``abspath`` is absent
unless an earlier read put the file in the local cache.

Parsed input
============

A label or image that has already been parsed can be passed in its place: a
:class:`~pdsparser.Pds3Label` is read as a PDS3 label, and a :class:`~vicar.VicarLabel`
or :class:`~vicar.VicarImage` as the image file. Parsed content that was not read from a
file has no path, so the four path subfields are absent. In a read that needs the image
array, a label or header without a path cannot lead the host to the image file, and it
raises :exc:`~oops.host.HostFormatError`.

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

The standard OOPS overrides, ``target``, ``lightsource``, ``path``, ``frame``,
``cadence``, ``fov``, ``calibrations``, ``timeshift``, ``navigation``, ``tracker``, and
``parallel``, replace what the host would otherwise build. They are listed with
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file`. Any other keyword is accepted and
ignored.

Reading a label through oops.Host
=================================

:meth:`oops.host.Host.from_file` parses a PDS3 label itself, with the parser's fast
method alone, before it passes the label to the host. A few Voyager labels need the
strict or loose method, such as ``C2673426_GEOMED.LBL`` in VGISS_7205, whose quoted
``NOTE`` value starts on the line after its keyword. Call
:meth:`~host_voyager.oops.iss.VoyagerISS.from_file` for these labels.
