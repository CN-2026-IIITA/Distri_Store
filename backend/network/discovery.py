"""
DistriStore — UDP Broadcast Discovery with Health Scoring

Phase 2 Upgrade: HELLO broadcasts now include a Node Health Score.
  Health Score = (Free RAM MB) + (CPU Freq GHz * 100) - (Network Latency ms)

Peers are scored on discovery, enabling smarter chunk placement decisions.
"""

import asyncio
import orjson
import os
import socket
import time
import hmac
import hashlib
from typing import Optional

import psutil

from backend.node.state import NodeState, PeerInfo
from backend.utils.logger import get_logger

logger = get_logger("network.discovery")


def compute_health_score() -> dict:
    """
    Compute the current node's health metrics.

    Returns:
        dict with raw metrics and computed health_score.
    """
    mem = psutil.virtual_memory()
    free_ram_mb = mem.available / (1024 * 1024)

    # CPU frequency (GHz) — fallback to 0 if unavailable
    cpu_freq = psutil.cpu_freq()
    cpu_freq_ghz = (cpu_freq.current / 1000) if cpu_freq else 0.0

    # CPU usage (lower is better, we invert)
    cpu_percent = psutil.cpu_percent(interval=0)

    # Disk free space (cross-platform root path)
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    free_disk_gb = disk.free / (1024 ** 3)

    # Health Score formula:
    # (Free RAM MB) + (CPU Freq GHz * 100) + (Free Disk GB * 10) - (CPU load)
    health_score = round(
        free_ram_mb + (cpu_freq_ghz * 100) + (free_disk_gb * 10) - cpu_percent,
        2
    )

    return {
        "free_ram_mb": round(free_ram_mb, 1),
        "cpu_freq_ghz": round(cpu_freq_ghz, 2),
        "cpu_percent": round(cpu_percent, 1),
        "free_disk_gb": round(free_disk_gb, 2),
        "health_score": health_score,
    }


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol for peer discovery with health scoring."""

    def __init__(self, state: NodeState,
                 broadcast_addr: str = "255.255.255.255",
                 discovery_port: int = 50000,
                 interval: int = 5):
        self.state = state
        self.broadcast_addr = broadcast_addr
        self.discovery_port = discovery_port
        self.interval = interval
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info(f"Discovery listener on UDP :{self.discovery_port}")

    def datagram_received(self, data: bytes, addr: tuple):
        try:
            wrapper = orjson.loads(data)
        except (orjson.JSONDecodeError, UnicodeDecodeError):
            return

        payload = wrapper.get("payload")
        signature = wrapper.get("signature")

        if not payload or not signature:
            logger.debug("Rejected unauthorized UDP broadcast")
            return

        from backend.utils.config import get_config
        key = get_config().network.swarm_key.encode()
        payload_str = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        expected = hmac.new(key, payload_str, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.debug(f"Rejected unauthorized UDP broadcast: {payload_str} sig={signature} exp={expected}")
            return

        msg = payload

        if msg.get("type") != "HELLO":
            return

        peer_id = msg.get("node_id", "")
        if peer_id == self.state.node_id:
            return

        # Extract health metrics from the HELLO message
        health = msg.get("health", {})
        free_space_bytes = int(health.get("free_disk_gb", 0) * 1024 ** 3)

        peer = PeerInfo(
            node_id=peer_id,
            ip=addr[0],
            tcp_port=msg.get("tcp_port", 50001),
            name=msg.get("name", "unknown"),
            free_space=free_space_bytes,
            uptime=msg.get("uptime", 0),
            health_score=health.get("health_score", 0),
            api_port=msg.get("api_port", 8888),
            public_key=msg.get("public_key", ""),  # Phase 25A: onion-routing pubkey
        )

        logger.debug(
            f"HELLO from {peer.name} ({peer_id[:12]}...) at {peer.ip} "
            f"[health={peer.health_score}, RAM={health.get('free_ram_mb', 0)}MB, "
            f"CPU={health.get('cpu_freq_ghz', 0)}GHz]"
        )
        asyncio.ensure_future(self.state.add_peer(peer))

    def error_received(self, exc):
        logger.error(f"Discovery error: {exc}")

    def connection_lost(self, exc):
        logger.warning("Discovery transport closed")

    def _build_hello(self) -> bytes:
        """Build a HELLO message with health score and dynamic TCP port."""
        health = compute_health_score()
        payload = {
            "type": "HELLO",
            "node_id": self.state.node_id,
            "name": self.state.name,
            "tcp_port": getattr(self.state, 'tcp_port', 0),
            "api_port": getattr(self.state, 'api_port', 8888),
            "uptime": self.state.uptime,
            "timestamp": time.time(),
            "health": health,
            # Phase 25A: gossip our X25519 onion-routing pubkey (hex)
            "public_key": getattr(self.state, 'public_key_hex', ''),
        }
        from backend.utils.config import get_config
        key = get_config().network.swarm_key.encode()
        payload_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        sig = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()
        
        return orjson.dumps({"payload": payload, "signature": sig})

    async def broadcast_loop(self):
        logger.info(
            f"Broadcasting every {self.interval}s -> "
            f"{self.broadcast_addr}:{self.discovery_port}"
        )
        while True:
            try:
                hello = self._build_hello()
                self.transport.sendto(hello, (self.broadcast_addr, self.discovery_port))
                logger.debug(f"Broadcast HELLO ({len(hello)} bytes)")
            except Exception as e:
                logger.error(f"Broadcast failed: {e}")
            await asyncio.sleep(self.interval)


async def start_discovery(state, discovery_port=50000,
                          broadcast_address="255.255.255.255", interval=5):
    """Start the UDP discovery service. Returns the DiscoveryProtocol."""
    loop = asyncio.get_running_loop()
    protocol = DiscoveryProtocol(state, broadcast_address, discovery_port, interval)

    # Create a pre-configured socket with SO_REUSEADDR *before* binding
    # so multiple nodes on the same host can share the discovery port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("0.0.0.0", discovery_port))
    sock.setblocking(False)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        sock=sock,
    )

    logger.info("UDP Discovery service started")
    return protocol
