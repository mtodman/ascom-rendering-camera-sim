import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from camera_sim.discovery_client import discover_focusers, discover_telescopes

FAKE_DEVICES_BODY = {
    "Value": [
        {"DeviceName": "Fake Camera", "DeviceType": "Camera", "DeviceNumber": 0, "UniqueID": "cam-1"},
        {"DeviceName": "Fake Mount", "DeviceType": "Telescope", "DeviceNumber": 0, "UniqueID": "tel-1"},
        {"DeviceName": "Fake Focuser", "DeviceType": "Focuser", "DeviceNumber": 0, "UniqueID": "foc-1"},
    ]
}


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ManagementHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/management/v1/configureddevices":
            body = json.dumps(FAKE_DEVICES_BODY).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def fake_alpaca_device():
    """Spins up a minimal fake Alpaca device: a UDP discovery responder plus
    an HTTP server answering /management/v1/configureddevices with one
    Camera and one Telescope, matching what a real Alpaca driver looks like
    from discover_telescopes()'s point of view.
    """
    http_server = HTTPServer(("127.0.0.1", 0), _ManagementHandler)
    http_port = http_server.server_address[1]
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()

    discovery_port = _free_udp_port()

    async def _run_udp_responder(stop_event: asyncio.Event):
        class Proto(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                if data.strip() == b"alpacadiscovery1":
                    reply = json.dumps({"AlpacaPort": http_port}).encode()
                    self.transport.sendto(reply, addr)

        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", discovery_port))
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(lambda: Proto(), sock=sock)
        try:
            await stop_event.wait()
        finally:
            transport.close()

    loop = asyncio.new_event_loop()
    result = {}

    def _thread_main():
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        result["stop"] = stop
        loop.run_until_complete(_run_udp_responder(stop))

    udp_thread = threading.Thread(target=_thread_main, daemon=True)
    udp_thread.start()
    # Give the responder a moment to bind before the test sends probes.
    import time

    time.sleep(0.2)

    yield {"discovery_port": discovery_port, "http_port": http_port}

    if "stop" in result:
        loop.call_soon_threadsafe(result["stop"].set)
    udp_thread.join(timeout=2)
    http_server.shutdown()
    http_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_discover_telescopes_finds_fake_device(fake_alpaca_device):
    found = await discover_telescopes(
        discovery_port=fake_alpaca_device["discovery_port"], timeout_s=0.8
    )
    assert len(found) == 1
    telescope = found[0]
    assert telescope.device_name == "Fake Mount"
    assert telescope.port == fake_alpaca_device["http_port"]
    assert telescope.base_url == f"http://127.0.0.1:{fake_alpaca_device['http_port']}"


@pytest.mark.asyncio
async def test_discover_telescopes_no_responders_returns_empty():
    found = await discover_telescopes(discovery_port=_free_udp_port(), timeout_s=0.3)
    assert found == []


@pytest.mark.asyncio
async def test_discover_focusers_finds_fake_device(fake_alpaca_device):
    found = await discover_focusers(
        discovery_port=fake_alpaca_device["discovery_port"], timeout_s=0.8
    )
    assert len(found) == 1
    focuser = found[0]
    assert focuser.device_name == "Fake Focuser"
    assert focuser.port == fake_alpaca_device["http_port"]
    assert focuser.base_url == f"http://127.0.0.1:{fake_alpaca_device['http_port']}"


@pytest.mark.asyncio
async def test_discover_focusers_no_responders_returns_empty():
    found = await discover_focusers(discovery_port=_free_udp_port(), timeout_s=0.3)
    assert found == []


@pytest.mark.asyncio
async def test_discover_telescopes_dedupes_multihomed_responses(monkeypatch):
    """A multi-homed host answers a broadcast once per interface, so the same
    device can be seen from two different (host, port) pairs. Both should
    collapse to one result, keyed by the device's UniqueID.
    """
    from camera_sim import discovery_client
    from camera_sim.discovery_client import DiscoveredTelescope

    same_device_twice = [
        DiscoveredTelescope(host="192.168.0.106", port=11112, device_number=0, device_name="Mount", unique_id="abc-123"),
        DiscoveredTelescope(host="192.168.0.122", port=11112, device_number=0, device_name="Mount", unique_id="abc-123"),
    ]

    async def fake_broadcast(discovery_port, timeout_s):
        return {("192.168.0.106", 11112), ("192.168.0.122", 11112)}

    async def fake_query(client, host, port):
        return [t for t in same_device_twice if t.host == host]

    monkeypatch.setattr(discovery_client, "_broadcast_for_alpaca_servers", fake_broadcast)
    monkeypatch.setattr(discovery_client, "_telescopes_from_server", fake_query)

    found = await discover_telescopes(discovery_port=1234, timeout_s=0.01)
    assert len(found) == 1
    assert found[0].unique_id == "abc-123"


@pytest.mark.asyncio
async def test_discover_focusers_dedupes_multihomed_responses(monkeypatch):
    from camera_sim import discovery_client
    from camera_sim.discovery_client import DiscoveredFocuser

    same_device_twice = [
        DiscoveredFocuser(host="192.168.0.106", port=11114, device_number=0, device_name="Focuser", unique_id="xyz-789"),
        DiscoveredFocuser(host="192.168.0.122", port=11114, device_number=0, device_name="Focuser", unique_id="xyz-789"),
    ]

    async def fake_broadcast(discovery_port, timeout_s):
        return {("192.168.0.106", 11114), ("192.168.0.122", 11114)}

    async def fake_query(client, host, port):
        return [f for f in same_device_twice if f.host == host]

    monkeypatch.setattr(discovery_client, "_broadcast_for_alpaca_servers", fake_broadcast)
    monkeypatch.setattr(discovery_client, "_focusers_from_server", fake_query)

    found = await discover_focusers(discovery_port=1234, timeout_s=0.01)
    assert len(found) == 1
    assert found[0].unique_id == "xyz-789"
