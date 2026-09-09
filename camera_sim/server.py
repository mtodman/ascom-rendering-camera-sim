"""Builds and runs the Alpaca camera simulator FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .camera import CameraDevice, build_camera_router
from .config import Settings, load_raw_or_empty, load_settings_with_raw, resolve_config_path
from .discovery import start_discovery_server
from .management import build_management_router
from .setup_ui import build_setup_router
from .sky_model import StarCatalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("camera_sim.server")


def create_app(settings: Settings | None = None, config_path: Path | None = None) -> FastAPI:
    config_path = config_path or resolve_config_path()
    if settings is None:
        settings, raw_config = load_settings_with_raw(config_path)
    else:
        raw_config = load_raw_or_empty(config_path)
    catalog = StarCatalog(settings.catalog_path())
    device = CameraDevice(
        settings.camera,
        settings.telescope,
        settings.focuser,
        settings.filter_wheel,
        settings.cover_calibrator,
        catalog,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.discovery_transport = await start_discovery_server(
            settings.server.discovery_port, settings.server.port
        )
        device.ensure_cooling_task()
        logger.info(
            "Camera simulator ready: %dx%d px, %.2fum pixels, sensor=%s",
            settings.camera.num_pixels_x,
            settings.camera.num_pixels_y,
            settings.camera.pixel_size_um,
            settings.camera.sensor_name,
        )
        yield
        transport = getattr(app.state, "discovery_transport", None)
        if transport:
            transport.close()

    app = FastAPI(title=settings.server.server_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.device = device

    app.include_router(build_management_router(settings))
    app.include_router(build_camera_router(0, device))
    app.include_router(build_setup_router(settings, config_path, device, raw_config))

    return app


def main() -> None:
    import uvicorn

    app = create_app()
    settings = app.state.settings
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level="info")


if __name__ == "__main__":
    main()
