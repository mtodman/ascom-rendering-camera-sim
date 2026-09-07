"""CameraDevice state machine and the FastAPI router implementing the full
ASCOM ICameraV4 (Platform 7) surface over Alpaca REST.
"""
from __future__ import annotations

import asyncio
import datetime
import enum
import logging

import numpy as np
from fastapi import APIRouter, Request

from . import image_synth
from .alpaca_common import (
    CommonDeviceState,
    alpaca_error,
    alpaca_response,
    build_common_router,
    client_transaction_id,
    find_param,
    get_params,
    put_params,
)
from .alpaca_errors import (
    InvalidOperationException,
    InvalidValueException,
    NotConnectedException,
    NotImplementedException,
)
from .config import CameraConfig, TelescopeConfig
from .sky_model import StarCatalog, project_stars
from .telescope_client import TelescopeClient

logger = logging.getLogger("camera_sim.camera")


class CameraState(enum.IntEnum):
    IDLE = 0
    WAITING = 1
    EXPOSING = 2
    READING = 3
    DOWNLOAD = 4
    ERROR = 5


class CameraDevice:
    def __init__(self, cam_cfg: CameraConfig, tel_cfg: TelescopeConfig, catalog: StarCatalog):
        self.cfg = cam_cfg
        self.catalog = catalog
        self.telescope = TelescopeClient(tel_cfg)
        self.rng = np.random.default_rng()

        self.common = CommonDeviceState(
            name="ASCOM Alpaca Sky-Rendering Camera Simulator",
            description="Camera simulator that renders the real sky at the connected telescope's pointing.",
            driver_info="camera_sim v1.0 (Python/FastAPI)",
            driver_version="1.0",
            interface_version=4,
            supported_actions=[],
        )

        # Frame geometry.
        self.bin_x = 1
        self.bin_y = 1
        self.start_x = 0
        self.start_y = 0
        self.num_x = cam_cfg.num_pixels_x
        self.num_y = cam_cfg.num_pixels_y

        # Controls.
        self.gain = cam_cfg.gain_default
        self.offset = cam_cfg.offset_default
        self.fast_readout = False
        self.readout_mode = 0
        self.readout_modes = ["Normal", "Fast"]
        self.cooler_on = False
        self.ccd_temperature = cam_cfg.ambient_temp_c
        self.ccd_set_point = cam_cfg.ambient_temp_c
        self.is_pulse_guiding = False

        # Exposure state.
        self.camera_state = CameraState.IDLE
        self.image_ready = False
        self.image_array: np.ndarray | None = None
        self.last_exposure_duration = 0.0
        self.last_exposure_start_time: str | None = None
        self.percent_completed = 0
        self._exposure_task: asyncio.Task | None = None
        self._cooling_task: asyncio.Task | None = None

    # ---- capability/limits helpers -------------------------------------------------
    @property
    def max_bin_x(self) -> int:
        return self.cfg.max_bin

    @property
    def max_bin_y(self) -> int:
        return self.cfg.max_bin

    def ensure_cooling_task(self) -> None:
        if self._cooling_task is None or self._cooling_task.done():
            self._cooling_task = asyncio.create_task(self._cooling_loop())

    async def _cooling_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            target = self.ccd_set_point if self.cooler_on else self.cfg.ambient_temp_c
            delta = target - self.ccd_temperature
            step = self.cfg.cooling_rate_c_per_s
            if abs(delta) <= step:
                self.ccd_temperature = target
            else:
                self.ccd_temperature += step if delta > 0 else -step
            min_temp = self.cfg.ambient_temp_c - self.cfg.max_cooldown_delta_c
            self.ccd_temperature = max(self.ccd_temperature, min_temp)

    @property
    def cooler_power(self) -> float:
        if not self.cooler_on:
            return 0.0
        delta = self.cfg.ambient_temp_c - self.ccd_temperature
        return float(np.clip(100.0 * delta / max(self.cfg.max_cooldown_delta_c, 1e-6), 0, 100))

    @property
    def heat_sink_temperature(self) -> float:
        return self.cfg.ambient_temp_c

    # ---- exposure --------------------------------------------------------------
    async def start_exposure(self, duration_s: float, light: bool) -> None:
        if self.camera_state in (CameraState.EXPOSING, CameraState.READING, CameraState.DOWNLOAD):
            raise InvalidOperationException("An exposure is already in progress.")
        if self.start_x + self.num_x > self.cfg.num_pixels_x // self.bin_x:
            raise InvalidValueException("StartX + NumX exceeds sensor width for current binning.")
        if self.start_y + self.num_y > self.cfg.num_pixels_y // self.bin_y:
            raise InvalidValueException("StartY + NumY exceeds sensor height for current binning.")

        self.image_ready = False
        self.percent_completed = 0
        self.last_exposure_duration = duration_s
        self.last_exposure_start_time = (
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        )
        self.camera_state = CameraState.EXPOSING
        self.ensure_cooling_task()

        if self._exposure_task and not self._exposure_task.done():
            self._exposure_task.cancel()
        self._exposure_task = asyncio.create_task(self._run_exposure(duration_s, light))

    async def _run_exposure(self, duration_s: float, light: bool) -> None:
        try:
            elapsed = 0.0
            step = 0.1
            while elapsed < duration_s:
                await asyncio.sleep(min(step, duration_s - elapsed))
                elapsed += step
                self.percent_completed = int(min(99, 100 * elapsed / max(duration_s, 1e-9)))

            self.camera_state = CameraState.READING

            pointing = await asyncio.to_thread(self.telescope.get_pointing)
            center_ra_deg = pointing.ra_hours * 15.0

            if light:
                stars = project_stars(
                    self.catalog,
                    center_ra_deg=center_ra_deg,
                    center_dec_deg=pointing.dec_deg,
                    focal_length_mm=pointing.focal_length_mm,
                    pixel_size_um=self.cfg.pixel_size_um,
                    num_pixels_x=self.cfg.num_pixels_x,
                    num_pixels_y=self.cfg.num_pixels_y,
                    rotation_deg=self.cfg.rotation_deg,
                    flip_x=self.cfg.flip_x,
                    flip_y=self.cfg.flip_y,
                )
            else:
                from .sky_model import ProjectedStars

                stars = ProjectedStars(np.array([]), np.array([]), np.array([]))

            full_frame_e = await asyncio.to_thread(
                image_synth.render_frame,
                stars,
                self.cfg,
                duration_s,
                light,
                self.gain,
                pointing.focal_length_mm,
                self.ccd_temperature,
                self.rng,
            )
            region_e = image_synth.subframe_and_bin(
                full_frame_e, self.start_x, self.start_y, self.num_x, self.num_y, self.bin_x, self.bin_y
            )
            adu = image_synth.to_adu(region_e, self.cfg, bias_adu=self.offset + self.cfg.bias_level_adu)

            self.image_array = adu
            self.percent_completed = 100
            self.camera_state = CameraState.IDLE
            self.image_ready = True
            logger.info(
                "exposure done: %.3fs light=%s pointing=(%.4fh,%.4f deg,%s) stars_in_frame=%d",
                duration_s, light, pointing.ra_hours, pointing.dec_deg, pointing.source, len(stars.vmag),
            )
        except asyncio.CancelledError:
            self.camera_state = CameraState.IDLE
            raise
        except Exception:  # noqa: BLE001
            logger.exception("exposure failed")
            self.camera_state = CameraState.ERROR

    def abort_exposure(self) -> None:
        if self._exposure_task and not self._exposure_task.done():
            self._exposure_task.cancel()
        self.camera_state = CameraState.IDLE
        self.image_ready = False
        self.percent_completed = 0

    def stop_exposure(self) -> None:
        if self._exposure_task and not self._exposure_task.done():
            self._exposure_task.cancel()
        self.camera_state = CameraState.IDLE
        self.percent_completed = 100


