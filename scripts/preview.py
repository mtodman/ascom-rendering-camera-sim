#!/usr/bin/env python3
"""Dev helper: drives a running camera simulator through one exposure and
saves the resulting frame as a stretched PNG for visual inspection.

Usage:
    python3 scripts/preview.py --base-url http://localhost:11112 \
        --duration 3 --out /tmp/preview.png
"""
import argparse
import struct
import time

import numpy as np
import requests
from PIL import Image


def alpaca_put(base_url: str, path: str, **params):
    resp = requests.put(f"{base_url}/api/v1/camera/0/{path}", data=params, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"{path} -> {body}")
    return body


def alpaca_get(base_url: str, path: str, **params):
    resp = requests.get(f"{base_url}/api/v1/camera/0/{path}", params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("ErrorNumber", 0) != 0:
        raise RuntimeError(f"{path} -> {body}")
    return body["Value"]


# Alpaca's binary ImageBytes transfer (see alpaca_common.py's
# alpaca_imagebytes_response docstring for the header layout) - large frames
# are dramatically slower to fetch and parse as JSON, so this preview script
# uses the same negotiation real Alpaca clients (N.I.N.A. included) use.
_IMAGEBYTES_DTYPE = {1: "<i2", 8: "<u2", 2: "<i4", 9: "<u4"}


def alpaca_get_imagearray(base_url: str, timeout_s: float = 60.0) -> np.ndarray:
    resp = requests.get(
        f"{base_url}/api/v1/camera/0/imagearray",
        headers={"Accept": "application/imagebytes"},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    if "application/imagebytes" not in resp.headers.get("content-type", ""):
        return np.array(resp.json()["Value"], dtype=np.float64)
    body = resp.content
    (_meta_version, error_number, _client_txn, _server_txn,
     data_start, _image_type, xmsn_type, _rank, dim1, dim2, _dim3) = struct.unpack("<11i", body[:44])
    if error_number != 0:
        raise RuntimeError(f"imagearray -> ErrorNumber {error_number}: {body[44:].decode('utf-8')}")
    flat = np.frombuffer(body, dtype=_IMAGEBYTES_DTYPE[xmsn_type], offset=data_start)
    return flat.reshape(dim1, dim2).astype(np.float64)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:11112")
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--out", default="/tmp/preview.png")
    args = ap.parse_args()

    alpaca_put(args.base_url, "connected", Connected="true")
    alpaca_put(args.base_url, "startexposure", Duration=args.duration, Light="true")

    while True:
        ready = alpaca_get(args.base_url, "imageready")
        if ready:
            break
        time.sleep(0.2)

    array = alpaca_get_imagearray(args.base_url)
    # ImageArray is [x][y] per Alpaca convention; transpose back to [y][x] for display.
    array = array.T

    # A sparse star field is >99% background, so a plain percentile stretch
    # just stretches read noise into visible "static". Instead: estimate the
    # background level/noise robustly (median/MAD), clip anything within a
    # few sigma of background to black (a standard astro "screen stretch"
    # black-point), then asinh-stretch what's left so faint stars are still
    # visible without blowing out bright ones.
    bg = np.median(array)
    mad = np.median(np.abs(array - bg)) * 1.4826  # robust sigma estimate
    black_point = bg + 3.0 * max(mad, 1e-6)
    signal = np.clip(array - black_point, 0, None)
    peak = max(signal.max(), 1e-6)
    stretched = np.arcsinh(signal / peak * 20.0) / np.arcsinh(20.0)
    img = Image.fromarray((stretched * 255).astype(np.uint8), mode="L")
    img.save(args.out)
    print(f"Saved {args.out} ({array.shape[1]}x{array.shape[0]}, "
          f"min={array.min():.0f} max={array.max():.0f} mean={array.mean():.1f})")


if __name__ == "__main__":
    main()
