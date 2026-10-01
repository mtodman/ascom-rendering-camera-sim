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
    fallback_aperture_diameter_mm: float = 200.0
    use_telescope_focal_length: bool = True
    use_telescope_aperture: bool = True
    request_timeout_s: float = 2.0


class FocuserConfig(BaseModel):
    alpaca_base_url: str = "http://localhost:11114"
    device_number: int = 0
    # Focuser step position representing perfect focus - Alpaca's Position
    # property is just a raw step count with no defined zero, so this has
    # to be set explicitly (e.g. via the web setup page) to match wherever
    # "in focus" actually is for a given setup.
    in_focus_position: int = 0
    fallback_step_size_um: float = 5.0
    use_focuser_step_size: bool = True
    # If true, asks the focuser for its true drawtube position via the
    # "OpticalPosition" Alpaca Action (implemented by this project's
    # focuser_sim, which simulates backlash) and renders defocus from that
    # instead of the reported Position. Focusers that don't support the
    # action fall back to Position automatically.
    use_optical_position_action: bool = True
    request_timeout_s: float = 2.0


class FilterWheelConfig(BaseModel):
    alpaca_base_url: str = "http://localhost:11117"
    device_number: int = 0
    request_timeout_s: float = 2.0


class CoverCalibratorConfig(BaseModel):
    alpaca_base_url: str = "http://localhost:11116"
    device_number: int = 0
    # Used if the cover/calibrator is unreachable/not connected - "open, no
    # calibrator" means no effect at all, i.e. today's behavior.
    fallback_cover_closed: bool = False
    # Electrons/second/pixel the calibrator produces at Brightness == MaxBrightness;
    # scales linearly down with the device's reported Brightness fraction.
    # Tuned so a 1s exposure at max brightness uses ~75% of the default
    # camera's full_well_capacity_e (20000) - a strong, visible flat signal
    # without saturating outright; adjust to taste for other sensors/well depths.
    calibrator_e_per_s_at_max_brightness: float = 1.5e4
    request_timeout_s: float = 2.0


class CameraConfig(BaseModel):
    sensor_name: str = "Simulated Sensor"
    sensor_type: str = "Monochrome"
    pixel_size_um: float = 3.76
    num_pixels_x: int = 6248
    num_pixels_y: int = 4176
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
    # See config.yaml for why 3.5" rather than a sharper value.
    seeing_fwhm_arcsec: float = 3.5
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
    path: str = "data/tycho2_mag13_5.csv"
    limit_mag: float = 13.5


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    telescope: TelescopeConfig = TelescopeConfig()
    focuser: FocuserConfig = FocuserConfig()
    filter_wheel: FilterWheelConfig = FilterWheelConfig()
    cover_calibrator: CoverCalibratorConfig = CoverCalibratorConfig()
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
        save_sections_yaml(self, path, raw)


def save_sections_yaml(settings: BaseModel, path: Path, raw: CommentedMap | None = None) -> None:
    """Writes a sectioned settings model (each field itself a BaseModel,
    like `Settings`) to YAML, preserving `raw`'s comments/formatting for
    every field whose value isn't changing. Shared with the focuser
    simulator's own settings file.
    """
    if raw is None:
        raw = CommentedMap()
    for section_name in type(settings).model_fields:
        section_model: BaseModel = getattr(settings, section_name)
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
