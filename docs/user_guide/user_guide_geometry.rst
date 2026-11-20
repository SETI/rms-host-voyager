=============================
Time, Pointing, and Targets
=============================

Time
====

``STOP_TIME`` is the close of the shutter, in UTC at the spacecraft. A PDS3 label and the
supplemental index give it to 1/100 s. The exposure ends at ``STOP_TIME`` and begins
``EXPOSURE_DURATION`` earlier, and the Snapshot's default cadence covers exactly that
interval.
Without ``STOP_TIME``, the host uses ``IMAGE_TIME``, the same time to the second, which
the raw image index gives. The VICAR header gives both as its spacecraft event time,
also to the second.

A few images have no time in any label read. The labels of 17 raw images give none, 9 in
VGISS_6101 and 8 in VGISS_6201, and the VICAR headers of 93 images give no valid time.
For these the host takes the time of the image's spacecraft clock stop count and sets the
``time_from_sclk`` subfield to True:

- The count is ``SPACECRAFT_CLOCK_STOP_COUNT``, else the FDS count ``IMAGE_NUMBER``, else
  the FDS count with which the name of the image file begins.
- The FDS count recurs about every six years, so the count must be placed in one
  *partition* of the clock. The host uses the partition that the supplemental index
  gives, else the one in which the count falls nearest ``EARTH_RECEIVED_TIME``, else the
  one partition in which the count falls within the months when the spacecraft's cameras
  took the archived images.
- The SPICE clock kernel converts the count in that partition to a time at the
  spacecraft.

For most images, the count's time comes about a second after ``STOP_TIME``. Of the
76,635 raw images whose labels give both, 79% differ by 3 s or less. Most of the rest
are later by whole FDS minutes of 48 s, and 12 are earlier, by up to about two hours for
``C4201035``. A time taken from the count can therefore be off by minutes or more.
:doc:`user_guide_appendix_pointing` gives the evidence.

An image cannot be read if it has neither a time nor a clock count (see
:doc:`user_guide_errors`).

Pointing
========

The scan platform C kernels hold one pointing record per image, stamped at the image's
own clock stop count. The count can fall minutes after the shutter closed, so a record
found by the image's time can belong to a neighboring image. The host therefore finds the
record by the clock stop count, with a tolerance of 20 ticks (1.2 s), in the partition
that the supplemental index gives or else the one nearest the image's time. The record is
never found by the time. :doc:`user_guide_appendix_pointing` gives the evidence.

The default frame of an image is a fixed :class:`~oops.frame.Cmatrix` relative to J2000,
built from that record and the rotation from the scan platform to the camera. Because the
frame is fixed, the ``navigation`` and ``tracker`` overrides work as usual, but a time
shift of the frame, the letter ``F`` in the ``timeshift`` override, has no effect on it.

When the SPICE kernels cannot supply the pointing, because the C kernels hold no record
for the count, the clock kernel cannot encode the count, or the frame kernel does not
connect the scan platform to the camera, the frame raises :exc:`oops.OopsSpiceError`
whenever it is evaluated. :meth:`~host_voyager.oops.iss.VoyagerISS.from_file`
evaluates it before it returns, at the middle of the exposure after any ``cadence`` or
``timeshift`` override. :meth:`~host_voyager.oops.iss.VoyagerISS.from_index` does not,
so the error appears only when the Snapshot's frame is evaluated. Of the 76,652 VGISS
raw images, 2,137 have no record, 1,090 of them in VGISS_8210.

A caller that has better pointing can pass it with the ``frame`` override, for example
as a :class:`~oops.frame.Cmatrix`. The host then uses that frame in place of the C
kernels, so missing pointing in the kernels cannot cause an error. The image can then be
read without a clock count.

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
star becomes the light source and the target becomes ``NONE``.

The planet of the encounter is the first word of ``MISSION_PHASE_NAME``, such as
``SATURN`` for ``SATURN ENCOUNTER``. When no label gives it, it comes from the image's
time: Jupiter until 1980, Saturn from 1980, Uranus from 1983, and Neptune from 1987. It
is given as the ``planet`` subfield.

The ``label_target`` subfield keeps ``TARGET_NAME`` as the label spells it.
