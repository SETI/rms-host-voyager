=======================
Metadata and Subfields
=======================

The two labels
==============

A Voyager ISS product describes itself twice. The *PDS3 label* is the detached ``.LBL``
file beside the image. The *VICAR header* is the label at the start of the image file
itself. The two were written at different times and do not always agree, and each holds
values that the other lacks.

The host takes its metadata from whichever of the two it read (see
:doc:`user_guide_files`): from the VICAR header alone when given the image file, from
the PDS3 label alone in an astrometry read of the label, and from both when given the
label otherwise.

Merging the two labels
======================

The VICAR header of a Voyager image gives most of its values as fixed-width text lines.
The host reads each field of these lines and gives it under the PDS3 name, with the
type and spelling that the PDS3 label gives it:

- ``LAB02`` gives the spacecraft as ``INSTRUMENT_HOST_NAME``, the FDS count as
  ``IMAGE_NUMBER`` and ``SPACECRAFT_CLOCK_STOP_COUNT``, the picture number as
  ``IMAGE_ID``, and the spacecraft event time as ``IMAGE_TIME`` and ``STOP_TIME``. The
  times are :class:`datetime.datetime` objects, as the label parser gives a label's
  times.
- ``LAB03`` gives the camera as ``INSTRUMENT_ID``, the exposure as
  ``EXPOSURE_DURATION`` (in seconds, from the header's milliseconds), and the filter as
  ``FILTER_NAME`` and ``FILTER_NUMBER``.
- ``LAB05`` gives the target as ``TARGET_NAME``.
- The processing steps that the header records give ``PRODUCT_TYPE``.

These three lines appear in the merged metadata only as the items they give. Every other
header keyword, such as ``LAB04`` or ``LABEL3``, keeps its own name.

The header's spellings are changed to the label's. ``LAB03`` pads the filter name and
writes ``CH4/JS`` and ``CH4/U`` where a label writes ``CH4_JS`` and ``CH4_U``. The target
field of ``LAB05`` holds at most eight characters and uses discovery designations for
some moons, so these spellings become the label's names:

.. list-table::
   :header-rows: 1

   * - VICAR header
     - ``TARGET_NAME``
   * - ``CALLAMPS``
     - ``CAL LAMPS``
   * - ``ENCELADU``
     - ``ENCELADUS``
   * - ``EPIMETHE``
     - ``EPIMETHEUS``
   * - ``PROMETHE``
     - ``PROMETHEUS``
   * - ``1985U1``
     - ``PUCK``
   * - ``1989N1``
     - ``PROTEUS``
   * - ``1989N2``
     - ``LARISSA``
   * - ``SIGMASGR``
     - ``SIGMA SGR``
   * - ``BETACMA``
     - ``BETA CMA``

Where both labels give an item, the PDS3 label's value is used, for every item and every
image. Where the two disagree, the PDS3 label is almost always the one that is right.
For some images the header's clock count is one or two FDS minutes late, its time is
garbled or is to the second where the label's is to 1/100 s, it gives about 90 long
exposures as 5 or 240 ms, and its eight-character target is less precise than the
label's. The notes in some labels also record corrections of the camera, the filter, or
the time that the header does not have. The label's own errors are small: about 800
labels give the exposure cut to 0.01 s, and 137 of them give 0.0 s for an exposure under
10 ms. A read of such a label gives a zero exposure, while a read of the image file alone
gives the header's.

Where the PDS3 label lacks an item, or gives a missing value for it, the header's value is
used.

Missing values
==============

A value that no label read gives is left out of the merged metadata: there is no key for
it, and never a None in its place. These count as missing:

- the texts that the archive writes for an unknown value, ``UNK``, ``UNKNOWN``,
  ``N/A``, ``NULL``, ``NOT APPLICABLE``, and ``---``, in any case, and empty text;
- a field of the VICAR header that the ground system filled with asterisks, such as
  ``SCET **.*** **:**:**``, or with a placeholder or unreadable bytes, such as the FDS
  count ``00000.00``, the picture number ``0000Z1+000``, or an hour of 24 or more.

The same holds inside the objects of a label. Some labels give
``REFLECTANCE_SCALING_FACTOR`` in their ``IMAGE`` object as ``UNK``, and the ``IMAGE``
entry of ``dict`` then has no such key.

The dictionaries
================

Three subfields hold the metadata as dictionaries:

``dict``
    The merged metadata: every item of either label read, under the PDS3 name where the
    host converts it, and under its own name otherwise.

``label_dict``
    The PDS3 label exactly as the label parser gives it, present only when the label was
    read. It keeps the entries that the parser adds beside each keyword, such as
    ``STOP_TIME_fmt``, and every missing value as the label writes it.

``header_dict``
    The VICAR header exactly as the VICAR parser gives it, present only when the image
    file was read. A parameter that occurs more than once, such as the ``TASK`` of each
    step of the processing history, is keyed by the pair of its name and its occurrence,
    such as ``('TASK', 1)``. Reading the image array sets ``HOST`` and ``BHOST`` to the
    machine that read the file.

Subfields
=========

Every Snapshot has these subfields. A subfield is absent when no file read gives its
value.

.. list-table::
   :header-rows: 1

   * - Subfield
     - Contents
   * - ``host``, ``instrument``
     - ``"Voyager"`` and ``"ISS"``.
   * - ``spacecraft``
     - The integer 1 for Voyager 1 or 2 for Voyager 2, from ``INSTRUMENT_HOST_NAME``.
       It is present on every Snapshot and is the way to tell the two spacecraft apart.
   * - ``detector``
     - The camera, ``"NAC"`` or ``"WAC"``.
   * - ``planet``
     - The planet of the encounter, from ``MISSION_PHASE_NAME``, or from the time when
       no file gives it.
   * - ``filter``
     - ``FILTER_NAME``, such as ``"CLEAR"`` or ``"CH4_JS"``.
   * - ``target``, ``lightsource``, ``calibrations``
     - The default target, light source, and calibrations of the observation (see
       :doc:`user_guide_geometry` and :doc:`user_guide_products`).
   * - ``if_scale``
     - The factor that converts the product's data to I/F (see
       :doc:`user_guide_products`).
   * - ``spacecraft_clock_start_count``, ``spacecraft_clock_stop_count``
     - The clock counts, described below.
   * - ``spice_kernels``
     - The SPICE kernels used for the observation.
   * - ``spice_frame_name``, ``spice_frame_id``, ``spice_to_frame``
     - The SPICE frame of the camera, such as ``VG1_ISSNA`` (-31101) or ``VG2_ISSWA``
       (-32102), and the rotation from it to the camera frame, which is the identity.
   * - ``ck_object_id``, ``ck_sclk_id``
     - The SPICE object whose C kernels hold the pointing, the scan platform of the
       spacecraft (-31100 or -32100), and the spacecraft clock that stamps its records
       (-31 or -32).
   * - ``spice_frame_is_frozen``
     - True when the frame is fixed from the image's own pointing record, False when it
       is the camera's SPICE frame (see :doc:`user_guide_geometry`).
   * - ``filepath``, ``basename``, ``image_url``, ``image_abspath``, ``label_url``,
       ``label_abspath``
     - The files read (see :doc:`user_guide_files`).

The spacecraft clock counts
===========================

The clock of the Voyager Flight Data Subsystem (FDS) counts FDS hours of 48 minutes,
FDS minutes of 48 seconds, and ticks of 60 ms. ``spacecraft_clock_start_count`` and
``spacecraft_clock_stop_count`` are the counts written as the PDS3 label writes them,
``hhhhh:mm:ddd`` without leading zeros, such as ``34502:01:001`` or ``9096:16:001``. The
stop count is the image's FDS count, which also begins the name of the image file:
``C3450201`` is FDS 34502.01.

The VICAR header gives only the stop count, as the FDS count ``34502.01`` in its
``LAB02`` line, which the host writes in the label's form. A start count comes only from
the PDS3 label or the supplemental index.
