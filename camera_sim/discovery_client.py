"""Client side of the Alpaca UDP discovery protocol: broadcasts an
"alpacadiscovery1" probe, collects replies from any Alpaca device servers on
the network, then queries each one's Management API for devices of a given
type. Used by the web setup UI's "Discover telescopes/focusers" buttons so a
user can pick an existing Alpaca driver instead of typing its URL by hand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass

import httpx

logger = logging.getLogger("camera_sim.discovery_client")

DISCOVERY_MESSAGE = b"alpacadiscovery1"


@dataclass
class DiscoveredTelescope:
    host: str
    port: int
    device_number: int
    device_name: str
    unique_id: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return f"{self.device_name} ({self.host}:{self.port}, dev #{self.device_number})"


@dataclass
class DiscoveredFocuser:
    host: str
    port: int
    device_number: int
    device_name: str
    unique_id: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return f"{self.device_name} ({self.host}:{self.port}, dev #{self.device_number})"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, responders: set[tuple[str, int]]):
        self.responders = responders

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            payload = json.loads(data)
            port = int(payload["AlpacaPort"])
        except (ValueError, KeyError, TypeError):
            return
        self.responders.add((addr[0], port))


async def _broadcast_for_alpaca_servers(discovery_port: int, timeout_s: float) -> set[tuple[str, int]]:
    """Sends the Alpaca discovery probe and collects (host, port) replies for
    `timeout_s` seconds. Probes both the LAN broadcast address (for real
    devices elsewhere on the network) and localhost directly (broadcast
    delivery to loopback-bound responders isn't guaranteed on every OS/
    sandbox, so this ensures local Alpaca servers are always found).
    """
    loop = asyncio.get_running_loop()
    responders: set[tuple[str, int]] = set()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(responders), sock=sock
    )
    try:
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                transport.sendto(DISCOVERY_MESSAGE, (target, discovery_port))
            except OSError as exc:
                logger.debug("discovery probe to %s failed: %s", target, exc)
        await asyncio.sleep(timeout_s)
    finally:
        transport.close()
    return responders


async def _devices_of_type(client: httpx.AsyncClient, host: str, port: int, device_type: str) -> list[dict]:
    try:
        resp = await client.get(f"http://{host}:{port}/management/v1/configureddevices")
        resp.raise_for_status()
        devices = resp.json().get("Value", [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("management query to %s:%d failed: %s", host, port, exc)
        return []
    return [d for d in devices if d.get("DeviceType") == device_type]


async def _telescopes_from_server(client: httpx.AsyncClient, host: str, port: int) -> list[DiscoveredTelescope]:
    return [
        DiscoveredTelescope(
            host=host,
            port=port,
            device_number=d.get("DeviceNumber", 0),
            device_name=d.get("DeviceName", "Telescope"),
            unique_id=d.get("UniqueID", ""),
        )
        for d in await _devices_of_type(client, host, port, "Telescope")
    ]


async def _focusers_from_server(client: httpx.AsyncClient, host: str, port: int) -> list[DiscoveredFocuser]:
    return [
        DiscoveredFocuser(
            host=host,
            port=port,
            device_number=d.get("DeviceNumber", 0),
            device_name=d.get("DeviceName", "Focuser"),
            unique_id=d.get("UniqueID", ""),
        )
        for d in await _devices_of_type(client, host, port, "Focuser")
    ]


def _dedupe(found: list) -> list:
    # A multi-homed host (multiple network interfaces) answers a broadcast
    # probe once per interface, so the same physical device can otherwise
    # show up more than once under different IPs. Collapse those by the
    # device's own UniqueID (falling back to host/port/device_number for
    # drivers that don't set one) so the picker shows one entry per device.
    deduped: dict[str, object] = {}
    for item in found:
        key = item.unique_id or f"{item.host}:{item.port}:{item.device_number}"
        deduped.setdefault(key, item)
    return list(deduped.values())


async def discover_telescopes(discovery_port: int = 32227, timeout_s: float = 2.0) -> list[DiscoveredTelescope]:
    responders = await _broadcast_for_alpaca_servers(discovery_port, timeout_s)
    if not responders:
        return []
    async with httpx.AsyncClient(timeout=2.0) as client:
        results = await asyncio.gather(
            *(_telescopes_from_server(client, host, port) for host, port in responders)
        )
    return _dedupe([t for group in results for t in group])


async def discover_focusers(discovery_port: int = 32227, timeout_s: float = 2.0) -> list[DiscoveredFocuser]:
    responders = await _broadcast_for_alpaca_servers(discovery_port, timeout_s)
    if not responders:
        return []
    async with httpx.AsyncClient(timeout=2.0) as client:
        results = await asyncio.gather(
            *(_focusers_from_server(client, host, port) for host, port in responders)
        )
    return _dedupe([f for group in results for f in group])
