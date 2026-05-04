"""
DistriStore — Node Orchestrator
Boots the node: loads config, starts discovery, starts TCP server,
and connects to discovered peers.
"""

import asyncio

from backend.utils.config import get_config
from backend.utils.logger import setup_logging, get_logger
from backend.node.state import NodeState
from backend.network.discovery import start_discovery
from backend.network.connection import ConnectionManager

logger = get_logger("node.core")


class DistriNode:
    """Main node orchestrator — ties together state, discovery, and connections."""

    def __init__(self, config_path: str = None):
        self.config = get_config(config_path)
        setup_logging(self.config.logging.level, self.config.logging.file)

        self.state = NodeState(
            node_id=self.config.node.node_id,
            name=self.config.node.name,
        )
        self.conn_mgr = ConnectionManager(self.state, message_handler=self._handle_message)
        self._discovery_protocol = None
        self._tasks: list[asyncio.Task] = []
        # Phase 25B: local store wired in via start(local_store=...) so the
        # STORE_CHUNK handler can actually persist incoming replication payloads.
        self._local_store = None

    async def _handle_message(self, conn, msg: dict) -> None:
        """Route incoming TCP messages to the appropriate handler."""
        msg_type = msg.get("type", "")

        if msg_type == "CHAT":
            # Phase 19: Route chat messages to WebSocket bridge
            from backend.api.routes import handle_tcp_chat
            await handle_tcp_chat(msg)
        elif msg_type == "STORE_CHUNK":
            # Phase 25B: actually persist chunks pushed by replication peers.
            # Without this, k-copy replication silently no-ops (peer ACKs but
            # never saves), and proof-of-storage audits always fail.
            chunk_hash = msg.get("chunk_hash", "")
            chunk_data = msg.get("chunk_data", b"")
            file_hash = msg.get("file_hash", "")
            if not chunk_hash or not chunk_data or self._local_store is None:
                logger.warning(
                    f"STORE_CHUNK from {conn.peer_id[:12]}... missing fields "
                    f"or no local_store wired"
                )
            else:
                try:
                    self._local_store.save_chunk(chunk_hash, chunk_data)
                    await self.state.register_chunk(chunk_hash, chunk_hash)
                    logger.info(
                        f"Stored chunk {chunk_hash[:12]}... ({len(chunk_data)}B) "
                        f"from {conn.peer_id[:12]}... (file {file_hash[:12]}...)"
                    )
                except Exception as e:
                    logger.error(f"Failed to save chunk {chunk_hash[:12]}...: {e}")
        elif msg_type in ("GET_CHUNK", "CHUNK_ACK", "STORE_ACK",
                          "CHUNK_DATA", "FIND_NODE", "FIND_RESULT", "PING", "PONG"):
            logger.debug(f"TCP msg from {conn.peer_id[:12]}...: {msg_type}")
        else:
            logger.debug(f"Unknown TCP msg type: {msg_type}")

    async def start(self, local_store=None) -> None:
        """Boot the node: TCP server + UDP discovery + peer connector."""
        net = self.config.network

        # Phase 25B: stash local_store so the STORE_CHUNK handler can persist
        self._local_store = local_store

        # 1. Start TCP server (port=0 lets OS assign a free port)
        await self.conn_mgr.start_server("0.0.0.0", net.tcp_port)

        # 2. Start UDP discovery (reads dynamic tcp_port from state)
        self._discovery_protocol = await start_discovery(
            state=self.state,
            discovery_port=net.discovery_port,
            broadcast_address=net.broadcast_address,
            interval=net.discovery_interval,
        )

        # 3. Launch background tasks
        self._tasks.append(asyncio.create_task(
            self._discovery_protocol.broadcast_loop()
        ))
        self._tasks.append(asyncio.create_task(
            self._peer_connector_loop()
        ))
        
        if local_store:
            from backend.advanced.garbage_collector import storage_monitor_loop
            self._tasks.append(asyncio.create_task(
                storage_monitor_loop(local_store)
            ))

        logger.info(
            f"Node [{self.state.name}] started | "
            f"ID: {self.state.node_id[:12]}... | "
            f"TCP: {self.state.tcp_port} | UDP: {net.discovery_port}"
        )

    async def _peer_connector_loop(self) -> None:
        """Periodically try to TCP-connect to discovered peers we're not connected to."""
        while True:
            await asyncio.sleep(3)
            peers = await self.state.get_alive_peers(self.config.network.peer_timeout)
            for nid, peer in peers.items():
                if nid not in self.conn_mgr.connections:
                    logger.debug(f"Attempting TCP to {peer.ip}:{peer.tcp_port}")
                    await self.conn_mgr.connect_to_peer(peer.ip, peer.tcp_port)

    async def stop(self) -> None:
        """Gracefully shut down."""
        for t in self._tasks:
            t.cancel()
        await self.conn_mgr.stop()
        logger.info("Node shut down")

    async def run_forever(self) -> None:
        """Start the node and block until interrupted."""
        await self.start()
        try:
            await asyncio.Event().wait()  # block forever
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
