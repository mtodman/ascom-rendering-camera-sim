#!/usr/bin/env python3
"""One-time build script: fetch the Tycho-2 catalog (stars with VTmag < 9)
from the VizieR TAP service and write a compact local CSV catalog used by
the camera simulator at runtime. Run this once (or whenever you want to
refresh the catalog); the simulator itself needs no internet access.

Usage:
    python3 scripts/build_catalog.py [--max-mag 9.0] [--out data/tycho2_mag9.csv]
"""
import argparse
import csv
import io
import sys
import time

import requests

TAP_URL = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
# (connect timeout, read timeout) -- a short connect timeout means a stalled
# TCP handshake fails fast and gets retried, instead of hanging indefinitely
# (as urllib's single overall timeout could, in practice, on this host).
REQUEST_TIMEOUT_S = (10, 120)


def fetch_all(max_mag: float) -> list[dict]:
    """Fetch the full catalog in RA bands to stay under any server row caps."""
    rows: list[dict] = []
    band_width = 15.0  # degrees of RA per request band
    ra = 0.0
    while ra < 360.0:
        ra_hi = min(ra + band_width, 360.0)
        query = (
            f"SELECT RAmdeg, DEmdeg, VTmag, BTmag "
            f"FROM \"I/259/tyc2\" "
            f"WHERE VTmag < {max_mag} AND RAmdeg >= {ra} AND RAmdeg < {ra_hi}"
        )
        params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
        for attempt in range(3):
            try:
                resp = requests.post(TAP_URL, data=params, timeout=REQUEST_TIMEOUT_S)
                resp.raise_for_status()
                text = resp.text
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise
                print(f"  retry ({exc})...", file=sys.stderr)
                time.sleep(2)
        reader = csv.DictReader(io.StringIO(text))
        band_rows = list(reader)
        rows.extend(band_rows)
        print(f"  RA [{ra:6.1f},{ra_hi:6.1f}): {len(band_rows)} stars (total {len(rows)})")
        ra = ra_hi
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-mag", type=float, default=9.0)
    ap.add_argument("--out", default="data/tycho2_mag9.csv")
    args = ap.parse_args()

    print(f"Fetching Tycho-2 stars with VTmag < {args.max_mag} from VizieR TAP...")
    rows = fetch_all(args.max_mag)
    print(f"Fetched {len(rows)} stars total.")

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ra_deg", "dec_deg", "vmag", "bv"])
        n_written = 0
        for row in rows:
            try:
                ra = float(row["RAmdeg"])
                dec = float(row["DEmdeg"])
                vmag = float(row["VTmag"])
            except (ValueError, KeyError):
                continue
            bt = row.get("BTmag", "")
            bv = ""
            if bt not in ("", None):
                try:
                    bv = f"{float(bt) - vmag:.3f}"
                except ValueError:
                    bv = ""
            writer.writerow([f"{ra:.6f}", f"{dec:.6f}", f"{vmag:.3f}", bv])
            n_written += 1

    print(f"Wrote {n_written} stars to {args.out}")


if __name__ == "__main__":
    main()
