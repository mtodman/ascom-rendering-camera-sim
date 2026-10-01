"""FocuserDevice state machine (timed motor moves through a BacklashModel)
and the FastAPI router implementing the ASCOM IFocuserV4 surface over
Alpaca REST.

Non-standard extras, exposed as Alpaca Actions (see SupportedActions):
  OpticalPosition -> true drawtube position, as a string integer. The camera
                     simulator uses this to render defocus from where the
                     optics actually are, not where the motor says they are.
  BacklashState   -> JSON snapshot of the backlash model.
  ResetBacklash   -> re-syncs the drawtube to the motor (Parameters: "in",
                     "out" or "" for the configured initial direction).
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import time
from collections import deque
from dataclasses import dataclass

from fastapi import APIRouter, Request

from camera_sim.alpaca_common import (
    CommonDeviceState,
    alpaca_error,
    alpaca_response,
    build_common_router,
    client_transaction_id,
    find_param,
    get_params,
    put_params,
)
from camera_sim.alpaca_errors import (
    AlpacaError,
    InvalidValueException,
    NotImplementedException,
)

from .backlash import BacklashModel, parse_direction
from .config import FocuserSimConfig

logger = logging.getLogger("focuser_sim.device")

MOTION_TICK_S = 0.02
MOVE_HISTORY_LEN = 25


@dataclass
class MoveRecord:
    started: str
    requested: int
    target: int
    start_position: int
    start_optical: int
    end_position: int | None = None
    end_optical: int | None = None
    outcome: str = "moving"  # moving | done | halted | retargeted

    @property
    def lost_steps(self) -> int | None:
        """Motor steps that didn't move the drawtube (absorbed by backlash)."""
        if self.end_position is None or self.end_optical is None:
            return None
        return abs(self.end_position - self.start_position) - abs(self.end_optical - self.start_optical)


class FocuserDevice:
    def __init__(self, cfg: FocuserSimConfig):
        self.cfg = cfg
        self.model = BacklashModel(
            cfg.start_position,
            cfg.backlash_steps,
            parse_direction(cfg.initial_engaged_direction),
        )
        self.target = self.model.motor
        self.is_moving = False
        self.history: deque[MoveRecord] = deque(maxlen=MOVE_HISTORY_LEN)
        self._current: MoveRecord | None = None
        self._motion_task: asyncio.Task | None = None

        self.common = CommonDeviceState(
            name="ASCOM Alpaca Backlash Focuser Simulator",
            description="Absolute focuser simulator with configurable mechanical backlash.",
            driver_info="focuser_sim v1.0 (Python/FastAPI)",
            driver_version="1.0",
            interface_version=4,
            supported_actions=["OpticalPosition", "BacklashState", "ResetBacklash"],
        )

    # ---- config ----------------------------------------------------------------------
    def apply_config(self) -> None:
        """Re-reads self.cfg after the setup UI changed it in place."""
        self.model.set_backlash(self.cfg.backlash_steps)

    # ---- motion ----------------------------------------------------------------------
    def move(self, requested: int) -> None:
        start = self.model.motor
        target = min(max(requested, 0), self.cfg.max_step)
        # Clamp the distance of any single move to MaxIncrement.
        if abs(target - start) > self.cfg.max_increment:
            target = start + self.cfg.max_increment * (1 if target > start else -1)

        self._finish_record("retargeted")
        self._current = MoveRecord(
            started=datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            requested=requested,
            target=target,
            start_position=start,
            start_optical=self.model.optical,
        )
        self.history.appendleft(self._current)
        self.target = target
        logger.info("move requested=%d target=%d from position=%d optical=%d",
                    requested, target, start, self.model.optical)

        if self.cfg.steps_per_second <= 0:
            self.model.move(target - self.model.motor)
            self._finish_record("done")
            return

        # Set synchronously, so an IsMoving poll straight after Move sees it.
        self.is_moving = True
        if self._motion_task is None or self._motion_task.done():
            self._motion_task = asyncio.create_task(self._motion_loop())

    async def _motion_loop(self) -> None:
        last = time.monotonic()
        carry = 0.0
        try:
            while self.model.motor != self.target:
                await asyncio.sleep(MOTION_TICK_S)
                now = time.monotonic()
                carry += (now - last) * self.cfg.steps_per_second
                last = now
                n = int(carry)
                carry -= n
                remaining = self.target - self.model.motor
                step = min(n, abs(remaining))
                if step:
                    self.model.move(step if remaining > 0 else -step)
            self._finish_record("done")
        finally:
            self.is_moving = False

    def halt(self) -> None:
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()
        self.target = self.model.motor
        self.is_moving = False
        self._finish_record("halted")

    def _finish_record(self, outcome: str) -> None:
        rec = self._current
        if rec is None:
            return
        rec.end_position = self.model.motor
        rec.end_optical = self.model.optical
        rec.outcome = outcome
        self._current = None
        logger.info("move %s: position %d->%d optical %d->%d (%d steps lost to backlash)",
                    outcome, rec.start_position, rec.end_position, rec.start_optical, rec.end_optical,
                    rec.lost_steps)

    def reset_backlash(self, direction: str = "") -> None:
        self.halt()
        engaged = parse_direction(direction or self.cfg.initial_engaged_direction)
        self.model.reset(engaged=engaged)
        logger.info("backlash reset: optical re-synced to position %d, engaged %s", self.model.motor, direction)

    # ---- actions / state ---------------------------------------------------------------
    def run_action(self, action: str, parameters: str) -> str:
        self.common.require_connected()
        name = action.lower()
        if name == "opticalposition":
            return str(self.model.optical)
        if name == "backlashstate":
            return json.dumps(self.status())
        if name == "resetbacklash":
            p = parameters.strip().lower()
            if p not in ("", "in", "out"):
                raise InvalidValueException("ResetBacklash parameter must be 'in', 'out' or empty.")
            self.reset_backlash(p)
            return ""
        raise InvalidValueException(f"Unknown action '{action}'.")

    def status(self) -> dict:
        snap = self.model.snapshot()
        d = dataclasses.asdict(snap)
        d.update(
            offset_steps=snap.offset_steps,
            is_moving=self.is_moving,
            target=self.target,
            connected=self.common.connected,
        )
        return d

    def history_rows(self) -> list[dict]:
        return [dict(dataclasses.asdict(r), lost_steps=r.lost_steps) for r in self.history]


