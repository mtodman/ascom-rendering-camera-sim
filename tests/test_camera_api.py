import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from camera_sim.config import CameraConfig, CatalogConfig, ServerConfig, Settings, TelescopeConfig
from camera_sim.server import create_app

BASE = "/api/v1/camera/0"


def make_settings(tmp_catalog: Path) -> Settings:
    tmp_catalog.write_text("ra_deg,dec_deg,vmag,bv\n0.0,0.0,5.0,0.5\n0.01,0.0,7.0,0.6\n")
    return Settings(
        server=ServerConfig(port=0, discovery_port=0),
        telescope=TelescopeConfig(alpaca_base_url="http://127.0.0.1:1", fallback_ra_hours=0.0, fallback_dec_deg=0.0),
        camera=CameraConfig(num_pixels_x=200, num_pixels_y=150, pixel_size_um=3.76),
        catalog=CatalogConfig(path=str(tmp_catalog)),
    )


def test_not_connected_error(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get(f"{BASE}/camerastate")
        body = resp.json()
        assert body["ErrorNumber"] == 0x407


def test_full_exposure_lifecycle(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.put(f"{BASE}/connected", data={"Connected": "true"})
        assert r.json()["ErrorNumber"] == 0

        r = client.get(f"{BASE}/cameraxsize")
        assert r.json()["Value"] == 200
        r = client.get(f"{BASE}/cameraysize")
        assert r.json()["Value"] == 150

        r = client.put(f"{BASE}/startexposure", data={"Duration": "0.2", "Light": "true"})
        assert r.json()["ErrorNumber"] == 0

        for _ in range(50):
            ready = client.get(f"{BASE}/imageready").json()["Value"]
            if ready:
                break
            time.sleep(0.05)
        assert ready

        r = client.get(f"{BASE}/imagearray")
        body = r.json()
        assert body["ErrorNumber"] == 0
        assert body["Type"] == 2
        assert body["Rank"] == 2
        value = body["Value"]
        assert len(value) == 200  # X dimension first
        assert len(value[0]) == 150  # Y dimension


def test_imagearray_imagebytes_matches_json(tmp_path):
    """A client that sends `Accept: application/imagebytes` (as real Alpaca
    clients do to avoid the JSON ImageArray body's parse/size overhead on
    large frames) must get back pixel-identical data to the JSON path,
    decoded per the same header layout the reference `alpyca` client uses.
    """
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings)
    with TestClient(app) as client:
        client.put(f"{BASE}/connected", data={"Connected": "true"})
        client.put(f"{BASE}/startexposure", data={"Duration": "0.2", "Light": "true"})
        for _ in range(50):
            if client.get(f"{BASE}/imageready").json()["Value"]:
                break
            time.sleep(0.05)

        json_value = client.get(f"{BASE}/imagearray").json()["Value"]

        r = client.get(f"{BASE}/imagearray", headers={"Accept": "application/imagebytes"})
        assert r.headers["content-type"].startswith("application/imagebytes")
        body = r.content
        (
            metadata_version, error_number, _client_txn, _server_txn,
            data_start, image_element_type, xmsn_element_type, rank,
            dim1, dim2, dim3,
        ) = struct.unpack("<11i", body[:44])
        assert metadata_version == 1
        assert error_number == 0
        assert data_start == 44
        assert image_element_type == 2  # Int32, matching the JSON path's declared Type
        assert xmsn_element_type == 8  # UInt16 - the actual (compact) on-wire type
        assert rank == 2
        assert (dim1, dim2, dim3) == (200, 150, 0)

        import array

        pixels = array.array("H")
        pixels.frombytes(body[data_start:])
        for x in range(dim1):
            row = list(pixels[x * dim2 : (x + 1) * dim2])
            assert row == json_value[x]


def test_imagearray_imagebytes_error_when_not_connected():
    settings = make_settings(Path("/tmp/camsim_test_cat_bytes_err.csv"))
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get(f"{BASE}/imagearray", headers={"Accept": "application/imagebytes"})
        assert r.headers["content-type"].startswith("application/imagebytes")
        body = r.content
        error_number = struct.unpack("<i", body[4:8])[0]
        assert error_number == 0x407
        assert body[44:].decode("utf-8") == "Camera is not connected."


def test_management_api():
    settings = make_settings(Path("/tmp/camsim_test_cat.csv"))
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/management/apiversions")
        assert r.json()["Value"] == [1]
        r = client.get("/management/v1/configureddevices")
        devices = r.json()["Value"]
        assert devices[0]["DeviceType"] == "Camera"


def test_invalid_bin_rejected(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings)
    with TestClient(app) as client:
        client.put(f"{BASE}/connected", data={"Connected": "true"})
        r = client.put(f"{BASE}/binx", data={"BinX": "99"})
        assert r.json()["ErrorNumber"] == 0x401
