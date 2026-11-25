==================
Supported Products
==================

Instrument
==========

The Imaging Science Subsystem (ISS) of each Voyager spacecraft has two vidicon cameras,
the narrow angle camera (NAC) and the wide angle camera (WAC), which took images of 800
by 800 pixels. The host reads the ISS images of both spacecraft in the PDS3 volumes
VGISS_5xxx (Jupiter), VGISS_6xxx (Saturn), VGISS_7xxx (Uranus), and VGISS_8xxx (Neptune).

Products
========

Each image is archived as up to four products, each a VICAR image file with a detached
PDS3 label beside it:

.. list-table::
   :header-rows: 1

   * - Product
     - Example file
     - ``PRODUCT_TYPE``
   * - RAW
     - ``C3450201_RAW.IMG``
     - ``DECOMPRESSED_RAW_IMAGE``
   * - CLEANED
     - ``C3450201_CLEANED.IMG``
     - ``CLEANED_IMAGE``
   * - CALIB
     - ``C3450201_CALIB.IMG``
     - ``CALIBRATED_IMAGE``
   * - GEOMED
     - ``C3450201_GEOMED.IMG``
     - ``GEOMETRICALLY_CORRECTED_IMAGE``

The VICAR file holds a header of its own, so a product carries two labels.
:doc:`user_guide_metadata` describes how the host combines them. The VICAR header gives
no ``PRODUCT_TYPE``, so the host infers it from the processing steps that the header
records.

The volumes also hold the reseau location and geometric correction tables of each image,
in files ending ``_RESLOC`` and ``_GEOMA``. These are tables, and the host does not read
them.

Every image becomes an OOPS :class:`~oops.observation.Snapshot`, with the image's lines
and samples as its *v* and *u* axes. An image read from its file holds the image array in
its ``data`` subfield, except in an astrometry read; a row of an index has no ``data``
(see :doc:`user_guide_index`).

Fields of view
==============

The product type decides the field of view. A GEOMED image has been resampled to remove
the camera's geometric distortion, and its field of view is 1000 by 1000 pixels. Every
other product keeps the camera's 800 by 800 pixels. This holds for every read, an
astrometry read included. A read that reads the image array refuses an array of any
other size (see :doc:`user_guide_errors`). Every field of view is flat: the host does
not model the distortion of the raw image.

Calibration
===========

The ``if_scale`` subfield is the factor that converts a product's data to I/F. When it
is present, the observation carries one calibration, an
:class:`~oops.calibration.FlatCalib` named ``I/F`` with that factor.

When the PDS3 label is read, ``if_scale`` is the ``REFLECTANCE_SCALING_FACTOR`` of the
label's ``IMAGE`` object. The labels of RAW and CLEANED products give an approximate
factor, and those of CALIB and GEOMED products an accurate one.

When the label is not read or gives no factor, and the image file is read, ``if_scale``
comes from the VICAR header. An astrometry read of a label does not read the image file,
so it has no ``if_scale`` when the label gives no factor. The header of a CALIB or GEOMED product has a line of the form ``FOR
(I/F)*10000., MULTIPLY DN VALUE BY x``, and the factor is *x* divided by 10000. For a
Voyager 1 image, it is then corrected as the archive's tutorial
(``DOCUMENT/TUTORIAL.TXT`` on the volumes) documents, because the calibration software
that wrote the header lacked these corrections:

- times 3.345 for an image taken at Saturn, since every Voyager 1 Saturn image was
  calibrated for the solar distance of Jupiter;
- times 40 for the ``SODIUM`` filter and 12.5 for the ``CH4_U`` filter, which were never
  calibrated for Voyager 1.

The header of a RAW or CLEANED product has no such line. When no file read gives a
factor, as for a RAW image file read alone or a row of an index, there is no
``if_scale`` subfield and no calibration, so check for the subfield before using it.

The ``calibrations`` override replaces the host's calibration.
