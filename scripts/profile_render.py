#!/usr/bin/env python3
"""Times (and profiles) the camera simulator's light-frame render path with
the real config.yaml and star catalog, without involving the server or any
Alpaca devices - for diagnosing slow readouts on a particular machine.

Usage (from the repo root):
    ./venv/bin/python scripts/profile_render.py [--defocus-um 150]
"""
import argparse
import cProfile
import os
import platform
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from camera_sim import image_synth
from camera_sim.config import load_settings
from camera_sim.sky_model import StarCatalog, project_stars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--defocus-um", type=float, default=150.0)
    ap.add_argument("--exposure-s", type=float, default=1.0)
    args = ap.parse_args()

    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}, {platform.platform()}")
    s = load_settings()
    cam, tel = s.camera, s.telescope

    t = time.perf_counter()
    catalog = StarCatalog(s.catalog_path())
    print(f"catalog load: {time.perf_counter() - t:.2f}s ({len(catalog)} stars)")

    t = time.perf_counter()
    stars = project_stars(
        catalog, tel.fallback_ra_hours * 15.0, tel.fallback_dec_deg, tel.fallback_focal_length_mm,
        cam.pixel_size_um, cam.num_pixels_x, cam.num_pixels_y, cam.rotation_deg, cam.flip_x, cam.flip_y,
    )
    print(f"project_stars: {time.perf_counter() - t:.2f}s -> {len(stars.vmag)} stars in frame "
          f"(brightest V={stars.vmag.min() if len(stars.vmag) else float('nan'):.2f})")

    rng = np.random.default_rng()
    prof = cProfile.Profile()
    t = time.perf_counter()
    prof.enable()
    image_synth.render_frame(
        stars, cam, args.exposure_s, True, 0, tel.fallback_focal_length_mm,
        tel.fallback_aperture_diameter_mm, args.defocus_um, cam.ambient_temp_c, rng,
    )
    prof.disable()
    print(f"render_frame (light, {cam.num_pixels_x}x{cam.num_pixels_y}, defocus {args.defocus_um}um): "
          f"{time.perf_counter() - t:.2f}s")
    pstats.Stats(prof).sort_stats("cumulative").print_stats(12)


if __name__ == "__main__":
    main()
