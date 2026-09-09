import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from camera_sim.config import FocuserConfig
from camera_sim.focuser_client import FocuserClient

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
