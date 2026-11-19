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
The host reads each field of these lines and gives it under the PDS3 name, in the form
that the PDS3 label uses:

- ``LAB02`` gives the spacecraft as ``INSTRUMENT_HOST_NAME``, the FDS count as
  ``IMAGE_NUMBER`` and ``SPACECRAFT_CLOCK_STOP_COUNT``, the picture number as
  ``IMAGE_ID``, and the spacecraft event time as ``IMAGE_TIME`` and ``STOP_TIME``. The
  times are ISO 8601 text, such as ``1980-10-29T05:57:22.000``.
- ``LAB03`` gives the camera as ``INSTRUMENT_ID``, the exposure as
  ``EXPOSURE_DURATION`` (in seconds, from the header's milliseconds), and the filter as
  ``FILTER_NAME`` and ``FILTER_NUMBER``.
- ``LAB04`` gives the earth received time as ``EARTH_RECEIVED_TIME``.
- ``LAB05`` gives the target as ``TARGET_NAME``.
- The processing steps that the header records give ``PRODUCT_TYPE``, and the time gives
  ``MISSION_PHASE_NAME``.

The values from ``LAB02``, ``LAB03``, and ``LAB05`` appear in the metadata only under
their PDS3 names. ``LAB04`` holds more than the earth received time, so it also keeps its
own name. Every other header keyword, such as ``LABEL3``, keeps its own name.

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

Where only one label gives an item, its value is used. Where both give it and agree,
that value is used. Where both give it and disagree, the item always takes its value
from the same label, for every image. For every item that both labels give, that label is
the PDS3 label. The label's errors are small: about 800 labels give the exposure cut to
0.01 s. The header's errors are large: for some images its clock count is one or two FDS
minutes late, and it gives about 90 long exposures of 15 s or more as 5 or 240 ms. The
notes in some labels also record corrections of the camera, the filter, or the time that
the header does not have. If the PDS3 label gives no value for an item, the header's
value is used.

Missing values
==============

A value that no label read gives is left out: there is no ``dict`` key and no subfield
for it, and never a None or a placeholder in its place. These count as no value:

- the PDS3 null constants ``UNK``, ``N/A``, and ``NULL``, in any case, and a list of
  nothing else;
- the placeholder picture numbers ``0000Z1+000`` and ``0000Z2+000`` of ``IMAGE_ID``;
- a field of the VICAR header that the ground system filled with asterisks, such as
  ``SCET **.*** **:**:**``, or with a placeholder or unreadable bytes, such as the FDS
  count ``00000.00`` or an hour of 24 or more.

The dictionaries
================

Three subfields hold the metadata as dictionaries. All three can be written with
:func:`json.dump`: times are ISO 8601 text, sets are lists, and an item has the same
type whichever label it came from.

``dict``
    The merged metadata: every item of either label read, under the PDS3 name where the
    host converts it, and under its own name otherwise.

``label_dict``
    The PDS3 label as its parser reads it, present only when the label was read. Times
    are ISO 8601 text, and sets are lists in the label's order. The ``END`` keyword and
    the extra keys that the parser adds beside each keyword, such as ``STOP_TIME_fmt`` and
    ``^IMAGE_offset``, are left out.

``header_dict``
    Every parameter of the VICAR header, present only when the image file was read. A
    parameter that occurs more than once, such as the ``TASK`` of each step of the
    processing history, is keyed by its name and its occurrence counted from zero:
    ``TASK[0]``, ``TASK[1]``, and so on.

Subfields
=========

Besides ``dict``, ``label_dict``, ``header_dict``, and ``data``, every Snapshot has
these subfields. One whose item no label gives is absent.

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
       no label gives it.
   * - ``filter``
     - ``FILTER_NAME`` as a PDS3 label spells it, such as ``"CLEAR"`` or ``"CH4_JS"``.
   * - ``label_target``
     - ``TARGET_NAME`` as the label spells it.
   * - ``if_scale``
     - The factor that converts a calibrated product's data to I/F (see
       :doc:`user_guide_products`).
   * - ``spacecraft_clock_start_count``, ``spacecraft_clock_stop_count``
     - The clock counts, described below.
   * - ``time_from_sclk``
     - True if no label read gives a time, so that the time comes from the clock count
       (see :doc:`user_guide_geometry`).
   * - ``spice_kernels``
     - The SPICE kernels used for the observation.
   * - ``spice_frame_name``, ``spice_frame_id``, ``spice_to_frame``
     - The SPICE frame of the camera, such as ``VG1_ISSNA`` (-31101) or ``VG2_ISSWA``
       (-32102), and the rotation from it to the camera frame, which is the identity.
   * - ``filepath``, ``basename``, ``abspath``, ``image_url``
     - The image file (see :doc:`user_guide_files`).

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
the PDS3 label or the supplemental index. Neither label gives the clock partition; the
supplemental index does (see :doc:`user_guide_geometry`).
