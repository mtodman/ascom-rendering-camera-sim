"""Alpaca Management API: /management/apiversions, /management/v1/description,
/management/v1/configureddevices.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from .alpaca_common import alpaca_response, client_transaction_id, get_params
from .config import Settings

DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "camera-sim.local/camera/0"))


def build_management_router(settings: Settings) -> APIRouter:
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
        value = [
            {
                "DeviceName": "Sky-Rendering Camera Simulator",
                "DeviceType": "Camera",
                "DeviceNumber": 0,
                "UniqueID": DEVICE_UUID,
            }
        ]
        return alpaca_response(value, client_transaction_id(params))

    return router
