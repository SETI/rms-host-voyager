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

Every image becomes an OOPS :class:`~oops.observation.Snapshot`, with the image in its
``data`` subfield and the image's lines and samples as its *v* and *u* axes.

Fields of view
==============

The product type decides the field of view. A GEOMED image has been resampled to remove
the camera's geometric distortion, and its field of view is 1000 by 1000 pixels. Every
other product keeps the camera's 800 by 800 pixels. Every field of view is flat: the host
does not model the distortion of the raw image.

Calibration
===========

The VICAR header of a CALIB or GEOMED product has a line of the form ``FOR
(I/F)*10000., MULTIPLY DN VALUE BY x``. The host divides *x* by 10000 and gives the result
as the ``if_scale`` subfield, the factor that converts the product's data to I/F. The
observation then carries one calibration, an :class:`~oops.calibration.FlatCalib` named
``I/F`` with that factor.

Only the VICAR header gives this factor. The header of a RAW or CLEANED product has no
such line. An astrometry read of a PDS3 label leaves the header unread, and a row of an
index comes from the index tables alone. These observations have no ``if_scale``
subfield and no calibration, so check for the subfield before using it. The PDS3 label's
``REFLECTANCE_SCALING_FACTOR`` is not used: for the Voyager 1 images of Saturn it is
3.345 times larger than the header's factor.

The ``calibrations`` override replaces the host's calibration.
