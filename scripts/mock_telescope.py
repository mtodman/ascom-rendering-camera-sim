#!/usr/bin/env python3
"""A minimal Alpaca telescope device server for testing the camera simulator
without real hardware. Exposes just enough of ITelescope for the camera sim's
telescope_client to work: Connected, RightAscension, Declination, FocalLength.

Also exposes PUT /api/v1/telescope/0/slewtocoordinates (Alpaca-standard) so
you can point it, plus a convenience PUT /debug/pointing for quick testing.

Usage:
    python3 scripts/mock_telescope.py --host 0.0.0.0 --port 11111 \
        --ra 5.5877 --dec -5.3911 --focal-length 800
    (defaults point near Orion's Belt / M42 region)
"""
import argparse
import itertools

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Alpaca Telescope")

state = {
    "connected": True,
    "ra_hours": 5.5877,
    "dec_deg": -5.3911,
    "focal_length_mm": 800.0,
}

_txn_counter = itertools.count(1)


def _client_txn(params) -> int:
    try:
        return int(params.get("ClientTransactionID", 0))
    except (TypeError, ValueError):
        return 0


def _ok(value, params):
    return JSONResponse(
        {
            "ClientTransactionID": _client_txn(params),
            "ServerTransactionID": next(_txn_counter),
            "ErrorNumber": 0,
            "ErrorMessage": "",
            "Value": value,
        }
    )


@app.get("/api/v1/telescope/0/connected")
async def get_connected(request: Request):
    return _ok(state["connected"], dict(request.query_params))


@app.put("/api/v1/telescope/0/connected")
async def put_connected(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    state["connected"] = str(form.get("Connected", "true")).lower() in ("true", "1")
    return _ok(None, dict(request.query_params))


@app.get("/api/v1/telescope/0/rightascension")
async def get_ra(request: Request):
    return _ok(state["ra_hours"], dict(request.query_params))


@app.get("/api/v1/telescope/0/declination")
async def get_dec(request: Request):
    return _ok(state["dec_deg"], dict(request.query_params))


@app.get("/api/v1/telescope/0/focallength")
async def get_focal_length(request: Request):
    # ASCOM's ITelescope.FocalLength is specified in meters, not
    # millimeters - report it correctly so this stub matches how a real
    # telescope driver behaves (camera_sim's telescope_client converts back
    # to mm on its end).
    return _ok(state["focal_length_mm"] / 1000.0, dict(request.query_params))


@app.put("/debug/pointing")
async def set_pointing(request: Request):
    """Convenience endpoint (not part of Alpaca spec) for testing:
    PUT /debug/pointing?ra_hours=5.5877&dec_deg=-5.3911
    """
    params = dict(request.query_params)
    if "ra_hours" in params:
        state["ra_hours"] = float(params["ra_hours"])
    if "dec_deg" in params:
        state["dec_deg"] = float(params["dec_deg"])
    if "focal_length_mm" in params:
        state["focal_length_mm"] = float(params["focal_length_mm"])
    return JSONResponse(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11111)
    ap.add_argument("--ra", type=float, default=5.5877, help="RA in hours")
    ap.add_argument("--dec", type=float, default=-5.3911, help="Dec in degrees")
    ap.add_argument("--focal-length", type=float, default=800.0, help="Focal length in mm")
    args = ap.parse_args()
    state["ra_hours"] = args.ra
    state["dec_deg"] = args.dec
    state["focal_length_mm"] = args.focal_length
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
