=============================
Time, Pointing, and Targets
=============================

Time
====

``STOP_TIME`` is the close of the shutter, in UTC at the spacecraft. A PDS3 label and the
supplemental index give it to 1/100 s. The exposure ends at ``STOP_TIME`` and begins
``EXPOSURE_DURATION`` earlier, and the Snapshot's default cadence covers exactly that
interval. Without ``STOP_TIME``, the host uses ``IMAGE_TIME``, the same time to the
second, which the raw image index gives. The VICAR header gives both as its spacecraft
event time, also to the second.

When no file read gives an exposure, the exposure is zero. A zero exposure, from any
source, gives a Snapshot whose start and stop times are the same.

A few images have no time in any file read: 17 raw images have none in their labels or
index rows, 9 in VGISS_6101 and 8 in VGISS_6201, and the VICAR headers of 93 images lack
a valid time. For these, the time is that of the image's spacecraft clock stop count:

- The count is ``SPACECRAFT_CLOCK_STOP_COUNT``, else the FDS count ``IMAGE_NUMBER``, else
  the FDS count with which the name of the image file begins.
- The FDS count recurs about every six years, so the count must be placed in one
  *partition* of the clock. The host uses the one partition in which the count falls
  within the months when the spacecraft's cameras took the archived images.
- The SPICE clock kernel converts the count in that partition to a time at the
  spacecraft.

For most images, the count's time comes about a second after ``STOP_TIME``. Of the
76,635 raw images whose labels give both, 79% differ by 3 s or less. Most of the rest
are later by whole FDS minutes of 48 s, and 12 are earlier, by up to about two hours for
``C4201035``. A time taken from the count can therefore be off by minutes or more.
:doc:`user_guide_appendix_pointing` gives the evidence.

Pointing
========

The scan platform C kernels hold one pointing record per image, stamped at the image's
own clock stop count. The count can fall minutes after the shutter closed, so a record
found by the image's time can belong to a neighboring image. The host therefore finds the
record by the clock stop count, with a tolerance of 20 ticks (1.2 s), in the partition
described above. The record is never found by the time.
:doc:`user_guide_appendix_pointing` gives the evidence.

The default frame of an image is a fixed :class:`~oops.frame.Cmatrix` relative to J2000,
built from that record and the rotation from the scan platform to the camera, and the
``spice_frame_is_frozen`` subfield is True. Because the frame is fixed, the ``navigation``
and ``tracker`` overrides work as usual, but a time shift of the frame, the letter ``F``
in the ``timeshift`` override, has no effect on it.

When the C kernels hold no record at the count, the frame is the camera's own SPICE
frame, a :class:`~oops.frame.SpiceFrame` such as ``VG1_ISSNA``, and
``spice_frame_is_frozen`` is False. The observation is built as usual, and using its
frame raises the error of the SPICE toolkit, whose message names the SPICE error code,
such as ``SPICE(NOFRAMECONNECT)``. Reading an image or an index never checks the
pointing.

A caller that has better pointing can pass it with the ``frame`` override, for example
as a :class:`~oops.frame.Cmatrix`, which is used as it is given.

Targets and light sources
=========================

The target body comes from ``TARGET_NAME``. A few names that the labels use are not
bodies that OOPS knows, and the host repairs them:

.. list-table::
   :header-rows: 1

   * - ``TARGET_NAME``
     - Target
   * - ``J RINGS``, ``S RINGS``, ``U RINGS``, ``N RINGS``
     - ``JUPITER_RING_PLANE``, ``SATURN_RING_PLANE``, ``URANUS_RING_PLANE``,
       ``NEPTUNE_RING_PLANE``
   * - ``CAL LAMPS``, ``DARK``, ``ORION``, ``PLAQUE``, ``SCORPIUS``, ``SKY``, ``STAR``,
       ``TAURUS``
     - ``NONE``
   * - ``SYSTEM``, ``UNK SAT``
     - The planet of the encounter

By default the light source is the Sun, except in an image of a star. When the target is
``ARCTURUS``, ``BETA CMA``, ``PLEIADES``, ``SIGMA SGR``, ``THETA CAR``, or ``VEGA``, that
star becomes the light source and the target becomes ``NONE``. An image whose files give
no ``TARGET_NAME`` has no default target.

The planet of the encounter is the first word of ``MISSION_PHASE_NAME``, such as
``SATURN`` for ``SATURN ENCOUNTER``. When no file read gives it, as for an image file
read alone, it comes from the image's time: Jupiter until 1980, Saturn from 1980, Uranus
from 1983, and Neptune from 1987. It is given as the ``planet`` subfield.

The raw ``TARGET_NAME`` stays in ``dict``.
