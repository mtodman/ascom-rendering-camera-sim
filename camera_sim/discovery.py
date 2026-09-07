"""Alpaca UDP discovery protocol: listens on the discovery port and replies
to "alpacadiscovery1" broadcasts with the server's Alpaca HTTP port.

Binds with SO_REUSEADDR/SO_REUSEPORT so this can coexist with other Alpaca
device servers on the same host also listening on the shared discovery port
32227 (per the Alpaca discovery spec, each replies with its own port).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

logger = logging.getLogger("camera_sim.discovery")

DISCOVERY_MESSAGE = b"alpacadiscovery1"


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, alpaca_port: int):
        self.alpaca_port = alpaca_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        if data.strip() == DISCOVERY_MESSAGE:
            reply = json.dumps({"AlpacaPort": self.alpaca_port}).encode()
            logger.debug("discovery request from %s, replying with port %d", addr, self.alpaca_port)
            if self.transport:
                self.transport.sendto(reply, addr)


async def start_discovery_server(discovery_port: int, alpaca_port: int) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("0.0.0.0", discovery_port))
    sock.setblocking(False)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: DiscoveryProtocol(alpaca_port),
        sock=sock,
    )
    logger.info("Alpaca discovery listening on UDP port %d (shared)", discovery_port)
    return transport
