"""
DistriStore — FastAPI Entry Point
Starts the FastAPI server with the DistriStore node running in the background.

Usage:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
    python -m backend.main --name node-beta --tcp-port 50003 --api-port 8001
"""

import asyncio
import sys
import os

# Phase 17: Windows asyncio tuning — use IOCP for native speed
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import argparse
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.utils.config import get_config, _generate_node_id
from backend.utils.logger import setup_logging, get_logger
from backend.node.node import DistriNode
from backend.dht.routing import RoutingTable
from backend.storage.local_store import LocalStore
from backend.api.routes import router, init_routes

logger = get_logger("main")

# Module-level state
_node: DistriNode = None
_store: LocalStore = None
_routing: RoutingTable = None


def _init():
    """Initialize node, store, routing from config (+ env var overrides)."""
    global _node, _store, _routing

    config = get_config()

    # Allow env var overrides (useful when spawned by test or subprocess)
    name = os.environ.get("DS_NAME", config.node.name)
    tcp_port = int(os.environ.get("DS_TCP_PORT", config.network.tcp_port))
    udp_port = int(os.environ.get("DS_UDP_PORT", config.network.discovery_port))
    storage_dir = os.environ.get("DS_STORAGE_DIR", config.storage.chunk_dir)

    if name != config.node.name:
        config.node.name = name
        config.node.node_id = _generate_node_id()
    config.network.tcp_port = tcp_port
    config.network.discovery_port = udp_port
    config.storage.chunk_dir = storage_dir

    setup_logging(config.logging.level, config.logging.file)

    _node = DistriNode()
    _store = LocalStore(config.storage.chunk_dir)
    _routing = RoutingTable()
    init_routes(_node, _store, _routing)


@asynccontextmanager
async def lifespan(app):
    """Start P2P node when FastAPI starts, stop on shutdown."""
    # Set the API port on node state so HELLO broadcasts include it
    config = get_config()
    _node.state.api_port = int(os.environ.get("DS_API_PORT", config.api.port))
    # Phase 16: Wire SQLite persistence into NodeState
    _node.state.set_database(_store.db)
    await _node.state.load_peers_from_db()
    # Phase 25A: load (or generate on first boot) the onion-routing keypair
    from backend.network.identity import load_or_create_keypair
    _priv, _pub = load_or_create_keypair(_store.db._conn)
    _node.state.set_identity(_priv, _pub.encode().hex())
    # Phase 25B: launch the background proof-of-storage auditor loop
    import httpx as _httpx_for_audit
    from backend.strategies.audit import auditor_loop
    _audit_task = asyncio.create_task(
        auditor_loop(
            _node.state, _routing, _store, _store.db,
            _httpx_for_audit.AsyncClient, interval_seconds=30,
        )
    )
    await _node.start(_store)
    logger.info("DistriStore node started alongside API server")
    yield
    # Phase 25B: stop the auditor loop on shutdown
    try:
        _audit_task.cancel()
    except Exception:
        pass
    await _node.stop()
    logger.info("DistriStore node stopped")


# Initialize on module import
_init()

app = FastAPI(
    title="DistriStore API",
    version="1.0.0",
    description="Zero-Config P2P Storage Mesh",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    base_port = int(os.environ.get("DS_API_PORT", config.api.port))

    # Try ports in range [base_port, base_port+10] to avoid "Address in use"
    for port in range(base_port, base_port + 11):
        try:
            print(f"\n{'='*60}")
            print(f"  DistriStore GUI accessible at http://localhost:{port}")
            print(f"{'='*60}\n")
            uvicorn.run(
                "backend.main:app",
                host=config.api.host,
                port=port,
                reload=False,
            )
            break  # If uvicorn returns cleanly, don't try next port
        except OSError as e:
            if "Address already in use" in str(e) or e.errno == 98:
                logger.warning(f"Port {port} in use, trying {port + 1}...")
                continue
            raise
    else:
        logger.error(f"Could not find an open port in range {base_port}-{base_port+10}")
        sys.exit(1)
