import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from camera_sim.config import FocuserConfig
from camera_sim.focuser_client import FocuserClient
from focuser_sim.config import FocuserSimConfig, FocuserSimServerConfig, FocuserSimSettings
from focuser_sim.server import create_app as create_focuser_app

# ASCOM's Focuser.StepSize is specified in microns already (unlike
# Telescope.FocalLength/ApertureDiameter, which are in meters) - no unit
# conversion should happen for it.
FAKE_FOCUSER_STATE = {
    "connected": True,
    "position": 15000,
    "stepsize": 2.5,
}


class _FocuserHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        prop = self.path.rsplit("/", 1)[-1].split("?")[0]
        if prop in FAKE_FOCUSER_STATE:
            body = json.dumps(
                {"ErrorNumber": 0, "ErrorMessage": "", "Value": FAKE_FOCUSER_STATE[prop]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_focuser_server():
    server = HTTPServer(("127.0.0.1", 0), _FocuserHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


def test_step_size_not_unit_converted(fake_focuser_server):
    cfg = FocuserConfig(
        alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}",
        in_focus_position=15000,
        use_focuser_step_size=True,
    )
    client = FocuserClient(cfg)
    state = client.get_state()

    assert state.source == "focuser"
    assert state.position == 15000
    assert state.step_size_um == 2.5  # not *1000 like FocalLength/ApertureDiameter


def test_defocus_zero_when_at_in_focus_position(fake_focuser_server):
    cfg = FocuserConfig(alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}", in_focus_position=15000)
    client = FocuserClient(cfg)
    defocus_um, source = client.get_defocus_um()

    assert source == "focuser"
    assert defocus_um == 0.0


def test_defocus_scales_with_step_offset(fake_focuser_server):
    cfg = FocuserConfig(alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}", in_focus_position=14900)
    client = FocuserClient(cfg)
    defocus_um, source = client.get_defocus_um()

    assert source == "focuser"
    # position=15000, in_focus=14900 -> 100 steps * 2.5um/step
    assert defocus_um == 250.0


def test_defocus_includes_extra_offset_steps(fake_focuser_server):
    """extra_offset_steps folds a filter wheel's per-filter FocusOffsets into
    the same calculation, in the same step units as the focuser."""
    cfg = FocuserConfig(alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}", in_focus_position=15000)
    client = FocuserClient(cfg)

    defocus_um, source = client.get_defocus_um(extra_offset_steps=40)

    assert source == "focuser"
    # position=15000, in_focus=15000, +40 filter offset steps * 2.5um/step
    assert defocus_um == 100.0


def test_defocus_extra_offset_can_be_negative(fake_focuser_server):
    cfg = FocuserConfig(alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}", in_focus_position=15000)
    client = FocuserClient(cfg)

    defocus_um, source = client.get_defocus_um(extra_offset_steps=-40)

    assert source == "focuser"
    assert defocus_um == -100.0


def test_fallback_is_zero_defocus_when_focuser_unreachable():
    cfg = FocuserConfig(
        alpaca_base_url="http://127.0.0.1:1",  # nothing listens here
        in_focus_position=12345,
        fallback_step_size_um=7.0,
        request_timeout_s=0.5,
    )
    client = FocuserClient(cfg)
    state = client.get_state()
    defocus_um, source = client.get_defocus_um()

    assert state.source == "fallback"
    assert state.position == 12345  # reported as sitting exactly at in-focus
    assert defocus_um == 0.0
    assert source == "fallback"


def test_falls_back_to_reported_position_without_optical_action(fake_focuser_server):
    """The fake server doesn't implement PUT /action (like most real
    drivers), so the reported Position must be used."""
    cfg = FocuserConfig(alpaca_base_url=f"http://127.0.0.1:{fake_focuser_server}", in_focus_position=15000)
    state = FocuserClient(cfg).get_state()

    assert state.optical is False
    assert state.position == state.reported_position == 15000


@pytest.fixture
def running_focuser_sim():
    """A real focuser_sim server on a free port, with 100 steps of backlash
    each way and instant moves."""
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = FocuserSimSettings(
        server=FocuserSimServerConfig(port=port, discovery_port=0),
        focuser=FocuserSimConfig(start_position=25000, backlash_in_steps=100, backlash_out_steps=100,
                                 step_size_um=2.0, steps_per_second=0),
    )
    server = uvicorn.Server(uvicorn.Config(create_focuser_app(settings), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_defocus_uses_optical_position_from_focuser_sim(running_focuser_sim):
    base = f"{running_focuser_sim}/api/v1/focuser/0"
    requests.put(f"{base}/connected", data={"Connected": "true"}, timeout=2)
    # Overshoot out, then come back in to "focus" - the classic move that
    # backlash spoils: the motor is back at 25000, the drawtube stops 100
    # steps short at 25100.
    requests.put(f"{base}/move", data={"Position": "25500"}, timeout=2)
    requests.put(f"{base}/move", data={"Position": "25000"}, timeout=2)

    cfg = FocuserConfig(alpaca_base_url=running_focuser_sim, in_focus_position=25000)
    client = FocuserClient(cfg)
    state = client.get_state()
    assert state.reported_position == 25000
    assert state.optical is True
    assert state.position == 25100
    assert client.defocus_um_for(state) == 200.0  # 100 steps * 2um

    cfg.use_optical_position_action = False
    defocus_um, _ = client.get_defocus_um()
    assert defocus_um == 0.0
