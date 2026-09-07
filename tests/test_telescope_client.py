import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from camera_sim.config import TelescopeConfig
from camera_sim.telescope_client import TelescopeClient

# A real, spec-compliant Alpaca telescope reports FocalLength in *meters*
# (per the ASCOM ITelescope interface) - 0.4 here represents an actual
# 400mm scope, the exact case that exposed the bug.
FAKE_TELESCOPE_STATE = {
    "connected": True,
    "rightascension": 5.5,
    "declination": 12.3,
    "focallength": 0.4,
}


class _TelescopeHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        prop = self.path.rsplit("/", 1)[-1].split("?")[0]
        if prop in FAKE_TELESCOPE_STATE:
            body = json.dumps(
                {"ErrorNumber": 0, "ErrorMessage": "", "Value": FAKE_TELESCOPE_STATE[prop]}
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
def fake_telescope_server():
    server = HTTPServer(("127.0.0.1", 0), _TelescopeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


def test_focal_length_converted_from_meters_to_mm(fake_telescope_server):
    cfg = TelescopeConfig(
        alpaca_base_url=f"http://127.0.0.1:{fake_telescope_server}",
        device_number=0,
        use_telescope_focal_length=True,
    )
    client = TelescopeClient(cfg)
    pointing = client.get_pointing()

    assert pointing.source == "telescope"
    assert pointing.ra_hours == 5.5
    assert pointing.dec_deg == 12.3
    # 0.4m reported by the telescope must become 400mm, not be taken as 0.4mm.
    assert pointing.focal_length_mm == 400.0
