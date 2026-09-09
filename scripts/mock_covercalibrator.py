#!/usr/bin/env python3
"""A minimal Alpaca cover/calibrator device server for testing the camera
simulator's cover-closed/calibrator simulation without real hardware.
Exposes just enough of ICoverCalibrator for the camera sim's
cover_calibrator_client to work: Connected, CoverState, CalibratorState,
Brightness, MaxBrightness, plus OpenCover/CloseCover/CalibratorOn/Off.

CoverStatus/CalibratorStatus ordinals match the real ASCOM enums (confirmed
against a real Alpaca CoverCalibrator device): Closed=1, Moving=2, Open=3
for cover; Off=1, NotReady=2, Ready=3 for calibrator. This stub moves
instantly (no Moving/NotReady transient state) for simplicity.

Usage:
    python3 scripts/mock_covercalibrator.py --host 0.0.0.0 --port 11116

Toggle it at any time for testing:
    curl -X PUT "http://localhost:11116/debug/state?cover=closed&calibrator_on=true&brightness=50"
"""
import argparse
import itertools

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Alpaca CoverCalibrator")

COVER_OPEN = 3
COVER_CLOSED = 1
CALIBRATOR_OFF = 1
CALIBRATOR_READY = 3

state = {
    "connected": True,
    "cover_state": COVER_OPEN,
    "calibrator_state": CALIBRATOR_OFF,
    "brightness": 0,
    "max_brightness": 100,
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


@app.get("/api/v1/covercalibrator/0/connected")
async def get_connected(request: Request):
    return _ok(state["connected"], dict(request.query_params))


@app.put("/api/v1/covercalibrator/0/connected")
async def put_connected(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    state["connected"] = str(form.get("Connected", "true")).lower() in ("true", "1")
    return _ok(None, dict(request.query_params))


@app.get("/api/v1/covercalibrator/0/coverstate")
async def get_cover_state(request: Request):
    return _ok(state["cover_state"], dict(request.query_params))


@app.get("/api/v1/covercalibrator/0/calibratorstate")
async def get_calibrator_state(request: Request):
    return _ok(state["calibrator_state"], dict(request.query_params))


@app.get("/api/v1/covercalibrator/0/brightness")
async def get_brightness(request: Request):
    return _ok(state["brightness"], dict(request.query_params))


@app.get("/api/v1/covercalibrator/0/maxbrightness")
async def get_max_brightness(request: Request):
    return _ok(state["max_brightness"], dict(request.query_params))


@app.put("/api/v1/covercalibrator/0/opencover")
async def put_opencover(request: Request):
    state["cover_state"] = COVER_OPEN
    return _ok(None, dict(request.query_params))


@app.put("/api/v1/covercalibrator/0/closecover")
async def put_closecover(request: Request):
    state["cover_state"] = COVER_CLOSED
    return _ok(None, dict(request.query_params))


@app.put("/api/v1/covercalibrator/0/calibratoron")
async def put_calibratoron(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    if "Brightness" in form:
        state["brightness"] = int(float(form["Brightness"]))
    state["calibrator_state"] = CALIBRATOR_READY
    return _ok(None, dict(request.query_params))


@app.put("/api/v1/covercalibrator/0/calibratoroff")
async def put_calibratoroff(request: Request):
    state["calibrator_state"] = CALIBRATOR_OFF
    state["brightness"] = 0
    return _ok(None, dict(request.query_params))


@app.put("/debug/state")
async def set_state(request: Request):
    """Convenience endpoint (not part of Alpaca spec) for testing:
    PUT /debug/state?cover=closed&calibrator_on=true&brightness=50
    """
    params = dict(request.query_params)
    if "cover" in params:
        state["cover_state"] = COVER_CLOSED if params["cover"].lower() == "closed" else COVER_OPEN
    if "calibrator_on" in params:
        on = params["calibrator_on"].lower() in ("true", "1")
        state["calibrator_state"] = CALIBRATOR_READY if on else CALIBRATOR_OFF
        if not on:
            state["brightness"] = 0
    if "brightness" in params:
        state["brightness"] = int(float(params["brightness"]))
    return JSONResponse(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11116)
    ap.add_argument("--cover", choices=["open", "closed"], default="open")
    ap.add_argument("--max-brightness", type=int, default=100)
    args = ap.parse_args()
    state["cover_state"] = COVER_CLOSED if args.cover == "closed" else COVER_OPEN
    state["max_brightness"] = args.max_brightness
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
