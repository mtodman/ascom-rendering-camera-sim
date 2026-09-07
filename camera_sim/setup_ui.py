"""Web UI for configuring the simulator, following the Alpaca spec's
standard "Setup" URL convention (GET /setup, GET/POST
/setup/v1/{devicetype}/{devicenumber}/setup) so ASCOM tools that open a
driver's "Setup" page land somewhere useful.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ruamel.yaml.comments import CommentedMap

from .camera import CameraDevice
from .config import REPO_ROOT, CameraConfig, CatalogConfig, ServerConfig, Settings, TelescopeConfig, update_model_in_place
from .discovery_client import discover_telescopes
from .form_utils import describe_fields, parse_form_to_model
from .sky_model import StarCatalog

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def build_setup_router(
    settings: Settings, config_path: Path, device: CameraDevice, raw_config: CommentedMap
) -> APIRouter:
    router = APIRouter()

    def _sections(cfg_server: ServerConfig, cfg_tel: TelescopeConfig, cfg_cam: CameraConfig, cfg_cat: CatalogConfig):
        return [
            {
                "id": "server",
                "title": "Server / network",
                "desc": "Requires restarting the simulator process to take effect.",
                "fields": describe_fields(cfg_server),
            },
            {
                "id": "telescope",
                "title": "Telescope",
                "desc": "Where to query for pointing, and fallback values used if it's unreachable.",
                "fields": describe_fields(cfg_tel),
            },
            {
                "id": "camera",
                "title": "Camera sensor & simulation",
                "desc": "Applied to the next exposure immediately, no restart needed.",
                "fields": describe_fields(cfg_cam),
            },
            {
                "id": "catalog",
                "title": "Star catalog",
                "desc": "Changing the path reloads the catalog immediately.",
                "fields": describe_fields(cfg_cat),
            },
        ]

    async def _status() -> dict:
        pointing = await asyncio.to_thread(device.telescope.get_pointing)
        return {
            "connected": device.common.connected,
            "camera_state": device.camera_state.name,
            "telescope_url": f"{settings.telescope.alpaca_base_url}/api/v1/telescope/{settings.telescope.device_number}",
            "telescope_ra_hours": round(pointing.ra_hours, 4),
            "telescope_dec_deg": round(pointing.dec_deg, 4),
            "telescope_pointing_source": pointing.source,
        }

    @router.get("/")
    async def root():
        return RedirectResponse(url="/setup")

    @router.get("/setup")
    async def setup_index(request: Request):
        return templates.TemplateResponse(
            request,
            "setup_index.html",
            {
                "server_name": settings.server.server_name,
                "description": "This server hosts a single simulated camera device.",
                "devices": [
                    {
                        "name": "Sky-Rendering Camera Simulator",
                        "device_type": "Camera",
                        "device_number": 0,
                        "setup_url": "/setup/v1/camera/0/setup",
                    }
                ],
            },
        )

    @router.get("/setup/v1/camera/{device_number}/discover_telescopes")
    async def discover_telescopes_endpoint(device_number: int):
        found = await discover_telescopes(settings.server.discovery_port)
        return JSONResponse(
            [
                {
                    "base_url": t.base_url,
                    "device_number": t.device_number,
                    "label": t.label,
                }
                for t in found
            ]
        )

    @router.get("/setup/v1/camera/{device_number}/setup")
    async def camera_setup_get(request: Request, device_number: int):
        return templates.TemplateResponse(
            request,
            "camera_setup.html",
            {
                "server_name": settings.server.server_name,
                "status": await _status(),
                "sections": _sections(settings.server, settings.telescope, settings.camera, settings.catalog),
                "saved": False,
                "errors": [],
                "restart_needed": False,
            },
        )

    @router.post("/setup/v1/camera/{device_number}/setup")
    async def camera_setup_post(request: Request, device_number: int):
        form = dict((await request.form()).multi_items())

        new_server, server_errors = parse_form_to_model(ServerConfig, form)
        new_telescope, telescope_errors = parse_form_to_model(TelescopeConfig, form)
        new_camera, camera_errors = parse_form_to_model(CameraConfig, form)
        new_catalog, catalog_errors = parse_form_to_model(CatalogConfig, form)
        errors = server_errors + telescope_errors + camera_errors + catalog_errors
        server_changed = False

        new_catalog_obj = None
        if not errors:
            server_changed = new_server.model_dump() != settings.server.model_dump()
            catalog_changed = new_catalog.path != settings.catalog.path
            if catalog_changed:
                # Validate the new catalog loads before committing any change,
                # so a bad path can't leave settings/device in a half-applied
                # state (config.yaml unsaved but the in-memory path already
                # pointing somewhere broken).
                catalog_path = Path(new_catalog.path)
                if not catalog_path.is_absolute():
                    catalog_path = REPO_ROOT / new_catalog.path
                try:
                    new_catalog_obj = StarCatalog(catalog_path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Catalog path: could not load '{new_catalog.path}': {exc}")

        if not errors:
            update_model_in_place(settings.server, new_server)
            update_model_in_place(settings.telescope, new_telescope)
            update_model_in_place(settings.camera, new_camera)
            update_model_in_place(settings.catalog, new_catalog)
            if new_catalog_obj is not None:
                device.catalog = new_catalog_obj
            settings.save(config_path, raw_config)

        return templates.TemplateResponse(
            request,
            "camera_setup.html",
            {
                "server_name": settings.server.server_name,
                "status": await _status(),
                "sections": _sections(settings.server, settings.telescope, settings.camera, settings.catalog),
                "saved": not errors,
                "errors": errors,
                "restart_needed": not errors and server_changed,
            },
        )

    return router
