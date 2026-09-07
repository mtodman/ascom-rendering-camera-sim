"""Loads and validates config.yaml into typed settings objects."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

REPO_ROOT = Path(__file__).resolve().parent.parent

# Round-trip mode preserves comments, key order, and scalar formatting
# (e.g. "5.0e7") for anything the web setup UI doesn't actually change.
_yaml_rt = YAML()
_yaml_rt.preserve_quotes = True
_yaml_rt.indent(mapping=2, sequence=4, offset=2)

_UNSET = object()


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 11115
    discovery_port: int = 32227
    server_name: str = "Alpaca Camera Simulator"
    manufacturer: str = "DIY"
    manufacturer_version: str = "1.0"
    location: str = "Simulated Observatory"


class TelescopeConfig(BaseModel):
    alpaca_base_url: str = "http://localhost:11111"
    device_number: int = 0
    fallback_ra_hours: float = 0.0
    fallback_dec_deg: float = 90.0
    fallback_focal_length_mm: float = 800.0
    use_telescope_focal_length: bool = True
    request_timeout_s: float = 2.0


class CameraConfig(BaseModel):
    sensor_name: str = "Simulated Sensor"
    sensor_type: str = "Monochrome"
    pixel_size_um: float = 3.76
    num_pixels_x: int = 1920
    num_pixels_y: int = 1080
    max_bin: int = 4
    can_asymmetric_bin: bool = True
    can_fast_readout: bool = True
    can_pulse_guide: bool = True
    full_well_capacity_e: float = 20000.0
    max_adu: int = 65535
    read_noise_e: float = 2.5
    dark_current_e_per_s_per_px: float = 0.01
    bias_level_adu: int = 100
    electrons_per_adu: float = 1.0
    gain_min: int = 0
    gain_max: int = 100
    gain_default: int = 0
    offset_min: int = 0
    offset_max: int = 100
    offset_default: int = 0
    seeing_fwhm_arcsec: float = 2.5
    zero_point_e_per_s_mag0: float = 5.0e7
    rotation_deg: float = 0.0
    flip_x: bool = False
    flip_y: bool = False
    exposure_min_s: float = 0.001
    exposure_max_s: float = 3600.0
    exposure_resolution_s: float = 0.001
    ambient_temp_c: float = 20.0
    max_cooldown_delta_c: float = 40.0
    cooling_rate_c_per_s: float = 2.0


class CatalogConfig(BaseModel):
    path: str = "data/tycho2_mag9.csv"
    limit_mag: float = 9.0


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    telescope: TelescopeConfig = TelescopeConfig()
    camera: CameraConfig = CameraConfig()
    catalog: CatalogConfig = CatalogConfig()

    def catalog_path(self) -> Path:
        p = Path(self.catalog.path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p

    def save(self, path: Path, raw: CommentedMap | None = None) -> None:
        """Writes this Settings object back to a YAML file, e.g. after the
        web setup UI applies an edit, so changes survive a restart.

        `raw` is the CommentedMap originally loaded from this same file (see
        `load_settings_with_raw`), if available. Passing it preserves that
        file's comments and scalar formatting for every field whose value
        isn't actually changing; without it, this falls back to a fresh
        (comment-free) dump.
        """
        if raw is None:
            raw = CommentedMap()
        for section_name in self.__class__.model_fields:
            section_model: BaseModel = getattr(self, section_name)
            section_raw = raw.get(section_name)
            if not isinstance(section_raw, CommentedMap):
                section_raw = CommentedMap()
                raw[section_name] = section_raw
            for field_name, value in section_model.model_dump().items():
                if section_raw.get(field_name, _UNSET) != value:
                    section_raw[field_name] = value
        with open(path, "w") as f:
            _yaml_rt.dump(raw, f)


def resolve_config_path(path: str | None = None) -> Path:
    return Path(path or os.environ.get("CAMERA_SIM_CONFIG", REPO_ROOT / "config.yaml"))


def load_settings_with_raw(path: str | None = None) -> tuple[Settings, CommentedMap]:
    config_path = resolve_config_path(path)
    with open(config_path) as f:
        raw = _yaml_rt.load(f)
    if raw is None:
        raw = CommentedMap()
    return Settings(**raw), raw


def load_raw_or_empty(path: str | Path | None = None) -> CommentedMap:
    """Loads the raw CommentedMap for `path` if it exists, else an empty one
    (e.g. for a Settings object that wasn't loaded from disk, such as in
    tests). Used so `Settings.save` can still preserve comments/formatting
    when the caller already has a Settings instance but not its raw form.
    """
    config_path = resolve_config_path(str(path) if path else None)
    if not config_path.exists():
        return CommentedMap()
    with open(config_path) as f:
        raw = _yaml_rt.load(f)
    return raw if raw is not None else CommentedMap()


def load_settings(path: str | None = None) -> Settings:
    settings, _raw = load_settings_with_raw(path)
    return settings


def update_model_in_place(target: BaseModel, new: BaseModel) -> None:
    """Copies every field from `new` onto `target`, keeping `target`'s object
    identity. Used so that config changes applied via the web setup UI are
    immediately visible to code (e.g. CameraDevice, camera.py's route
    closures) that already holds a reference to the original object.
    """
    for name in type(new).model_fields:
        setattr(target, name, getattr(new, name))
