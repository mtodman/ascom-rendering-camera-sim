#!/usr/bin/env python3
"""A minimal Alpaca filter wheel device server for testing the camera
simulator's per-filter focus offset simulation without real hardware.
Exposes just enough of IFilterWheel for the camera sim's
filter_wheel_client to work: Connected, Position, Names, FocusOffsets.

Position reports -1 while "moving" (this stub moves instantly, but briefly
reports -1 so client fallback-on-moving logic can be exercised too - see
--settle-delay).

Usage:
    python3 scripts/mock_filterwheel.py --host 0.0.0.0 --port 11117

Move it at any time for testing:
    curl -X PUT "http://localhost:11117/api/v1/filterwheel/0/position" -d "Position=1"

Set a custom filter list/offsets:
    curl -X PUT "http://localhost:11117/debug/filters" \
        -d 'names=["Red","Green","Blue"]' -d 'offsets=[100,0,-150]'
"""
import argparse
import asyncio
import itertools
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Alpaca FilterWheel")

state = {
    "connected": True,
    "position": 0,
    "names": ["Red", "Green", "Blue", "Clear", "Ha", "OIII"],
    "offsets": [500, 550, 150, 0, 800, 250],
    "settle_delay_s": 0.0,
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


@app.get("/api/v1/filterwheel/0/connected")
async def get_connected(request: Request):
    return _ok(state["connected"], dict(request.query_params))


@app.put("/api/v1/filterwheel/0/connected")
async def put_connected(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    state["connected"] = str(form.get("Connected", "true")).lower() in ("true", "1")
    return _ok(None, dict(request.query_params))


@app.get("/api/v1/filterwheel/0/position")
async def get_position(request: Request):
    return _ok(state["position"], dict(request.query_params))


@app.get("/api/v1/filterwheel/0/names")
async def get_names(request: Request):
    return _ok(state["names"], dict(request.query_params))


@app.get("/api/v1/filterwheel/0/focusoffsets")
async def get_focus_offsets(request: Request):
    return _ok(state["offsets"], dict(request.query_params))


async def _settle(target: int) -> None:
    if state["settle_delay_s"] > 0:
        state["position"] = -1
        await asyncio.sleep(state["settle_delay_s"])
    state["position"] = target


@app.put("/api/v1/filterwheel/0/position")
async def put_position(request: Request):
    form = dict(await request.form()) or dict(request.query_params)
    if "Position" in form:
        target = int(float(form["Position"]))
        asyncio.create_task(_settle(target))
    return _ok(None, dict(request.query_params))


@app.put("/debug/filters")
async def set_filters(request: Request):
    """Convenience endpoint (not part of Alpaca spec) for testing:
    PUT /debug/filters?names=["Red","Clear"]&offsets=[100,0]&settle_delay_s=1.5
    """
    params = dict(request.query_params)
    if "names" in params:
        state["names"] = json.loads(params["names"])
    if "offsets" in params:
        state["offsets"] = json.loads(params["offsets"])
    if "settle_delay_s" in params:
        state["settle_delay_s"] = float(params["settle_delay_s"])
    return JSONResponse(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11117)
    ap.add_argument("--position", type=int, default=0, help="Starting filter position")
    ap.add_argument("--settle-delay", type=float, default=0.0, help="Seconds to report Position=-1 while moving")
    args = ap.parse_args()
    state["position"] = args.position
    state["settle_delay_s"] = args.settle_delay
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
