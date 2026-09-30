"""Minimal Alpaca REST client used to query a focuser device's current
position (and step size, if it exposes one) before rendering an exposure,
so defocus can be simulated.

If the focuser supports the "OpticalPosition" Action (this project's
focuser_sim does), the true drawtube position is used instead of the
reported Position - that's what makes simulated backlash visible in the
rendered frames: after a direction reversal the reported Position changes
but the optics (and so the star sizes) don't, until the slack is taken up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .alpaca_client_base import get_alpaca_property, put_alpaca_action
from .config import FocuserConfig

logger = logging.getLogger("camera_sim.focuser_client")

OPTICAL_POSITION_ACTION = "OpticalPosition"


@dataclass
class FocuserState:
    # Position used for defocus: the optical (drawtube) position when the
    # focuser reports one, otherwise the same as reported_position.
    position: int
    step_size_um: float
    source: str  # "focuser" or "fallback"
    # The focuser's standard Alpaca Position property (motor steps).
    reported_position: int = 0
    # True if `position` came from the OpticalPosition action.
    optical: bool = False


class FocuserClient:
    def __init__(self, cfg: FocuserConfig):
        self.cfg = cfg

    @property
    def _base(self) -> str:
        # Computed live (not cached) so config changes made through the web
        # setup UI (which mutate self.cfg in place) take effect immediately.
        return f"{self.cfg.alpaca_base_url.rstrip('/')}/api/v1/focuser/{self.cfg.device_number}"

    def _get(self, prop: str) -> float | bool | None:
        return get_alpaca_property(self._base, prop, self.cfg.request_timeout_s)

    def _get_optical_position(self) -> int | None:
        if not self.cfg.use_optical_position_action:
            return None
        raw = put_alpaca_action(self._base, OPTICAL_POSITION_ACTION, "", self.cfg.request_timeout_s)
        if raw is None:
            return None
        try:
            return int(float(raw))
        except ValueError:
            logger.warning("focuser returned non-numeric %s: %r", OPTICAL_POSITION_ACTION, raw)
            return None

    def _fallback_state(self) -> FocuserState:
        # No focuser configured/reachable: report the focuser as sitting
        # exactly at the configured in-focus position, i.e. zero defocus -
        # this is what makes the feature fully backward compatible for any
        # install that doesn't configure a focuser.
        return FocuserState(
            position=self.cfg.in_focus_position,
            step_size_um=self.cfg.fallback_step_size_um,
            source="fallback",
            reported_position=self.cfg.in_focus_position,
        )

    def get_state(self) -> FocuserState:
        connected = self._get("connected")
        if not connected:
            logger.info("focuser not connected/reachable; assuming in focus")
            return self._fallback_state()

        position = self._get("position")
        if position is None:
            return self._fallback_state()

        step_size = self.cfg.fallback_step_size_um
        if self.cfg.use_focuser_step_size:
            ss = self._get("stepsize")
            # ASCOM's Focuser.StepSize is already in microns - no unit
            # conversion needed here (unlike Telescope.FocalLength/
            # ApertureDiameter, which are in meters).
            if isinstance(ss, (int, float)) and ss > 0:
                step_size = float(ss)

        optical = self._get_optical_position()
        return FocuserState(
            position=int(position) if optical is None else optical,
            step_size_um=step_size,
            source="focuser",
            reported_position=int(position),
            optical=optical is not None,
        )

    def defocus_um_for(self, state: FocuserState, extra_offset_steps: int = 0) -> float:
        """Defocus in microns at the focal plane for an already-fetched
        state. `extra_offset_steps` adds an additional offset (in the same
        step units as the focuser) before converting to microns - used to
        fold a filter wheel's per-filter FocusOffsets into the calculation.
        """
        return (state.position - self.cfg.in_focus_position + extra_offset_steps) * state.step_size_um

    def get_defocus_um(self, extra_offset_steps: int = 0) -> tuple[float, str]:
        """Returns (defocus in microns at the focal plane, source), where
        source is "focuser" if this came from a live query or "fallback" if
        the configured focuser was unreachable/disconnected (defocus 0 in
        that case).
        """
        state = self.get_state()
        return self.defocus_um_for(state, extra_offset_steps), state.source
