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


def _base_cam(**overrides) -> CameraConfig:
    defaults = dict(
        num_pixels_x=100,
        num_pixels_y=100,
        seeing_fwhm_arcsec=1.0,
        read_noise_e=0.0,
        dark_current_e_per_s_per_px=0.0,
        bias_level_adu=0,
        zero_point_e_per_s_mag0=5.0e7,
    )
    defaults.update(overrides)
    return CameraConfig(**defaults)


def test_closed_cover_suppresses_stars_even_with_populated_star_list():
    cam = _base_cam()
    stars = _single_star(50.0, 50.0, vmag=2.0)  # bright star, would easily saturate if rendered
    rng = np.random.default_rng(1)

    frame = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
        cover_closed=True, calibrator_e_per_s=0.0,
    )

    assert frame.max() == 0.0  # no star, no dark/bias/noise configured -> flat zero frame


def test_open_cover_is_unaffected_baseline():
    cam = _base_cam()
    stars = _single_star(50.0, 50.0, vmag=2.0)
    rng = np.random.default_rng(1)

    frame = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
        cover_closed=False, calibrator_e_per_s=0.0,
    )

    assert frame.max() > 0.0  # star renders normally


def test_calibrator_produces_uniform_illumination_scaling_with_brightness():
    cam = _base_cam()
    stars = _single_star(50.0, 50.0, vmag=2.0)  # should be entirely suppressed by the closed cover

    rng = np.random.default_rng(7)
    dim = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
        cover_closed=True, calibrator_e_per_s=1000.0,
    )
    rng = np.random.default_rng(7)
    bright = render_frame(
        stars, cam, exposure_s=1.0, light=True, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
        cover_closed=True, calibrator_e_per_s=5000.0,
    )

    # Uniform illumination: every pixel should be close to the mean (no PSF
    # spatial structure like a star field would have). The comparison uses a
    # loose tolerance since each frame is an independent Poisson-noise
    # realization, not a deterministic multiple of the other.
    assert dim.std() / dim.mean() < 0.1
    assert bright.mean() == pytest.approx(dim.mean() * 5, rel=0.02)


def test_calibrator_gated_by_light_flag():
    # A client-requested dark frame (Light=False) must not see calibrator
    # illumination, same as it never sees stars - matches real hardware,
    # where the shutter stays closed regardless of what's in front of it.
    cam = _base_cam()
    stars = ProjectedStars(np.array([]), np.array([]), np.array([]))
    rng = np.random.default_rng(3)

    frame = render_frame(
        stars, cam, exposure_s=1.0, light=False, gain=0,
        focal_length_mm=800.0, aperture_diameter_mm=200.0, defocus_um=0.0,
        ccd_temperature_c=0.0, rng=rng,
        cover_closed=True, calibrator_e_per_s=5000.0,
    )

    assert frame.max() == 0.0