def build_focuser_router(device_number: int, device: FocuserDevice) -> APIRouter:
    def _device_state() -> list[dict]:
        return [
            {"Name": "IsMoving", "Value": device.is_moving},
            {"Name": "Position", "Value": device.model.motor},
            {"Name": "Temperature", "Value": device.cfg.temperature_c},
            {"Name": "TimeStamp", "Value": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        ]

    router = build_common_router(
        "/api/v1/focuser", device_number, device.common,
        action_handler=device.run_action, extra_device_state=_device_state,
    )

    def _getter(name: str, fn):
        @router.get(f"/{name}")
        async def _get(request: Request):
            params = await get_params(request)
            txn = client_transaction_id(params)
            try:
                device.common.require_connected()
                return alpaca_response(fn(), txn)
            except AlpacaError as exc:
                return alpaca_error(exc, txn)

    _getter("absolute", lambda: True)
    _getter("ismoving", lambda: device.is_moving)
    _getter("maxincrement", lambda: device.cfg.max_increment)
    _getter("maxstep", lambda: device.cfg.max_step)
    _getter("position", lambda: device.model.motor)
    _getter("stepsize", lambda: device.cfg.step_size_um)
    _getter("tempcomp", lambda: False)
    _getter("tempcompavailable", lambda: False)
    _getter("temperature", lambda: device.cfg.temperature_c)

    @router.put("/tempcomp")
    async def put_tempcomp(request: Request):
        params = await put_params(request)
        txn = client_transaction_id(params)
        try:
            device.common.require_connected()
            raw = find_param(params, "TempComp")
            if raw is None or raw.lower() not in ("true", "false"):
                raise InvalidValueException("TempComp parameter must be True or False.")
            if raw.lower() == "true":
                raise NotImplementedException("Temperature compensation is not available.")
            return alpaca_response(None, txn, include_value=False)
        except AlpacaError as exc:
            return alpaca_error(exc, txn)

    @router.put("/halt")
    async def put_halt(request: Request):
        params = await put_params(request)
        txn = client_transaction_id(params)
        try:
            device.common.require_connected()
            device.halt()
            return alpaca_response(None, txn, include_value=False)
        except AlpacaError as exc:
            return alpaca_error(exc, txn)

    @router.put("/move")
    async def put_move(request: Request):
        params = await put_params(request)
        txn = client_transaction_id(params)
        try:
            device.common.require_connected()
            raw = find_param(params, "Position")
            if raw is None:
                raise InvalidValueException("Position parameter is required.")
            try:
                position = int(raw)
            except ValueError:
                raise InvalidValueException(f"Invalid Position '{raw}'.") from None
            device.move(position)
            return alpaca_response(None, txn, include_value=False)
        except AlpacaError as exc:
            return alpaca_error(exc, txn)

    return router
