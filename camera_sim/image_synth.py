"""Renders a simulated CCD/CMOS frame: star PSFs (from projected catalog
positions/magnitudes), dark current, bias, read noise and shot noise, then
applies subframing, binning, gain and ADC quantization.
"""
from __future__ import annotations

import numpy as np

from .config import CameraConfig
from .sky_model import ARCSEC_PER_RAD, ProjectedStars, plate_scale_arcsec_per_px


def defocus_blur_fwhm_arcsec(defocus_um: float, aperture_diameter_mm: float, focal_length_mm: float) -> float:
    """Geometric-optics defocus blur size, treated as an equivalent-FWHM
    contribution to be combined in quadrature with atmospheric seeing.

    A defocus distance at the focal plane produces a blur circle whose
    diameter is (defocus / focal_ratio); converting that physical size to
    an angular one uses the same small-angle relation as the plate scale.
    A real defocused star's PSF is closer to a disk (or an annulus, with a
    central obstruction) than a Gaussian, but approximating its width as an
    equivalent FWHM is a standard simplification here - it gives a smooth,
    monotonic FWHM-vs-focuser-position curve, which is what autofocus
    routines actually need to converge on.
    """
    if aperture_diameter_mm <= 0 or focal_length_mm <= 0:
        return 0.0
    defocus_mm = abs(defocus_um) / 1000.0
    blur_diameter_mm = defocus_mm * aperture_diameter_mm / focal_length_mm
    return blur_diameter_mm * ARCSEC_PER_RAD / focal_length_mm


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


# Above this PSF sigma (px), stars are rendered by _render_blurred_stars
# instead of one _splat_gaussian each: a splat's cost grows with sigma^2,
# and a badly defocused star (sigma in the hundreds of px) made a single
# full-frame exposure take tens of seconds - long enough for clients to time
# the exposure out.
DIRECT_SPLAT_MAX_SIGMA_PX = 8.0


def _render_blurred_stars(
    image: np.ndarray, x_px: np.ndarray, y_px: np.ndarray, fluxes: np.ndarray, sigma_px: float
) -> None:
    """Adds Gaussian-blurred stars to `image` in time roughly independent of
    sigma and star count: deposits every star's flux (bilinearly) onto a grid
    downsampled by `b`, blurs that grid once with an FFT Gaussian at sigma/b
    (>= 4 coarse px, so the result is smooth on the coarse grid), then
    bilinearly interpolates back up. Flux is conserved; the coarse grid is
    zero-padded by 4 sigma so blur from stars just outside the frame still
    reaches it and the FFT's wrap-around doesn't fold light across edges.
    """
    h, w = image.shape
    b = max(1, int(sigma_px // 4))
    sigma_c = sigma_px / b
    pad = int(np.ceil(4 * sigma_c))
    hc = -(-h // b) + 2 * pad
    wc = -(-w // b) + 2 * pad

    # Star positions use pixel-edge coordinates (pixel i spans [i, i+1), see
    # _splat_gaussian); coarse pixel j spans [j*b, (j+1)*b) with its center
    # at continuous coarse index j.
    u = x_px / b - 0.5 + pad
    v = y_px / b - 0.5 + pad
    i0 = np.floor(u).astype(np.int64)
    j0 = np.floor(v).astype(np.int64)
    fu = u - i0
    fv = v - j0
    coarse = np.zeros((hc, wc), dtype=np.float64)
    for dj, wj in ((0, 1.0 - fv), (1, fv)):
        for di, wi in ((0, 1.0 - fu), (1, fu)):
            jj, ii = j0 + dj, i0 + di
            ok = (jj >= 0) & (jj < hc) & (ii >= 0) & (ii < wc)
            np.add.at(coarse, (jj[ok], ii[ok]), (fluxes * wj * wi)[ok])

    fy = np.fft.fftfreq(hc)[:, None]
    fx = np.fft.rfftfreq(wc)[None, :]
    transfer = np.exp(-2.0 * (np.pi * sigma_c) ** 2 * (fy * fy + fx * fx))
    coarse = np.fft.irfft2(np.fft.rfft2(coarse) * transfer, s=coarse.shape)

    # Back to full resolution: fine pixel center i+0.5 sits at continuous
    # coarse index (i+0.5)/b - 0.5 (+pad). Each coarse value is flux per
    # coarse pixel, i.e. b*b fine pixels' worth.
    def _axis(n: int) -> tuple[np.ndarray, np.ndarray]:
        c = (np.arange(n) + 0.5) / b - 0.5 + pad
        k = np.floor(c).astype(np.int64)
        return k, c - k

    ky, ty = _axis(h)
    kx, tx = _axis(w)
    rows = coarse[ky] * (1.0 - ty)[:, None] + coarse[ky + 1] * ty[:, None]
    image += (rows[:, kx] * (1.0 - tx) + rows[:, kx + 1] * tx) / (b * b)


def render_frame(
    stars: ProjectedStars,
    cam: CameraConfig,
    exposure_s: float,
    light: bool,
    gain: int,
    focal_length_mm: float,
    aperture_diameter_mm: float,
    defocus_um: float,
    ccd_temperature_c: float,
    rng: np.random.Generator,
    cover_closed: bool = False,
    calibrator_e_per_s: float = 0.0,
) -> np.ndarray:
    """Returns a full-resolution (num_pixels_y, num_pixels_x) float64 array
    of electrons collected per pixel (before subframing/binning/ADC)."""
    w, h = cam.num_pixels_x, cam.num_pixels_y
    image_e = np.zeros((h, w), dtype=np.float64)

    if light and exposure_s > 0 and cover_closed:
        # Cover blocks the star field entirely, same as a lens cap or a
        # closed flat panel - a calibrator behind it (if on) still
        # illuminates the sensor uniformly, exactly like a real flat frame.
        if calibrator_e_per_s > 0:
            image_e += calibrator_e_per_s * exposure_s
    elif light and exposure_s > 0 and len(stars.vmag) > 0:
        scale = plate_scale_arcsec_per_px(cam.pixel_size_um, focal_length_mm)
        defocus_fwhm = defocus_blur_fwhm_arcsec(defocus_um, aperture_diameter_mm, focal_length_mm)
        total_fwhm = float(np.hypot(cam.seeing_fwhm_arcsec, defocus_fwhm))
        sigma_px = max(0.35, (total_fwhm / 2.3548) / scale)
        # Gain reduces effective zero point slightly to give a simple, monotonic
        # brightness/gain relationship without a separate photometric gain curve.
        gain_factor = 1.0 + (gain / max(1, cam.gain_max)) * 0.5
        flux0 = cam.zero_point_e_per_s_mag0 * exposure_s * gain_factor
        fluxes = flux0 * np.power(10.0, -0.4 * stars.vmag)
        keep = fluxes >= 0.5
        if sigma_px > DIRECT_SPLAT_MAX_SIGMA_PX:
            _render_blurred_stars(image_e, stars.x_px[keep], stars.y_px[keep], fluxes[keep], sigma_px)
        else:
            for x0, y0, flux in zip(stars.x_px[keep], stars.y_px[keep], fluxes[keep]):
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
