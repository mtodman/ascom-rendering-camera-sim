"""Minimal Alpaca REST client used to query a cover/calibrator device's
current state before rendering an exposure, so a closed cover (no stars)
and/or an active calibrator (uniform flat-field illumination) can be
simulated.

ASCOM CoverStatus / CalibratorStatus ordinals (confirmed against a real
Alpaca CoverCalibrator device, not just documentation): CoverStatus
Closed=1, Moving=2, Open=3; CalibratorStatus Off=1, NotReady=2, Ready=3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .alpaca_client_base import get_alpaca_property
from .config import CoverCalibratorConfig

logger = logging.getLogger("camera_sim.cover_calibrator_client")

COVER_STATUS_CLOSED = 1
CALIBRATOR_STATUS_READY = 3


@dataclass
class CoverCalibratorState:
    cover_closed: bool
    calibrator_e_per_s: float
    source: str  # "covercalibrator" or "fallback"


class CoverCalibratorClient:
    def __init__(self, cfg: CoverCalibratorConfig):
        self.cfg = cfg

    @property
    def _base(self) -> str:
        # Computed live (not cached) so config changes made through the web
        # setup UI (which mutate self.cfg in place) take effect immediately.
        return f"{self.cfg.alpaca_base_url.rstrip('/')}/api/v1/covercalibrator/{self.cfg.device_number}"

    def _get(self, prop: str) -> float | bool | None:
        return get_alpaca_property(self._base, prop, self.cfg.request_timeout_s)

    def _fallback_state(self) -> CoverCalibratorState:
        # No cover/calibrator configured/reachable: report the cover as open
        # and the calibrator as off - i.e. no effect at all, which is what
        # makes this feature fully backward compatible for any install that
        # doesn't configure one.
        return CoverCalibratorState(
            cover_closed=self.cfg.fallback_cover_closed,
            calibrator_e_per_s=0.0,
            source="fallback",
        )

    def get_state(self) -> CoverCalibratorState:
        """Single source of truth for cover/calibrator arithmetic - used by
        both the exposure renderer and the setup page's status display.
        """
        connected = self._get("connected")
        if not connected:
            logger.info("cover/calibrator not connected/reachable; assuming open, calibrator off")
            return self._fallback_state()

        cover_state = self._get("coverstate")
        cover_closed = isinstance(cover_state, (int, float)) and int(cover_state) == COVER_STATUS_CLOSED

        calibrator_e_per_s = 0.0
        calibrator_state = self._get("calibratorstate")
        if isinstance(calibrator_state, (int, float)) and int(calibrator_state) == CALIBRATOR_STATUS_READY:
            brightness = self._get("brightness")
            max_brightness = self._get("maxbrightness")
            if (
                isinstance(brightness, (int, float))
                and isinstance(max_brightness, (int, float))
                and max_brightness > 0
            ):
                fraction = max(0.0, min(1.0, float(brightness) / float(max_brightness)))
                calibrator_e_per_s = fraction * self.cfg.calibrator_e_per_s_at_max_brightness

        return CoverCalibratorState(cover_closed=cover_closed, calibrator_e_per_s=calibrator_e_per_s, source="covercalibrator")
