"""Builds and runs the Alpaca backlash focuser simulator FastAPI application.

    python -m focuser_sim.server
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from ruamel.yaml.comments import CommentedMap

from camera_sim.alpaca_common import alpaca_response, client_transaction_id, get_params
from camera_sim.discovery import start_discovery_server

from .config import FocuserSimSettings, load_settings_with_raw, resolve_config_path
from .device import FocuserDevice, build_focuser_router
from .setup_ui import build_setup_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("focuser_sim.server")

DEVICE_NAME = "Backlash Focuser Simulator"
DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "camera-sim.local/focuser/0"))


def build_management_router(settings: FocuserSimSettings) -> APIRouter:
    router = APIRouter(prefix="/management")

    @router.get("/apiversions")
    async def apiversions(request: Request):
        params = await get_params(request)
        return alpaca_response([1], client_transaction_id(params))

    @router.get("/v1/description")
    async def description(request: Request):
        params = await get_params(request)
        value = {
            "ServerName": settings.server.server_name,
            "Manufacturer": settings.server.manufacturer,
            "ManufacturerVersion": settings.server.manufacturer_version,
            "Location": settings.server.location,
        }
        return alpaca_response(value, client_transaction_id(params))

    @router.get("/v1/configureddevices")
    async def configureddevices(request: Request):
        params = await get_params(request)
        value = [{"DeviceName": DEVICE_NAME, "DeviceType": "Focuser", "DeviceNumber": 0, "UniqueID": DEVICE_UUID}]
        return alpaca_response(value, client_transaction_id(params))

    return router


def create_app(settings: FocuserSimSettings | None = None, config_path: Path | None = None) -> FastAPI:
    config_path = config_path or resolve_config_path()
    if settings is None:
        settings, raw_config = load_settings_with_raw(config_path)
    else:
        raw_config = CommentedMap()
    device = FocuserDevice(settings.focuser)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        transport = None
        if settings.server.discovery_port:
            transport = await start_discovery_server(settings.server.discovery_port, settings.server.port)
        logger.info(
            "Focuser simulator ready: position=%d, backlash=%d steps, %.0f steps/s",
            device.model.motor, settings.focuser.backlash_steps,
            settings.focuser.steps_per_second,
        )
        yield
        if transport:
            transport.close()

    app = FastAPI(title=settings.server.server_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.device = device

    app.include_router(build_management_router(settings))
    app.include_router(build_focuser_router(0, device))
    app.include_router(build_setup_router(settings, config_path, device, raw_config))
    return app


def main() -> None:
    import uvicorn

    app = create_app()
    settings = app.state.settings
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level="info")


if __name__ == "__main__":
    main()
