"""Loads and validates focuser_sim.yaml into typed settings objects."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from ruamel.yaml.comments import CommentedMap

from camera_sim.config import REPO_ROOT, ServerConfig, _yaml_rt, save_sections_yaml


class FocuserSimServerConfig(ServerConfig):
    # 11111-11117 are used by the camera simulator and its test stubs.
    port: int = 11118
    server_name: str = "Alpaca Backlash Focuser Simulator"


# Pre-single-gap config keys, migrated by FocuserSimConfig on load and
# dropped from the YAML file on the next save.
LEGACY_BACKLASH_KEYS = ("backlash_in_steps", "backlash_out_steps")


class FocuserSimConfig(BaseModel):
    # Size of the drivetrain's slack: the motor steps absorbed (drawtube stays
    # put) whenever the focuser reverses direction, in either direction.
    backlash_steps: int = Field(default=0, ge=0)
    # Direction the drivetrain is engaged in at startup/reset, i.e. the
    # direction the drawtube is assumed to have last moved.
    initial_engaged_direction: Literal["in", "out"] = "out"
    start_position: int = Field(default=25000, ge=0)
    max_step: int = Field(default=50000, ge=1)
    max_increment: int = Field(default=50000, ge=1)
    # Microns per step, reported as StepSize.
    step_size_um: float = Field(default=5.0, gt=0)
    # Motor speed. 0 makes every move complete instantly.
    steps_per_second: float = Field(default=500.0, ge=0)
    temperature_c: float = 10.0

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_backlash(cls, data):
        # Older configs had separate IN/OUT amounts; one physical gap can't
        # lose different amounts each way (see backlash.py), so the larger
        # of the two becomes the gap.
        if isinstance(data, dict) and "backlash_steps" not in data:
            legacy = [data[k] for k in LEGACY_BACKLASH_KEYS if data.get(k) is not None]
            if legacy:
                data = {k: v for k, v in data.items() if k not in LEGACY_BACKLASH_KEYS}
                data["backlash_steps"] = max(int(v) for v in legacy)
        return data


class FocuserSimSettings(BaseModel):
    server: FocuserSimServerConfig = FocuserSimServerConfig()
    focuser: FocuserSimConfig = FocuserSimConfig()

    def save(self, path: Path, raw: CommentedMap | None = None) -> None:
        focuser_raw = raw.get("focuser") if raw is not None else None
        if focuser_raw is not None:
            for key in LEGACY_BACKLASH_KEYS:
                focuser_raw.pop(key, None)
        save_sections_yaml(self, path, raw)


def resolve_config_path(path: str | None = None) -> Path:
    return Path(path or os.environ.get("FOCUSER_SIM_CONFIG", REPO_ROOT / "focuser_sim.yaml"))


def load_settings_with_raw(path: str | Path | None = None) -> tuple[FocuserSimSettings, CommentedMap]:
    config_path = resolve_config_path(str(path) if path else None)
    if not config_path.exists():
        return FocuserSimSettings(), CommentedMap()
    with open(config_path) as f:
        raw = _yaml_rt.load(f)
    if raw is None:
        raw = CommentedMap()
    return FocuserSimSettings(**raw), raw
