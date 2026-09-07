import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from fastapi.testclient import TestClient

from camera_sim.config import CameraConfig, CatalogConfig, ServerConfig, Settings, TelescopeConfig
from camera_sim.server import create_app


def make_settings(tmp_catalog: Path) -> Settings:
    tmp_catalog.write_text("ra_deg,dec_deg,vmag,bv\n0.0,0.0,5.0,0.5\n")
    return Settings(
        server=ServerConfig(port=0, discovery_port=0),
        telescope=TelescopeConfig(alpaca_base_url="http://127.0.0.1:1"),
        camera=CameraConfig(num_pixels_x=200, num_pixels_y=150),
        catalog=CatalogConfig(path=str(tmp_catalog)),
    )


def test_setup_index_lists_camera(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=tmp_path / "config.yaml")
    with TestClient(app) as client:
        r = client.get("/setup")
        assert r.status_code == 200
        assert "Sky-Rendering Camera Simulator" in r.text


def test_setup_status_shows_telescope_pointing(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=tmp_path / "config.yaml")
    with TestClient(app) as client:
        r = client.get("/setup/v1/camera/0/setup")
        assert r.status_code == 200
        # Unreachable telescope (http://127.0.0.1:1) -> falls back to the
        # configured fallback RA/Dec, and the page should say so.
        assert f"{settings.telescope.fallback_ra_hours}h" in r.text
        assert f"{settings.telescope.fallback_dec_deg}" in r.text
        assert "fallback" in r.text


def test_setup_form_prefilled_with_current_values(tmp_path):
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=tmp_path / "config.yaml")
    with TestClient(app) as client:
        r = client.get("/setup/v1/camera/0/setup")
        assert r.status_code == 200
        assert 'value="200"' in r.text  # num_pixels_x
        assert 'value="3.76"' in r.text  # pixel_size_um


def test_setup_post_applies_live_and_persists(tmp_path):
    config_path = tmp_path / "config.yaml"
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=config_path)
    device = app.state.device

    form = _full_form(settings)
    form["seeing_fwhm_arcsec"] = "4.4"

    with TestClient(app) as client:
        r = client.post("/setup/v1/camera/0/setup", data=form)
        assert r.status_code == 200
        assert "saved" in r.text.lower() or "Settings saved" in r.text

    # Applied live (same object identity as device.cfg).
    assert device.cfg.seeing_fwhm_arcsec == 4.4
    # Persisted to disk.
    on_disk = yaml.safe_load(config_path.read_text())
    assert on_disk["camera"]["seeing_fwhm_arcsec"] == 4.4


def test_setup_post_rejects_invalid_value(tmp_path):
    config_path = tmp_path / "config.yaml"
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=config_path)

    form = _full_form(settings)
    form["num_pixels_x"] = ""  # simulates a cleared/invalid number input

    with TestClient(app) as client:
        r = client.post("/setup/v1/camera/0/setup", data=form)
        assert r.status_code == 200
        assert "Couldn" in r.text or "invalid value" in r.text

    assert not config_path.exists()
    assert settings.camera.num_pixels_x == 200  # unchanged


def test_setup_post_bad_catalog_path_leaves_settings_unchanged(tmp_path):
    config_path = tmp_path / "config.yaml"
    settings = make_settings(tmp_path / "cat.csv")
    app = create_app(settings, config_path=config_path)
    device = app.state.device
    original_catalog = device.catalog

    form = _full_form(settings)
    form["path"] = str(tmp_path / "does_not_exist.csv")

    with TestClient(app) as client:
        r = client.post("/setup/v1/camera/0/setup", data=form)
        assert r.status_code == 200
        assert "could not load" in r.text.lower()

    # Nothing should have been half-applied: settings, device.catalog, and
    # the on-disk config must all still reflect the original good state.
    assert settings.catalog.path == str(tmp_path / "cat.csv")
    assert device.catalog is original_catalog
    assert not config_path.exists()


def _full_form(settings: Settings) -> dict:
    """Builds a complete form submission (every field from every section)
    from the settings' current values, the way the real HTML form would.
    """
    form: dict[str, str] = {}
    for model in (settings.server, settings.telescope, settings.camera, settings.catalog):
        for name, info in type(model).model_fields.items():
            value = getattr(model, name)
            if info.annotation is bool:
                if value:
                    form[name] = "on"
            else:
                form[name] = str(value)
    return form
