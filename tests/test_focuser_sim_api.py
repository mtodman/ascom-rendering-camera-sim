import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from fastapi.testclient import TestClient

from focuser_sim.config import FocuserSimConfig, FocuserSimServerConfig, FocuserSimSettings
from focuser_sim.server import create_app

BASE = "/api/v1/focuser/0"


def make_app(tmp_path, **focuser_kwargs):
    settings = FocuserSimSettings(
        server=FocuserSimServerConfig(port=0, discovery_port=0),
        focuser=FocuserSimConfig(**focuser_kwargs),
    )
    return create_app(settings, config_path=tmp_path / "focuser_sim.yaml")


def connect(client):
    assert client.put(f"{BASE}/connected", data={"Connected": "true"}).json()["ErrorNumber"] == 0


def value(client, prop):
    body = client.get(f"{BASE}/{prop}").json()
    assert body["ErrorNumber"] == 0, body
    return body["Value"]


def action(client, name, parameters=""):
    body = client.put(f"{BASE}/action", data={"Action": name, "Parameters": parameters}).json()
    assert body["ErrorNumber"] == 0, body
    return body["Value"]


def wait_until_stopped(client, timeout=5.0):
    deadline = time.monotonic() + timeout
    while value(client, "ismoving"):
        assert time.monotonic() < deadline, "move did not finish"
        time.sleep(0.02)


def test_requires_connection(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        assert client.get(f"{BASE}/position").json()["ErrorNumber"] == 0x407


def test_basic_properties(tmp_path):
    with TestClient(make_app(tmp_path, start_position=1234, step_size_um=2.5, max_step=9000)) as client:
        connect(client)
        assert value(client, "absolute") is True
        assert value(client, "position") == 1234
        assert value(client, "stepsize") == 2.5
        assert value(client, "maxstep") == 9000
        assert value(client, "interfaceversion") == 4
        assert value(client, "tempcompavailable") is False
        assert set(value(client, "supportedactions")) == {"OpticalPosition", "BacklashState", "ResetBacklash"}


def test_timed_move_reports_moving_then_arrives(tmp_path):
    with TestClient(make_app(tmp_path, start_position=1000, steps_per_second=1000)) as client:
        connect(client)
        client.put(f"{BASE}/move", data={"Position": "1300"})
        assert value(client, "ismoving") is True
        wait_until_stopped(client)
        assert value(client, "position") == 1300


def test_halt_stops_part_way(tmp_path):
    with TestClient(make_app(tmp_path, start_position=1000, steps_per_second=500)) as client:
        connect(client)
        client.put(f"{BASE}/move", data={"Position": "5000"})
        time.sleep(0.2)
        client.put(f"{BASE}/halt")
        assert value(client, "ismoving") is False
        pos = value(client, "position")
        assert 1000 < pos < 5000


def test_backlash_visible_through_optical_position_action(tmp_path):
    with TestClient(make_app(tmp_path, start_position=25000, backlash_steps=100,
                             steps_per_second=0)) as client:
        connect(client)
        client.put(f"{BASE}/move", data={"Position": "25500"})
        client.put(f"{BASE}/move", data={"Position": "25000"})
        assert value(client, "position") == 25000
        assert action(client, "OpticalPosition") == "25100"

        state = json.loads(action(client, "BacklashState"))
        assert state["offset_steps"] == -100
        assert state["engaged_direction"] == "in"

        action(client, "ResetBacklash")
        assert action(client, "OpticalPosition") == "25000"


def test_move_clamped_to_range(tmp_path):
    with TestClient(make_app(tmp_path, start_position=100, max_step=1000, steps_per_second=0)) as client:
        connect(client)
        client.put(f"{BASE}/move", data={"Position": "-50"})
        assert value(client, "position") == 0
        client.put(f"{BASE}/move", data={"Position": "99999"})
        assert value(client, "position") == 1000


def test_device_state_includes_focuser_members(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        connect(client)
        names = {e["Name"] for e in value(client, "devicestate")}
        assert {"Connected", "IsMoving", "Position", "Temperature", "TimeStamp"} <= names


def test_unknown_action_rejected(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        connect(client)
        body = client.put(f"{BASE}/action", data={"Action": "Nope", "Parameters": ""}).json()
        assert body["ErrorNumber"] == 0x40C


def test_setup_page_and_live_backlash_change(tmp_path):
    config_path = tmp_path / "focuser_sim.yaml"
    app = make_app(tmp_path, steps_per_second=0)
    device = app.state.device
    with TestClient(app) as client:
        r = client.get("/setup/v1/focuser/0/setup")
        assert r.status_code == 200
        assert "Backlash Steps" in r.text

        form = {f"focuser_{k}": str(v) for k, v in FocuserSimConfig().model_dump().items()}
        form.update(focuser_backlash_steps="250", focuser_steps_per_second="0")
        server_cfg = FocuserSimServerConfig(port=0, discovery_port=0)
        form.update({f"server_{k}": str(v) for k, v in server_cfg.model_dump().items()})
        r = client.post("/setup/v1/focuser/0/setup", data=form)
        assert "Settings saved" in r.text
        assert device.model.backlash == 250
        assert yaml.safe_load(config_path.read_text())["focuser"]["backlash_steps"] == 250

        client.post("/setup/v1/focuser/0/control/move?position=24000")
        status = client.get("/setup/v1/focuser/0/status").json()
        assert status["status"]["motor_position"] == 24000
        assert status["status"]["optical_position"] == 24250
        assert status["history"][0]["lost_steps"] == 250
