===================================================
Appendix: Timing and Pointing of Voyager ISS Images
===================================================

The time and the pointing of a Voyager ISS image come from two sources that disagree for
many images. This appendix describes the disagreement and the evidence for how the host
resolves it. :doc:`user_guide_geometry` states what the host does.

Terms
=====

FDS count
    The count of the Voyager Flight Data Subsystem clock, written "hhhhh:mm:ddd": an FDS
    hour of 48 minutes, an FDS minute of 48 seconds (0--59), and a tick of 60 ms
    (1--800). The count wraps every 65536 FDS hours, so the same count recurs in each
    *partition* of the clock. An image's number, such as C3450201, is its FDS hour and
    minute, 34502:01.

``STOP_TIME``
    The close of the shutter in UTC, as given by the PDS3 label and the supplemental
    index to 1/100 s. ``IMAGE_TIME`` in the raw image index is the same time to the
    second.

``SPACECRAFT_CLOCK_STOP_COUNT``
    The FDS count of the image, as given by the label and the supplemental index. It is
    "hhhhh:mm:001", the image number, for all but five of the 76,652 VGISS raw images.

C kernel
    The SPICE files ``vg1_*_version1_type1_iss_sedr.bc`` and
    ``vg2_*_version1_type1_iss_sedr.bc``, which give the orientation of each
    spacecraft's scan platform, and so of both cameras, as discrete records stamped in
    FDS ticks.

The disagreement
================

The SPICE clock kernel converts an FDS count to a time. For the 76,635 VGISS raw images
whose labels give both, the image's clock stop count converts to a time a median of
0.9 s after its ``STOP_TIME``, and within 3 s of it for 79% of them. For most of the
others the count falls a whole number of FDS minutes later: 48, 96, 145, 240, 768, or
912 s, and in a few cases much more. For 12 images it falls more than a second earlier,
by up to 7,625 s for C4201035. For C3450201, ``STOP_TIME`` is
1980-10-29T05:57:22.00, while its count, 34502:01:001, converts to 06:10:10.7, 768.7 s
later.

An image has at most one record in the C kernel, stamped at its own clock stop count.
A record looked up by time near ``STOP_TIME`` is therefore the record of another image
whenever the two disagree: in the first 30 rows of the VGISS_6109 raw image index, this
is so for 19 images.

Evidence
========

The record stamped at the image's count holds that image's pointing
--------------------------------------------------------------------

On the C3450201 GEOMED image of Saturn, the record at the image's own count places
Saturn's predicted disk on the bright part of the image: the mean DN is 497 inside the
disk and 89 outside. The record nearest ``STOP_TIME``, which belongs to an image taken 16
FDS minutes earlier, gives 273 inside and 231 outside, so its disk misses Saturn.

``STOP_TIME`` is the close of the shutter
-----------------------------------------

Near Voyager 1's closest approach to Saturn, an error in the time moves each moon by a
different amount, while an error in the pointing moves every body in the image together.
For three wide angle GEOMED images whose count falls 145 s after ``STOP_TIME``, each
moon's predicted lit disk was fitted to the image separately, using the image's own
record for the pointing. At the right time, every moon needs the same shift:

.. list-table::
   :header-rows: 1

   * - Image
     - Moons
     - Disagreement at ``STOP_TIME``
     - Disagreement at the count's time
   * - C3492613
     - Rhea, Dione
     - 4.1 pixels
     - 28.6 pixels
   * - C3490928
     - Enceladus, Dione
     - 3.6 pixels
     - 21.4 pixels
   * - C3490810
     - Enceladus, Dione
     - 2.0 pixels
     - 20.2 pixels

The C kernels were therefore built by stamping each image's pointing at the image's FDS
count rather than at the close of its shutter. The contents of each record are right;
its time stamp is not.

The label's clock count is the right one
----------------------------------------

Where the PDS3 label and the VICAR header give different clock counts, the header's is
one or two FDS minutes later for 62 of the 63 images that differ. For every one checked,
the C kernels hold no record at the header's count and hold the image's pointing at the
label's.

Consequences
============

A lookup of the C kernel by time, whether a call to ``ckgp`` at mid-exposure or a frame
such as a :class:`~oops.frame.SpiceType1Frame`, gives a neighboring image's pointing
whenever the count and ``STOP_TIME`` disagree. Geometry computed that way for those
images is wrong.
