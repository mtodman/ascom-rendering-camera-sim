import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from camera_sim.config import CoverCalibratorConfig
from camera_sim.cover_calibrator_client import CoverCalibratorClient

# Ordinals confirmed against a real Alpaca CoverCalibrator device this
# session (not just documentation): CoverStatus Open=3/Closed=1,
# CalibratorStatus Ready=3/Off=1.
COVER_OPEN, COVER_CLOSED = 3, 1
CALIBRATOR_OFF, CALIBRATOR_READY = 1, 3


def _make_handler(fake_state: dict):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            prop = self.path.rsplit("/", 1)[-1].split("?")[0]
            if prop in fake_state:
                body = json.dumps(
                    {"ErrorNumber": 0, "ErrorMessage": "", "Value": fake_state[prop]}
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

    return _Handler


def _start_server(fake_state: dict):
    server = HTTPServer(("127.0.0.1", 0), _make_handler(fake_state))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


@pytest.fixture
def fake_server_factory():
    servers = []

    def _factory(fake_state: dict) -> int:
        server, thread, port = _start_server(fake_state)
        servers.append((server, thread))
        return port

    yield _factory
    for server, thread in servers:
        server.shutdown()
        thread.join(timeout=2)


def test_cover_open_calibrator_off_has_no_effect(fake_server_factory):
    port = fake_server_factory(
        {"connected": True, "coverstate": COVER_OPEN, "calibratorstate": CALIBRATOR_OFF, "brightness": 0, "maxbrightness": 100}
    )
    cfg = CoverCalibratorConfig(alpaca_base_url=f"http://127.0.0.1:{port}")
    state = CoverCalibratorClient(cfg).get_state()

    assert state.source == "covercalibrator"
    assert state.cover_closed is False
    assert state.calibrator_e_per_s == 0.0


def test_cover_closed_blocks_regardless_of_calibrator(fake_server_factory):
    port = fake_server_factory(
        {"connected": True, "coverstate": COVER_CLOSED, "calibratorstate": CALIBRATOR_OFF, "brightness": 0, "maxbrightness": 100}
    )
    cfg = CoverCalibratorConfig(alpaca_base_url=f"http://127.0.0.1:{port}")
    state = CoverCalibratorClient(cfg).get_state()

    assert state.cover_closed is True
    assert state.calibrator_e_per_s == 0.0


def test_calibrator_ready_scales_with_brightness_fraction(fake_server_factory):
    port = fake_server_factory(
        {"connected": True, "coverstate": COVER_CLOSED, "calibratorstate": CALIBRATOR_READY, "brightness": 50, "maxbrightness": 100}
    )
    cfg = CoverCalibratorConfig(
        alpaca_base_url=f"http://127.0.0.1:{port}", calibrator_e_per_s_at_max_brightness=1000.0
    )
    state = CoverCalibratorClient(cfg).get_state()

    assert state.cover_closed is True
    assert state.calibrator_e_per_s == 500.0  # 50/100 * 1000


def test_calibrator_not_ready_gives_no_illumination(fake_server_factory):
    # calibratorstate=2 (NotReady, e.g. still warming up) even with nonzero
    # brightness reported should not illuminate.
    port = fake_server_factory(
        {"connected": True, "coverstate": COVER_CLOSED, "calibratorstate": 2, "brightness": 50, "maxbrightness": 100}
    )
    cfg = CoverCalibratorConfig(alpaca_base_url=f"http://127.0.0.1:{port}")
    state = CoverCalibratorClient(cfg).get_state()

    assert state.calibrator_e_per_s == 0.0


def test_fallback_is_open_no_calibrator_when_unreachable():
    cfg = CoverCalibratorConfig(
        alpaca_base_url="http://127.0.0.1:1",  # nothing listens here
        request_timeout_s=0.5,
    )
    state = CoverCalibratorClient(cfg).get_state()

    assert state.source == "fallback"
    assert state.cover_closed is False
    assert state.calibrator_e_per_s == 0.0
