"""Web UI for configuring and driving the focuser simulator, at the Alpaca
standard setup URLs (GET /setup, GET/POST /setup/v1/focuser/0/setup).

Besides the config form, the page shows a live view of the backlash model
(reported vs optical position, slack taken up) and a history of recent
moves with the steps each one lost to backlash, plus manual move/halt/reset
controls that work without an Alpaca client being connected.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ruamel.yaml.comments import CommentedMap

from camera_sim.config import update_model_in_place
from camera_sim.form_utils import describe_fields, parse_form_to_model

from .config import FocuserSimConfig, FocuserSimServerConfig, FocuserSimSettings
from .device import FocuserDevice

TEMPLATE_DIRS = [
    str(Path(__file__).resolve().parent / "templates"),
    str(Path(__file__).resolve().parent.parent / "camera_sim" / "templates"),  # base.html
]
templates = Jinja2Templates(directory=TEMPLATE_DIRS)

SETUP_BASE = "/setup/v1/focuser/{device_number}"


def build_setup_router(
    settings: FocuserSimSettings, config_path: Path, device: FocuserDevice, raw_config: CommentedMap
) -> APIRouter:
    router = APIRouter()

    def _sections():
        return [
            {
                "id": "focuser",
                "title": "Focuser & backlash",
                "desc": "Applied immediately, no restart needed. Backlash Steps = the drivetrain's slack: "
                "motor steps absorbed (drawtube doesn't move) whenever the focuser reverses direction, "
                "either way. Start Position/Initial Engaged Direction apply at startup and on Reset.",
                "fields": describe_fields(settings.focuser),
            },
            {
                "id": "server",
                "title": "Server / network",
                "desc": "Requires restarting the simulator process to take effect.",
                "fields": describe_fields(settings.server),
            },
        ]

    def _render(request: Request, saved: bool, errors: list[str], restart_needed: bool):
        return templates.TemplateResponse(
            request,
            "focuser_setup.html",
            {
                "server_name": settings.server.server_name,
                "sections": _sections(),
                "saved": saved,
                "errors": errors,
                "restart_needed": restart_needed,
            },
        )

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
                "description": "This server hosts a single simulated focuser device.",
                "devices": [
                    {
                        "name": "Backlash Focuser Simulator",
                        "device_type": "Focuser",
                        "device_number": 0,
                        "setup_url": "/setup/v1/focuser/0/setup",
                    }
                ],
            },
        )

    @router.get(SETUP_BASE + "/setup")
    async def setup_get(request: Request, device_number: int):
        return _render(request, saved=False, errors=[], restart_needed=False)

    @router.post(SETUP_BASE + "/setup")
    async def setup_post(request: Request, device_number: int):
        form = dict((await request.form()).multi_items())
        new_focuser, focuser_errors = parse_form_to_model(FocuserSimConfig, form, "focuser")
        new_server, server_errors = parse_form_to_model(FocuserSimServerConfig, form, "server")
        errors = focuser_errors + server_errors
        restart_needed = False
        if not errors:
            restart_needed = new_server.model_dump() != settings.server.model_dump()
            update_model_in_place(settings.focuser, new_focuser)
            update_model_in_place(settings.server, new_server)
            device.apply_config()
            settings.save(config_path, raw_config)
        return _render(request, saved=not errors, errors=errors, restart_needed=restart_needed)

    @router.get(SETUP_BASE + "/status")
    async def status(device_number: int):
        return JSONResponse({"status": device.status(), "history": device.history_rows()})

    @router.post(SETUP_BASE + "/control/move")
    async def control_move(device_number: int, position: int):
        device.move(position)
        return JSONResponse(device.status())

    @router.post(SETUP_BASE + "/control/halt")
    async def control_halt(device_number: int):
        device.halt()
        return JSONResponse(device.status())

    @router.post(SETUP_BASE + "/control/reset")
    async def control_reset(device_number: int, direction: str = ""):
        device.reset_backlash(direction if direction in ("in", "out") else "")
        return JSONResponse(device.status())

    return router
