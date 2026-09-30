"""Shared Alpaca envelope handling and the common device members (the
subset of the API that's identical across every ASCOM Alpaca device type):
Connected/Connecting/Connect/Disconnect, Action, CommandBlind/Bool/String,
Description/DriverInfo/DriverVersion/InterfaceVersion/Name/SupportedActions,
and DeviceState.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .alpaca_errors import AlpacaError, InvalidValueException, NotConnectedException

_server_transaction_counter = itertools.count(1)


def next_server_transaction_id() -> int:
    return next(_server_transaction_counter)


async def get_params(request: Request) -> dict[str, str]:
    """Alpaca GET requests pass ClientID/ClientTransactionID as query params."""
    return dict(request.query_params)


async def put_params(request: Request) -> dict[str, str]:
    """Alpaca PUT requests pass parameters as form-encoded body (case-insensitive
    keys per spec), falling back to query params if present.
    """
    params: dict[str, str] = dict(request.query_params)
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        for k, v in form.multi_items():
            params[k] = v
    else:
        body = await request.body()
        if body:
            try:
                from urllib.parse import parse_qsl

                for k, v in parse_qsl(body.decode()):
                    params[k] = v
            except Exception:  # noqa: BLE001
                pass
    return params


def find_param(params: dict[str, str], name: str) -> str | None:
    """Alpaca parameter names are case-insensitive."""
    lname = name.lower()
    for k, v in params.items():
        if k.lower() == lname:
            return v
    return None


def client_transaction_id(params: dict[str, str]) -> int:
    raw = find_param(params, "ClientTransactionID")
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def alpaca_response(
    value: Any = None,
    client_txn_id: int = 0,
    include_value: bool = True,
    extra_fields: dict[str, Any] | None = None,
) -> JSONResponse:
    body = {
        "ClientTransactionID": client_txn_id,
        "ServerTransactionID": next_server_transaction_id(),
        "ErrorNumber": 0,
        "ErrorMessage": "",
    }
    if extra_fields:
        body.update(extra_fields)
    if include_value:
        body["Value"] = value
    return JSONResponse(body)


def alpaca_error(exc: AlpacaError, client_txn_id: int = 0) -> JSONResponse:
    body = {
        "ClientTransactionID": client_txn_id,
        "ServerTransactionID": next_server_transaction_id(),
        "ErrorNumber": exc.error_number,
        "ErrorMessage": exc.message,
    }
    return JSONResponse(body)


class CommonDeviceState:
    """Connection/identity state shared by any Alpaca device."""

    def __init__(self, name: str, description: str, driver_info: str, driver_version: str,
                 interface_version: int, supported_actions: list[str] | None = None):
        self.name = name
        self.description = description
        self.driver_info = driver_info
        self.driver_version = driver_version
        self.interface_version = interface_version
        self.supported_actions = supported_actions or []

        self._connected = False
        self._connecting = False
        self._connect_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def require_connected(self) -> None:
        if not self._connected:
            raise NotConnectedException("Device is not connected.")

    def set_connected_sync(self, value: bool) -> None:
        self._connected = bool(value)
        self._connecting = False

    async def begin_connect(self, value: bool) -> None:
        self._connecting = True

        async def _do():
            await asyncio.sleep(0.05)
            self._connected = value
            self._connecting = False

        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
        self._connect_task = asyncio.create_task(_do())

    @property
    def connecting(self) -> bool:
        return self._connecting


def build_common_router(
    prefix: str,
    device_number: int,
    state: CommonDeviceState,
    action_handler: Callable[[str, str], str] | None = None,
    extra_device_state: Callable[[], list[dict[str, Any]]] | None = None,
) -> APIRouter:
    """`action_handler(action, parameters) -> str`, if given, services PUT
    /action for the names in `state.supported_actions` (raising an
    AlpacaError to report failure); without it every Action is rejected.
    `extra_device_state()`, if given, appends device-specific entries to
    DeviceState after the common Connected entry.
    """
    router = APIRouter(prefix=f"{prefix}/{device_number}")

    @router.get("/connected")
    async def get_connected(request: Request):
        params = await get_params(request)
        return alpaca_response(state.connected, client_transaction_id(params))

    @router.put("/connected")
    async def put_connected(request: Request):
        params = await put_params(request)
        raw = find_param(params, "Connected")
        if raw is None:
            return alpaca_error(InvalidValueException("Connected parameter is required."), client_transaction_id(params))
        state.set_connected_sync(raw.lower() in ("true", "1"))
        return alpaca_response(None, client_transaction_id(params))

    @router.get("/connecting")
    async def get_connecting(request: Request):
        params = await get_params(request)
        return alpaca_response(state.connecting, client_transaction_id(params))

    @router.put("/connect")
    async def put_connect(request: Request):
        params = await put_params(request)
        await state.begin_connect(True)
        return alpaca_response(None, client_transaction_id(params))

    @router.put("/disconnect")
    async def put_disconnect(request: Request):
        params = await put_params(request)
        await state.begin_connect(False)
        return alpaca_response(None, client_transaction_id(params))

    @router.get("/description")
    async def get_description(request: Request):
        params = await get_params(request)
        return alpaca_response(state.description, client_transaction_id(params))

    @router.get("/driverinfo")
    async def get_driverinfo(request: Request):
        params = await get_params(request)
        return alpaca_response(state.driver_info, client_transaction_id(params))

    @router.get("/driverversion")
    async def get_driverversion(request: Request):
        params = await get_params(request)
        return alpaca_response(state.driver_version, client_transaction_id(params))

    @router.get("/interfaceversion")
    async def get_interfaceversion(request: Request):
        params = await get_params(request)
        return alpaca_response(state.interface_version, client_transaction_id(params))

    @router.get("/name")
    async def get_name(request: Request):
        params = await get_params(request)
        return alpaca_response(state.name, client_transaction_id(params))

    @router.get("/supportedactions")
    async def get_supportedactions(request: Request):
        params = await get_params(request)
        return alpaca_response(state.supported_actions, client_transaction_id(params))

    @router.put("/action")
    async def put_action(request: Request):
        params = await put_params(request)
        from .alpaca_errors import ActionNotImplementedException

        action = find_param(params, "Action") or ""
        supported = {a.lower() for a in state.supported_actions}
        if action_handler is not None and action.lower() in supported:
            try:
                value = action_handler(action, find_param(params, "Parameters") or "")
            except AlpacaError as exc:
                return alpaca_error(exc, client_transaction_id(params))
            return alpaca_response(value, client_transaction_id(params))
        return alpaca_error(
            ActionNotImplementedException(f"Action '{action}' is not supported."),
            client_transaction_id(params),
        )

    @router.put("/commandblind")
    async def put_commandblind(request: Request):
        from .alpaca_errors import NotImplementedException

        params = await put_params(request)
        return alpaca_error(NotImplementedException("CommandBlind is not implemented."), client_transaction_id(params))

    @router.put("/commandbool")
    async def put_commandbool(request: Request):
        from .alpaca_errors import NotImplementedException

        params = await put_params(request)
        return alpaca_error(NotImplementedException("CommandBool is not implemented."), client_transaction_id(params))

    @router.put("/commandstring")
    async def put_commandstring(request: Request):
        from .alpaca_errors import NotImplementedException

        params = await put_params(request)
        return alpaca_error(NotImplementedException("CommandString is not implemented."), client_transaction_id(params))

    @router.get("/devicestate")
    async def get_devicestate(request: Request):
        params = await get_params(request)
        value = [
            {"Name": "Connected", "Value": state.connected},
        ]
        if extra_device_state is not None:
            value.extend(extra_device_state())
        return alpaca_response(value, client_transaction_id(params))

    return router
