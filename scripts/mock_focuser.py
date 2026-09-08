#!/usr/bin/env python3
"""A minimal Alpaca focuser device server for testing the camera simulator's
defocus simulation without real hardware. Exposes just enough of IFocuser for
the camera sim's focuser_client to work: Connected, Position, StepSize.

Usage:
    python3 scripts/mock_focuser.py --host 0.0.0.0 --port 11114 \
        --position 15000 --step-size 2.5

Move it at any time for testing:
    curl -X PUT "http://localhost:11114/debug/position?position=15100"
"""
import argparse
import itertools

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Alpaca Focuser")

state = {
    "connected": True,
    "position": 15000,
    "step_size_um": 5.0,
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


@app.get("/api/v1/focuser/0/connected")
async def get_connected(request: Request):
    return _ok(state["connected"], dict(request.query_params))


@app.put("/api/v1/focuser/0/connected")
async def put_connected(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    state["connected"] = str(form.get("Connected", "true")).lower() in ("true", "1")
    return _ok(None, dict(request.query_params))


@app.get("/api/v1/focuser/0/position")
async def get_position(request: Request):
    return _ok(state["position"], dict(request.query_params))


@app.get("/api/v1/focuser/0/stepsize")
async def get_step_size(request: Request):
    # ASCOM's Focuser.StepSize is already in microns - no conversion needed
    # (unlike Telescope.FocalLength/ApertureDiameter, which are in meters).
    return _ok(state["step_size_um"], dict(request.query_params))


@app.put("/api/v1/focuser/0/move")
async def put_move(request: Request):
    """Standard Alpaca IFocuser.Move(Position) - absolute, since this stub
    doesn't model relative-only focusers."""
    form = dict(await request.form()) or dict(request.query_params)
    if "Position" in form:
        state["position"] = int(float(form["Position"]))
    return _ok(None, dict(request.query_params))


@app.put("/debug/position")
async def set_position(request: Request):
    """Convenience endpoint (not part of Alpaca spec) for testing:
    PUT /debug/position?position=15100&step_size_um=2.5
    """
    params = dict(request.query_params)
    if "position" in params:
        state["position"] = int(float(params["position"]))
    if "step_size_um" in params:
        state["step_size_um"] = float(params["step_size_um"])
    return JSONResponse(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11114)
    ap.add_argument("--position", type=int, default=15000, help="Starting step position")
    ap.add_argument("--step-size", type=float, default=5.0, help="Step size in microns")
    args = ap.parse_args()
    state["position"] = args.position
    state["step_size_um"] = args.step_size
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
