"""Loads the star catalog and projects stars around a given RA/Dec boresight
onto the focal plane pixel grid, given focal length and pixel geometry.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger("camera_sim.sky_model")

ARCSEC_PER_RAD = 206264.80624709636


@dataclass
class ProjectedStars:
    x_px: np.ndarray
    y_px: np.ndarray
    vmag: np.ndarray


class StarCatalog:
    """In-memory Tycho-2-derived catalog, RA/Dec in degrees, J2000."""

    def __init__(self, csv_path: Path):
        ra, dec, vmag = [], [], []
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ra.append(float(row["ra_deg"]))
                dec.append(float(row["dec_deg"]))
                vmag.append(float(row["vmag"]))
        self.ra_deg = np.asarray(ra, dtype=np.float64)
        self.dec_deg = np.asarray(dec, dtype=np.float64)
        self.vmag = np.asarray(vmag, dtype=np.float64)

        ra_rad = np.radians(self.ra_deg)
        dec_rad = np.radians(self.dec_deg)
        cos_dec = np.cos(dec_rad)
        self._unit = np.stack(
            [cos_dec * np.cos(ra_rad), cos_dec * np.sin(ra_rad), np.sin(dec_rad)],
            axis=1,
        )
        logger.info("loaded %d stars from %s", len(self.ra_deg), csv_path)

    def __len__(self) -> int:
        return len(self.ra_deg)


def plate_scale_arcsec_per_px(pixel_size_um: float, focal_length_mm: float) -> float:
    return ARCSEC_PER_RAD * (pixel_size_um / 1000.0) / focal_length_mm


def project_stars(
    catalog: StarCatalog,
    center_ra_deg: float,
    center_dec_deg: float,
    focal_length_mm: float,
    pixel_size_um: float,
    num_pixels_x: int,
    num_pixels_y: int,
    rotation_deg: float = 0.0,
    flip_x: bool = False,
    flip_y: bool = False,
    max_vmag: float | None = None,
    margin_factor: float = 1.3,
) -> ProjectedStars:
    """Gnomonic (tangent-plane) projection of catalog stars around the given
    boresight onto the sensor's pixel grid, with (0,0) at the top-left and
    the boresight at the frame center.
    """
    scale = plate_scale_arcsec_per_px(pixel_size_um, focal_length_mm)  # arcsec/px
    fov_x_deg = scale * num_pixels_x / 3600.0
    fov_y_deg = scale * num_pixels_y / 3600.0
    radius_deg = 0.5 * margin_factor * float(np.hypot(fov_x_deg, fov_y_deg))

    ra0 = np.radians(center_ra_deg)
    dec0 = np.radians(center_dec_deg)
    center_unit = np.array(
        [np.cos(dec0) * np.cos(ra0), np.cos(dec0) * np.sin(ra0), np.sin(dec0)]
    )

    # Coarse angular pre-filter via dot product (cos of angular separation).
    cos_sep = catalog._unit @ center_unit
    cos_radius = np.cos(np.radians(radius_deg))
    mask = cos_sep >= cos_radius
    if max_vmag is not None:
        mask &= catalog.vmag <= max_vmag

    if not np.any(mask):
        return ProjectedStars(np.array([]), np.array([]), np.array([]))

    ra = np.radians(catalog.ra_deg[mask])
    dec = np.radians(catalog.dec_deg[mask])
    vmag = catalog.vmag[mask]

    # Standard gnomonic (tangent-plane) projection.
    cos_dec = np.cos(dec)
    sin_dec = np.sin(dec)
    cos_dec0 = np.cos(dec0)
    sin_dec0 = np.sin(dec0)
    d_ra = ra - ra0
    cos_c = sin_dec0 * sin_dec + cos_dec0 * cos_dec * np.cos(d_ra)

    valid = cos_c > 1e-6
    xi = np.full_like(cos_c, np.nan)
    eta = np.full_like(cos_c, np.nan)
    xi[valid] = (cos_dec[valid] * np.sin(d_ra[valid])) / cos_c[valid]
    eta[valid] = (
        cos_dec0 * sin_dec[valid] - sin_dec0 * cos_dec[valid] * np.cos(d_ra[valid])
    ) / cos_c[valid]

    # xi/eta are in radians on the tangent plane; convert to pixels.
    scale_rad_per_px = np.radians(scale / 3600.0)
    x = xi / scale_rad_per_px
    y = eta / scale_rad_per_px

    if rotation_deg:
        theta = np.radians(rotation_deg)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        x, y = x * cos_t - y * sin_t, x * sin_t + y * cos_t

    if flip_x:
        x = -x
    if flip_y:
        y = -y

    # RA increases to the "left" on sky as conventionally imaged; place
    # origin (0,0) at top-left of the frame, center of frame at (W/2, H/2).
    x_px = num_pixels_x / 2.0 - x
    y_px = num_pixels_y / 2.0 - y

    ok = valid & np.isfinite(x_px) & np.isfinite(y_px)
    ok &= (x_px >= -50) & (x_px < num_pixels_x + 50)
    ok &= (y_px >= -50) & (y_px < num_pixels_y + 50)

    return ProjectedStars(x_px=x_px[ok], y_px=y_px[ok], vmag=vmag[ok])
