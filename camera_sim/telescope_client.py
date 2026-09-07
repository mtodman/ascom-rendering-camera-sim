"""Minimal Alpaca REST client used to query a telescope device's current
pointing (and focal length, if it exposes one) before rendering an exposure.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import requests

from .config import TelescopeConfig

logger = logging.getLogger("camera_sim.telescope_client")

_client_id = 1234
_transaction_counter = itertools.count(1)


@dataclass
class Pointing:
    ra_hours: float
    dec_deg: float
    focal_length_mm: float
    source: str  # "telescope" or "fallback"


class TelescopeClient:
    def __init__(self, cfg: TelescopeConfig):
        self.cfg = cfg

    @property
    def _base(self) -> str:
        # Computed live (not cached) so config changes made through the web
        # setup UI (which mutate self.cfg in place) take effect immediately.
        return f"{self.cfg.alpaca_base_url.rstrip('/')}/api/v1/telescope/{self.cfg.device_number}"

    def _get(self, prop: str) -> float | bool | None:
        params = {
            "ClientID": _client_id,
            "ClientTransactionID": next(_transaction_counter),
        }
        try:
            resp = requests.get(
                f"{self._base}/{prop}", params=params, timeout=self.cfg.request_timeout_s
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("telescope query %s failed: %s", prop, exc)
            return None
        if body.get("ErrorNumber", 0) != 0:
            logger.warning("telescope query %s returned error: %s", prop, body.get("ErrorMessage"))
            return None
        return body.get("Value")

    def get_pointing(self) -> Pointing:
        connected = self._get("connected")
        if not connected:
            logger.info("telescope not connected/reachable; using fallback pointing")
            return Pointing(
                ra_hours=self.cfg.fallback_ra_hours,
                dec_deg=self.cfg.fallback_dec_deg,
                focal_length_mm=self.cfg.fallback_focal_length_mm,
                source="fallback",
            )

        ra = self._get("rightascension")
        dec = self._get("declination")
        if ra is None or dec is None:
            return Pointing(
                ra_hours=self.cfg.fallback_ra_hours,
                dec_deg=self.cfg.fallback_dec_deg,
                focal_length_mm=self.cfg.fallback_focal_length_mm,
                source="fallback",
            )

        focal_length = self.cfg.fallback_focal_length_mm
        if self.cfg.use_telescope_focal_length:
            fl = self._get("focallength")
            if isinstance(fl, (int, float)) and fl > 0:
                # ASCOM's ITelescope.FocalLength is specified in meters, not
                # millimeters (a well-known gotcha) - convert here so a real,
                # spec-compliant telescope driver reports correctly. Without
                # this, a 400mm scope reporting 0.4 would be read as a
                # 0.4mm focal length: a 1000x plate-scale error that crams
                # the whole visible sky into a handful of pixels and
                # saturates them into a single blob.
                focal_length = float(fl) * 1000.0

        return Pointing(
            ra_hours=float(ra),
            dec_deg=float(dec),
            focal_length_mm=focal_length,
            source="telescope",
        )
