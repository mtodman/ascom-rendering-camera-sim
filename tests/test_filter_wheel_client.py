import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from camera_sim.config import FilterWheelConfig
from camera_sim.filter_wheel_client import FilterWheelClient

# Confirmed against a real Alpaca FilterWheel device this session: Names and
# FocusOffsets are plain JSON arrays, indexed the same as Position.
FAKE_FILTERWHEEL_STATE = {
    "connected": True,
    "position": 2,
    "names": ["Red", "Green", "Blue", "Clear"],
    "focusoffsets": [500, 550, 150, 0],
}


class _FilterWheelHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        prop = self.path.rsplit("/", 1)[-1].split("?")[0]
        if prop in FAKE_FILTERWHEEL_STATE:
            body = json.dumps(
                {"ErrorNumber": 0, "ErrorMessage": "", "Value": FAKE_FILTERWHEEL_STATE[prop]}
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
def fake_filterwheel_server():
    server = HTTPServer(("127.0.0.1", 0), _FilterWheelHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


def test_offset_and_name_looked_up_by_position(fake_filterwheel_server):
    cfg = FilterWheelConfig(alpaca_base_url=f"http://127.0.0.1:{fake_filterwheel_server}")
    state = FilterWheelClient(cfg).get_state()

    assert state.source == "filterwheel"
    assert state.position == 2
    assert state.name == "Blue"
    assert state.focus_offset_steps == 150


def test_zero_offset_filter_gives_zero(fake_filterwheel_server):
    # Position 3 ("Clear") has offset 0 in the fake state - a filter with no
    # offset should not contribute anything.
    FAKE_FILTERWHEEL_STATE["position"] = 3
    cfg = FilterWheelConfig(alpaca_base_url=f"http://127.0.0.1:{fake_filterwheel_server}")
    state = FilterWheelClient(cfg).get_state()
    FAKE_FILTERWHEEL_STATE["position"] = 2  # restore for other tests

    assert state.focus_offset_steps == 0
    assert state.name == "Clear"


def test_moving_position_minus_one_is_fallback(fake_filterwheel_server):
    FAKE_FILTERWHEEL_STATE["position"] = -1
    cfg = FilterWheelConfig(alpaca_base_url=f"http://127.0.0.1:{fake_filterwheel_server}")
    state = FilterWheelClient(cfg).get_state()
    FAKE_FILTERWHEEL_STATE["position"] = 2  # restore for other tests

    assert state.source == "fallback"
    assert state.focus_offset_steps == 0


def test_fallback_is_zero_offset_when_unreachable():
    cfg = FilterWheelConfig(
        alpaca_base_url="http://127.0.0.1:1",  # nothing listens here
        request_timeout_s=0.5,
    )
    state = FilterWheelClient(cfg).get_state()

    assert state.source == "fallback"
    assert state.position == -1
    assert state.focus_offset_steps == 0
    assert state.name == ""


def test_combines_with_focuser_defocus_calculation():
    """End-to-end sanity check of the intended usage pattern: the filter's
    offset feeds into FocuserClient.get_defocus_um(extra_offset_steps=...)."""
    from camera_sim.config import FocuserConfig
    from camera_sim.focuser_client import FocuserClient

    class _FakeFocuserHandler(BaseHTTPRequestHandler):
        _state = {"connected": True, "position": 15000, "stepsize": 2.5}

        def do_GET(self):  # noqa: N802
            prop = self.path.rsplit("/", 1)[-1].split("?")[0]
            if prop in self._state:
                body = json.dumps(
                    {"ErrorNumber": 0, "ErrorMessage": "", "Value": self._state[prop]}
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

    focuser_server = HTTPServer(("127.0.0.1", 0), _FakeFocuserHandler)
    focuser_thread = threading.Thread(target=focuser_server.serve_forever, daemon=True)
    focuser_thread.start()
    try:
        foc_cfg = FocuserConfig(
            alpaca_base_url=f"http://127.0.0.1:{focuser_server.server_address[1]}", in_focus_position=15000
        )
        fw_cfg = FilterWheelConfig(alpaca_base_url="http://127.0.0.1:1", request_timeout_s=0.3)  # unreachable

        fw_state = FilterWheelClient(fw_cfg).get_state()  # falls back to offset 0
        defocus_um, _ = FocuserClient(foc_cfg).get_defocus_um(fw_state.focus_offset_steps)

        assert defocus_um == 0.0  # in-focus focuser + zero filter offset -> zero
    finally:
        focuser_server.shutdown()
        focuser_thread.join(timeout=2)