def build_camera_router(device_number: int, device: CameraDevice) -> APIRouter:
    prefix = "/api/v1/camera"
    router = build_common_router(prefix, device_number, device.common)
    # Camera-specific routes are added directly onto `router`, which already
    # carries the full "/api/v1/camera/{device_number}" prefix from
    # build_common_router — a second prefixed router included into this one
    # would double the path segment.
    cam_router = router
    cfg = device.cfg

    def _ok(value, params):
        return alpaca_response(value, client_transaction_id(params))

    def _err(exc, params):
        return alpaca_error(exc, client_transaction_id(params))

    def _require_connected(params):
        if not device.common.connected:
            raise NotConnectedException("Camera is not connected.")

    def _not_implemented(message: str):
        def _raise():
            raise NotImplementedException(message)

        return _raise

    # ---- simple read-only properties -------------------------------------------
    simple_getters = {
        "bayeroffsetx": lambda: 0,
        "bayeroffsety": lambda: 0,
        "camerastate": lambda: int(device.camera_state),
        "cameraxsize": lambda: cfg.num_pixels_x,
        "cameraysize": lambda: cfg.num_pixels_y,
        "canabortexposure": lambda: True,
        "canasymmetricbin": lambda: cfg.can_asymmetric_bin,
        "canfastreadout": lambda: cfg.can_fast_readout,
        "cangetcoolerpower": lambda: True,
        "canpulseguide": lambda: cfg.can_pulse_guide,
        "cansetccdtemperature": lambda: True,
        "canstopexposure": lambda: True,
        "electronsperadu": lambda: cfg.electrons_per_adu,
        "exposuremax": lambda: cfg.exposure_max_s,
        "exposuremin": lambda: cfg.exposure_min_s,
        "exposureresolution": lambda: cfg.exposure_resolution_s,
        "fullwellcapacity": lambda: cfg.full_well_capacity_e,
        "gainmax": lambda: cfg.gain_max,
        "gainmin": lambda: cfg.gain_min,
        "gains": _not_implemented("Camera uses GainMin/GainMax range mode, not a Gains list."),
        "hasshutter": lambda: True,
        "heatsinktemperature": lambda: device.heat_sink_temperature,
        "imageready": lambda: device.image_ready,
        "ispulseguiding": lambda: device.is_pulse_guiding,
        "lastexposureduration": lambda: device.last_exposure_duration,
        "lastexposurestarttime": lambda: device.last_exposure_start_time or "",
        "maxadu": lambda: cfg.max_adu,
        "maxbinx": lambda: device.max_bin_x,
        "maxbiny": lambda: device.max_bin_y,
        "offsetmax": lambda: cfg.offset_max,
        "offsetmin": lambda: cfg.offset_min,
        "offsets": _not_implemented("Camera uses OffsetMin/OffsetMax range mode, not an Offsets list."),
        "percentcompleted": lambda: device.percent_completed,
        "readoutmodes": lambda: device.readout_modes,
        "sensorname": lambda: cfg.sensor_name,
        "sensortype": lambda: 0 if cfg.sensor_type.lower() == "monochrome" else 2,
        "coolerpower": lambda: device.cooler_power,
        "pixelsizex": lambda: cfg.pixel_size_um,
        "pixelsizey": lambda: cfg.pixel_size_um,
        "subexposureduration": lambda: device.last_exposure_duration,
    }

    def make_getter(name, fn):
        @cam_router.get(f"/{name}")
        async def _getter(request: Request):
            params = await get_params(request)
            try:
                _require_connected(params)
                return _ok(fn(), params)
            except Exception as exc:  # noqa: BLE001
                return _err(exc, params)

        _getter.__name__ = f"get_{name}"

    for name, fn in simple_getters.items():
        make_getter(name, fn)

    # ---- read/write properties ---------------------------------------------------
    @cam_router.get("/binx")
    async def get_binx(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            return _ok(device.bin_x, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/binx")
    async def put_binx(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            raw = find_param(params, "BinX")
            val = int(raw)
            if val < 1 or val > device.max_bin_x:
                raise InvalidValueException(f"BinX {val} out of range [1,{device.max_bin_x}]")
            device.bin_x = val
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.get("/biny")
    async def get_biny(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            return _ok(device.bin_y, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/biny")
    async def put_biny(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            raw = find_param(params, "BinY")
            val = int(raw)
            if val < 1 or val > device.max_bin_y:
                raise InvalidValueException(f"BinY {val} out of range [1,{device.max_bin_y}]")
            device.bin_y = val
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    def rw_int_property(name: str, attr: str, min_val_fn, max_val_fn=None, extra_validate=None):
        @cam_router.get(f"/{name}")
        async def _get(request: Request):
            params = await get_params(request)
            try:
                _require_connected(params)
                return _ok(getattr(device, attr), params)
            except Exception as exc:  # noqa: BLE001
                return _err(exc, params)

        @cam_router.put(f"/{name}")
        async def _put(request: Request):
            params = await put_params(request)
            try:
                _require_connected(params)
                raw = find_param(params, name)
                if raw is None:
                    raise InvalidValueException(f"{name} parameter is required.")
                val = int(raw)
                if val < min_val_fn():
                    raise InvalidValueException(f"{name} {val} below minimum {min_val_fn()}")
                if max_val_fn is not None and val > max_val_fn():
                    raise InvalidValueException(f"{name} {val} above maximum {max_val_fn()}")
                if extra_validate:
                    extra_validate(val)
                setattr(device, attr, val)
                return _ok(None, params)
            except Exception as exc:  # noqa: BLE001
                return _err(exc, params)

        _get.__name__ = f"get_{name}"
        _put.__name__ = f"put_{name}"

    rw_int_property("startx", "start_x", lambda: 0)
    rw_int_property("starty", "start_y", lambda: 0)
    rw_int_property("numx", "num_x", lambda: 1)
    rw_int_property("numy", "num_y", lambda: 1)
    rw_int_property("gain", "gain", lambda: cfg.gain_min, lambda: cfg.gain_max)
    rw_int_property("offset", "offset", lambda: cfg.offset_min, lambda: cfg.offset_max)
    rw_int_property("readoutmode", "readout_mode", lambda: 0, lambda: len(device.readout_modes) - 1)

    @cam_router.get("/fastreadout")
    async def get_fastreadout(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            if not cfg.can_fast_readout:
                raise NotImplementedException("FastReadout not supported.")
            return _ok(device.fast_readout, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/fastreadout")
    async def put_fastreadout(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            if not cfg.can_fast_readout:
                raise NotImplementedException("FastReadout not supported.")
            raw = find_param(params, "FastReadout")
            device.fast_readout = str(raw).lower() in ("true", "1")
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.get("/ccdtemperature")
    async def get_ccdtemperature(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            return _ok(round(device.ccd_temperature, 2), params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.get("/setccdtemperature")
    async def get_setccdtemperature(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            return _ok(round(device.ccd_set_point, 2), params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/setccdtemperature")
    async def put_setccdtemperature(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            raw = find_param(params, "SetCCDTemperature")
            device.ccd_set_point = float(raw)
            device.ensure_cooling_task()
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.get("/cooleron")
    async def get_cooleron(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            return _ok(device.cooler_on, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/cooleron")
    async def put_cooleron(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            raw = find_param(params, "CoolerOn")
            device.cooler_on = str(raw).lower() in ("true", "1")
            device.ensure_cooling_task()
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    # ---- image data ---------------------------------------------------------
    @cam_router.get("/imagearray")
    async def get_imagearray(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            if not device.image_ready or device.image_array is None:
                raise InvalidOperationException("No image available; call StartExposure first.")
            # Value is [x][y] per the Alpaca convention (transpose of our [row=y][col=x] array).
            value = device.image_array.T.tolist()
            return alpaca_response(
                value,
                client_transaction_id(params),
                extra_fields={"Type": 2, "Rank": 2},
            )
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.get("/imagearrayvariant")
    async def get_imagearrayvariant(request: Request):
        params = await get_params(request)
        try:
            _require_connected(params)
            if not device.image_ready or device.image_array is None:
                raise InvalidOperationException("No image available; call StartExposure first.")
            value = device.image_array.T.tolist()
            return alpaca_response(
                value,
                client_transaction_id(params),
                extra_fields={"Type": 2, "Rank": 2},
            )
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    # ---- exposure control -----------------------------------------------------
    @cam_router.put("/startexposure")
    async def put_startexposure(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            duration_raw = find_param(params, "Duration")
            light_raw = find_param(params, "Light")
            if duration_raw is None:
                raise InvalidValueException("Duration parameter is required.")
            duration = float(duration_raw)
            if duration < cfg.exposure_min_s or duration > cfg.exposure_max_s:
                raise InvalidValueException(
                    f"Duration {duration} out of range [{cfg.exposure_min_s},{cfg.exposure_max_s}]"
                )
            light = True if light_raw is None else str(light_raw).lower() in ("true", "1")
            await device.start_exposure(duration, light)
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/abortexposure")
    async def put_abortexposure(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            device.abort_exposure()
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/stopexposure")
    async def put_stopexposure(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            device.stop_exposure()
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    @cam_router.put("/pulseguide")
    async def put_pulseguide(request: Request):
        params = await put_params(request)
        try:
            _require_connected(params)
            if not cfg.can_pulse_guide:
                raise NotImplementedException("PulseGuide not supported.")
            duration_raw = find_param(params, "Duration")
            duration_ms = float(duration_raw) if duration_raw is not None else 0.0

            async def _guide():
                device.is_pulse_guiding = True
                await asyncio.sleep(duration_ms / 1000.0)
                device.is_pulse_guiding = False

            asyncio.create_task(_guide())
            return _ok(None, params)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, params)

    return router
