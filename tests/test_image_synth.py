import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from camera_sim.config import CameraConfig
from camera_sim.image_synth import defocus_blur_fwhm_arcsec, render_frame
from camera_sim.sky_model import ProjectedStars


def test_defocus_blur_zero_when_in_focus():
    assert defocus_blur_fwhm_arcsec(0.0, aperture_diameter_mm=200.0, focal_length_mm=800.0) == 0.0


def test_defocus_blur_scales_linearly_with_defocus():
    a = defocus_blur_fwhm_arcsec(50.0, aperture_diameter_mm=200.0, focal_length_mm=800.0)
    b = defocus_blur_fwhm_arcsec(100.0, aperture_diameter_mm=200.0, focal_length_mm=800.0)
    assert a > 0
    assert b == pytest.approx(2 * a)


def test_defocus_blur_matches_hand_computed_value():
    # f/4 scope (200mm aperture, 800mm focal length), 100um defocus.
    # blur_diameter_mm = 0.1mm * 200/800 = 0.025mm
    # blur_arcsec = 0.025mm * 206264.8 / 800mm = 6.446...
    value = defocus_blur_fwhm_arcsec(100.0, aperture_diameter_mm=200.0, focal_length_mm=800.0)
    assert value == pytest.approx(6.446, rel=1e-3)


def test_defocus_blur_zero_without_valid_optics():
    assert defocus_blur_fwhm_arcsec(100.0, aperture_diameter_mm=0.0, focal_length_mm=800.0) == 0.0
    assert defocus_blur_fwhm_arcsec(100.0, aperture_diameter_mm=200.0, focal_length_mm=0.0) == 0.0


def _single_star(x_px: float, y_px: float, vmag: float = 6.0) -> ProjectedStars:
    return ProjectedStars(x_px=np.array([x_px]), y_px=np.array([y_px]), vmag=np.array([vmag]))


def _measure_spread(image_e: np.ndarray) -> float:
    """Second-moment radius (in pixels) of the signal above background, as a
    simple, robust proxy for how "spread out" the rendered star is."""
    bg = np.median(image_e)
    signal = np.clip(image_e - bg, 0, None)
    total = signal.sum()
    if total <= 0:
        return 0.0
    ys, xs = np.indices(image_e.shape)
    cy = (signal * ys).sum() / total
    cx = (signal * xs).sum() / total
    r2 = ((xs - cx) ** 2 + (ys - cy) ** 2)
    return float(np.sqrt((signal * r2).sum() / total))


def test_render_frame_star_spreads_out_with_defocus():
    cam = CameraConfig(
        num_pixels_x=200,
        num_pixels_y=200,
        seeing_fwhm_arcsec=1.0,
        read_noise_e=0.0,
        dark_current_e_per_s_per_px=0.0,
        zero_point_e_per_s_mag0=5.0e7,
    )
    stars = _single_star(100.0, 100.0)
    rng = np.random.default_rng(42)

    in_focus = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
    )
    defocused = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=500.0,
        ccd_temperature_c=0.0, rng=rng,
    )

    assert _measure_spread(defocused) > _measure_spread(in_focus)
