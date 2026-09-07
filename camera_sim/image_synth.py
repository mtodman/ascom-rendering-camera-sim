"""Renders a simulated CCD/CMOS frame: star PSFs (from projected catalog
positions/magnitudes), dark current, bias, read noise and shot noise, then
applies subframing, binning, gain and ADC quantization.
"""
from __future__ import annotations

import numpy as np

from .config import CameraConfig
from .sky_model import ProjectedStars, plate_scale_arcsec_per_px


def _splat_gaussian(image: np.ndarray, x0: float, y0: float, flux: float, sigma_px: float) -> None:
    h, w = image.shape
    radius = max(3, int(np.ceil(5 * sigma_px)))
    xi0, xi1 = int(np.floor(x0 - radius)), int(np.ceil(x0 + radius))
    yi0, yi1 = int(np.floor(y0 - radius)), int(np.ceil(y0 + radius))
    xi0, xi1 = max(xi0, 0), min(xi1, w)
    yi0, yi1 = max(yi0, 0), min(yi1, h)
    if xi0 >= xi1 or yi0 >= yi1:
        return
    xs = np.arange(xi0, xi1) + 0.5
    ys = np.arange(yi0, yi1) + 0.5
    dx = xs[None, :] - x0
    dy = ys[:, None] - y0
    two_sigma2 = 2.0 * sigma_px * sigma_px
    psf = np.exp(-(dx * dx + dy * dy) / two_sigma2)
    norm = psf.sum()
    if norm <= 0:
        return
    image[yi0:yi1, xi0:xi1] += flux * (psf / norm)


def render_frame(
    stars: ProjectedStars,
    cam: CameraConfig,
    exposure_s: float,
    light: bool,
    gain: int,
    focal_length_mm: float,
    ccd_temperature_c: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Returns a full-resolution (num_pixels_y, num_pixels_x) float64 array
    of electrons collected per pixel (before subframing/binning/ADC)."""
    w, h = cam.num_pixels_x, cam.num_pixels_y
    image_e = np.zeros((h, w), dtype=np.float64)

    if light and exposure_s > 0 and len(stars.vmag) > 0:
        scale = plate_scale_arcsec_per_px(cam.pixel_size_um, focal_length_mm)
        sigma_px = max(0.35, (cam.seeing_fwhm_arcsec / 2.3548) / scale)
        # Gain reduces effective zero point slightly to give a simple, monotonic
        # brightness/gain relationship without a separate photometric gain curve.
        gain_factor = 1.0 + (gain / max(1, cam.gain_max)) * 0.5
        flux0 = cam.zero_point_e_per_s_mag0 * exposure_s * gain_factor
        fluxes = flux0 * np.power(10.0, -0.4 * stars.vmag)
        for x0, y0, flux in zip(stars.x_px, stars.y_px, fluxes):
            if flux < 0.5:
                continue
            _splat_gaussian(image_e, x0, y0, flux, sigma_px)

    # Dark current scales with temperature: simple doubling every 6 degC above -20C reference.
    temp_factor = 2.0 ** ((ccd_temperature_c - (-20.0)) / 6.0)
    dark_rate = cam.dark_current_e_per_s_per_px * max(temp_factor, 1e-6)
    dark_e = dark_rate * exposure_s
    image_e += dark_e

    # Shot noise (Poisson) on the accumulated signal (stars + dark).
    image_e = rng.poisson(np.clip(image_e, 0, None)).astype(np.float64)

    # Read noise (Gaussian, electrons).
    image_e += rng.normal(0.0, cam.read_noise_e, size=image_e.shape)

    # Full well clipping.
    np.clip(image_e, 0, cam.full_well_capacity_e, out=image_e)

    return image_e


def subframe_and_bin(
    image_e: np.ndarray,
    start_x: int,
    start_y: int,
    num_x: int,
    num_y: int,
    bin_x: int,
    bin_y: int,
) -> np.ndarray:
    h, w = image_e.shape
    x0 = start_x * bin_x
    y0 = start_y * bin_y
    x1 = min(x0 + num_x * bin_x, w)
    y1 = min(y0 + num_y * bin_y, h)
    region = image_e[y0:y1, x0:x1]

    actual_ny = (y1 - y0) // bin_y
    actual_nx = (x1 - x0) // bin_x
    region = region[: actual_ny * bin_y, : actual_nx * bin_x]
    if bin_x > 1 or bin_y > 1:
        region = region.reshape(actual_ny, bin_y, actual_nx, bin_x).sum(axis=(1, 3))
    return region


def to_adu(
    image_e: np.ndarray, cam: CameraConfig, bias_adu: int | None = None
) -> np.ndarray:
    bias = cam.bias_level_adu if bias_adu is None else bias_adu
    adu = image_e / cam.electrons_per_adu + bias
    np.clip(adu, 0, cam.max_adu, out=adu)
    return adu.astype(np.uint32 if cam.max_adu > 65535 else np.uint16)
