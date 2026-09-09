"""Minimal Alpaca REST client used to query a filter wheel device's current
position and per-filter focus offset before rendering an exposure, so a
non-parfocal filter set's focus shift can be simulated.

ASCOM's Filter.FocusOffsets is a per-filter array of offsets in the same
step units as the focuser (confirmed against a real Alpaca FilterWheel
device, not just documentation) - added directly into the focuser's own
defocus calculation via FocuserClient.get_defocus_um(extra_offset_steps=...).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .alpaca_client_base import get_alpaca_property
from .config import FilterWheelConfig

logger = logging.getLogger("camera_sim.filter_wheel_client")


@dataclass
class FilterWheelState:
    position: int
    focus_offset_steps: int
    name: str
    source: str  # "filterwheel" or "fallback"


class FilterWheelClient:
    def __init__(self, cfg: FilterWheelConfig):
        self.cfg = cfg

    @property
    def _base(self) -> str:
        # Computed live (not cached) so config changes made through the web
        # setup UI (which mutate self.cfg in place) take effect immediately.
        return f"{self.cfg.alpaca_base_url.rstrip('/')}/api/v1/filterwheel/{self.cfg.device_number}"

    def _get(self, prop: str):
        return get_alpaca_property(self._base, prop, self.cfg.request_timeout_s)

    def _fallback_state(self) -> FilterWheelState:
        # No filter wheel configured/reachable (or it's mid-move, which
        # Alpaca reports as Position == -1): zero offset, i.e. no effect -
        # this is what makes the feature fully backward compatible for any
        # install that doesn't configure one.
        return FilterWheelState(position=-1, focus_offset_steps=0, name="", source="fallback")

    def get_state(self) -> FilterWheelState:
        connected = self._get("connected")
        if not connected:
            logger.info("filter wheel not connected/reachable; no focus offset")
            return self._fallback_state()

        position = self._get("position")
        if position is None or int(position) < 0:
            return self._fallback_state()
        position = int(position)

        offset_steps = 0
        offsets = self._get("focusoffsets")
        if isinstance(offsets, list) and 0 <= position < len(offsets):
            try:
                offset_steps = int(offsets[position])
            except (TypeError, ValueError):
                offset_steps = 0

        name = ""
        names = self._get("names")
        if isinstance(names, list) and 0 <= position < len(names):
            name = str(names[position])

        return FilterWheelState(position=position, focus_offset_steps=offset_steps, name=name, source="filterwheel")
