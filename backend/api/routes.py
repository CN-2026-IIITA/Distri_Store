"""
DistriStore — FastAPI Routes
REST endpoints for upload, download, status, WebSocket chat,
and resumable download management (Phase 21).
"""

import os
import tempfile
from typing import Optional

import uuid
import time

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

from backend.utils.logger import get_logger

logger = get_logger("api.routes")

router = APIRouter()

# These will be set by main.py at startup
_node = None
_local_store = None
_routing = None
_download_manager = None


def init_routes(node, local_store, routing):
    """Inject dependencies into routes."""
    global _node, _local_store, _routing, _download_manager
    _node = node
    _local_store = local_store
    _routing = routing

    # Phase 21: Initialize the download manager
    from backend.api.download_manager import DownloadManager
    storage_dir = str(local_store.storage_dir) if local_store else ".storage"
    _download_manager = DownloadManager(storage_dir)



# ── Phase 19: WebSocket Chat Manager ─────────────────────────────────

class ChatManager:
    """Manages active WebSocket connections for swarm chat."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)
        logger.info(f"WebSocket chat connected ({len(self.active_connections)} total)")

    def disconnect(self, ws: WebSocket):
        self.active_connections.remove(ws)
        logger.info(f"WebSocket chat disconnected ({len(self.active_connections)} total)")

    async def broadcast(self, message: dict):
        """Send a message dict to all connected WebSocket clients."""
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self.active_connections.remove(ws)
            except ValueError:
                pass


chat_manager = ChatManager()


@router.get("/status")
async def get_status():
    """Get node status: peers, chunks, uptime."""
    if not _node:
        raise HTTPException(503, "Node not initialized")
    status = await _node.state.status()
    status["local_chunks"] = _local_store.list_chunks() if _local_store else []
    status["storage_used"] = _local_store.get_storage_size() if _local_store else 0
    
    from backend.utils.config import get_config
    config = get_config()
    status["storage_used_mb"] = round(status["storage_used"] / (1024 * 1024), 2)
    status["storage_max_mb"] = config.storage.max_storage_mb
    status["swarm_auth_active"] = True
    
    return status


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    password: str = Form(""),
):
    """Upload a file: chunk, encrypt, store locally, and replicate."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")

    from backend.file_engine.chunker import chunk_file, FileManifest, ShardInfo, get_optimal_chunk_size
    from backend.strategies.replication import ReplicationEngine
    from backend.utils.config import get_config as _get_cfg

    # Save uploaded file to temp location
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename)
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)

    try:
        # Get optimal chunk size (Phase 13 Dynamic Chunking)
        file_size = os.path.getsize(tmp_path)
        opt_chunk_size = get_optimal_chunk_size(file_size)

        # Chunk + encrypt
        pwd = password if password else None
        manifest, chunks = chunk_file(tmp_path, chunk_size=opt_chunk_size, password=pwd)

        # ── Phase 23: Reed-Solomon erasure coding (opt-in) ─────────
        cfg = _get_cfg()
        erasure_mode = cfg.replication.mode == "erasure"
        shards_per_chunk = []  # parallel to manifest.chunks; populated only in erasure mode

        if erasure_mode:
            from backend.strategies.erasure import encode_chunk
            k_data, n_total = cfg.replication.erasure_k, cfg.replication.erasure_n

            for info, data in zip(manifest.chunks, chunks):
                encoded = encode_chunk(data, chunk_index=info.index, k=k_data, n=n_total)
                # Save each shard locally (storage is shard-keyed by SHA-256, same as chunks)
                for s in encoded.shards:
                    _local_store.save_chunk(s.shard_hash, s.data)
                    await _node.state.register_chunk(s.shard_hash, s.shard_hash)
                # Attach shard metadata so the downloader can find them
                info.shards = [
                    ShardInfo(shard_index=s.shard_index, shard_hash=s.shard_hash, size=s.size)
                    for s in encoded.shards
                ]
                shards_per_chunk.append(encoded.shards)

            manifest.replication_mode = "erasure"
            manifest.erasure_k = k_data
            manifest.erasure_n = n_total
            logger.info(
                f"Erasure-encoded {len(chunks)} chunks into "
                f"{len(chunks) * n_total} shards (k={k_data}, n={n_total}, "
                f"overhead={n_total/k_data:.2f}x)"
            )
        else:
            # k-copy mode: store the whole encrypted chunks locally
            for info, data in zip(manifest.chunks, chunks):
                _local_store.save_chunk(info.chunk_hash, data)
                await _node.state.register_chunk(info.chunk_hash, info.chunk_hash)

        # Save manifest
        _local_store.save_manifest(manifest.file_hash, manifest.to_dict())

        # Replicate to peers if available
        replicated = {}
        peers = await _node.state.get_alive_peers()
        if peers and _routing:
            engine = ReplicationEngine(
                _node.state, _node.conn_mgr, _routing, _local_store
            )
            if erasure_mode:
                replicated = await engine.replicate_shards(manifest, shards_per_chunk)
            else:
                replicated = await engine.replicate_chunks(manifest, chunks)

        logger.info(f"Uploaded '{file.filename}': {len(chunks)} chunks, hash={manifest.file_hash[:16]}...")

        # Phase 18: compression telemetry
        compressed_size = sum(len(c) for c in chunks)
        original_size = manifest.original_size
        ratio = round(original_size / compressed_size, 2) if compressed_size > 0 else 1.0

        return {
            "status": "success",
            "file_hash": manifest.file_hash,
            "filename": manifest.original_filename,
            "size": manifest.original_size,
            "compressed_size": compressed_size,
            "compression_ratio": ratio,
            "compression": manifest.compression,
            "chunks": len(manifest.chunks),
            "manifest": manifest.to_dict(),
            "replication": replicated,
        }
    finally:
        os.unlink(tmp_path)
        os.rmdir(tmp_dir)


@router.get("/download/{file_hash}")
async def download_file(
    file_hash: str,
    password: str = "",
    background_tasks: BackgroundTasks = None,
):
    """
    Download a file by its hash: load chunks, decrypt, merge to disk.
    If the manifest or chunks aren't stored locally, fetches them from
    discovered peers via their HTTP API — enabling true cross-node downloads.
    """
    if not _local_store or not _node:
        raise HTTPException(503, "Node not initialized")

    import httpx
    from backend.file_engine.chunker import merge_chunks_to_disk, FileManifest

    # ── Step 1: Load or fetch manifest ─────────────────────────────
    manifest_dict = _local_store.load_manifest(file_hash)

    if not manifest_dict:
        # Not local — ask peers
        logger.info(f"Manifest {file_hash[:16]}... not local, querying peers...")
        peers = await _node.state.get_alive_peers()
        for nid, peer in peers.items():
            peer_url = f"http://{peer.ip}:{peer.api_port}/manifest/{file_hash}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(peer_url)
                if resp.status_code == 200:
                    manifest_dict = resp.json()
                    # Cache it locally for next time
                    _local_store.save_manifest(file_hash, manifest_dict)
                    logger.info(f"Fetched manifest from peer {peer.name} ({peer.ip})")
                    break
            except Exception as e:
                logger.debug(f"Peer {peer.ip}:{peer.api_port} manifest fetch failed: {e}")
                continue

    if not manifest_dict:
        raise HTTPException(404, f"File not found on this node or any peer: {file_hash}")

    manifest = FileManifest.from_dict(manifest_dict)

    # ── Step 2: Load or fetch chunks ───────────────────────────────
    chunks = []
    peers = None  # Lazy-load peer list only if needed

    async def _peer_fetch(target_hash: str):
        """Fetch any chunk/shard hash from peers, returning bytes or None."""
        nonlocal peers
        if peers is None:
            peers = await _node.state.get_alive_peers()
        for _nid, peer in peers.items():
            url = f"http://{peer.ip}:{peer.api_port}/chunk/{target_hash}"
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
            except Exception as e:
                logger.debug(f"Peer {peer.ip} fetch {target_hash[:12]}... failed: {e}")
                continue
        return None

    erasure_mode = manifest.replication_mode == "erasure"

    for info in manifest.chunks:
        if erasure_mode and info.shards:
            # Phase 23: reconstruct from any k of n Reed-Solomon shards
            from backend.strategies.erasure import fetch_and_decode_chunk
            try:
                data = await fetch_and_decode_chunk(
                    info, manifest.erasure_k, manifest.erasure_n,
                    _local_store, _peer_fetch,
                )
            except ValueError as ve:
                raise HTTPException(404, str(ve))
        else:
            # Whole-chunk path (k-copy mode)
            data = _local_store.load_chunk(info.chunk_hash)
            if data is None:
                data = await _peer_fetch(info.chunk_hash)
                if data is None:
                    raise HTTPException(404, f"Chunk {info.chunk_hash[:16]}... not found on any node")
                _local_store.save_chunk(info.chunk_hash, data)
                logger.debug(f"Fetched chunk {info.chunk_hash[:12]}... from peer")

        chunks.append(data)

    # ── Step 3: Merge + decrypt to disk ────────────────────────────
    # Check if file is encrypted and password is needed
    is_encrypted = any(info.encrypted for info in manifest.chunks)
    pwd = password if password else None

    if is_encrypted and not pwd:
        raise HTTPException(
            400,
            "This file is encrypted. Please provide the decryption password."
        )

    temp_dir = _local_store.storage_dir
    temp_file = os.path.join(str(temp_dir), f"temp_{uuid.uuid4().hex}.bin")

    try:
        merge_chunks_to_disk(manifest, chunks, temp_file, password=pwd)
    except ValueError as e:
        if os.path.exists(temp_file):
            os.unlink(temp_file)
        error_msg = str(e)
        if "integrity check failed" in error_msg.lower():
            if is_encrypted:
                error_msg += " (Wrong password? This file is encrypted.)"
        raise HTTPException(400, f"Decryption/integrity error: {error_msg}")

    # Schedule temp file deletion after response is sent
    if background_tasks:
        background_tasks.add_task(os.remove, temp_file)

    logger.info(
        f"Serving '{manifest.original_filename}' ({manifest.original_size} bytes) "
        f"via FileResponse [cross-node capable]"
    )

    return FileResponse(
        path=temp_file,
        media_type="application/octet-stream",
        filename=manifest.original_filename,
    )


@router.get("/preview/{file_hash}")
async def preview_file(
    file_hash: str,
    password: str = "",
):
    """
    Phase 20: Stream a file for in-browser preview (images, videos, PDFs, text).
    Uses an async generator for O(1) memory — never buffers the full file.
    Returns Content-Disposition: inline so the browser renders it.
    """
    import mimetypes
    import asyncio

    if not _local_store or not _node:
        raise HTTPException(503, "Node not initialized")

    from backend.file_engine.chunker import FileManifest
    from backend.file_engine.pipeline import pipeline_stream_file

    # ── Load manifest (local or from peers) ─────────────────────────
    manifest_dict = _local_store.load_manifest(file_hash)

    if not manifest_dict:
        import httpx
        peers = await _node.state.get_alive_peers()
        for nid, peer in peers.items():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"http://{peer.ip}:{peer.api_port}/manifest/{file_hash}")
                if resp.status_code == 200:
                    manifest_dict = resp.json()
                    _local_store.save_manifest(file_hash, manifest_dict)
                    break
            except Exception:
                continue

    if not manifest_dict:
        raise HTTPException(404, f"File not found: {file_hash}")

    manifest = FileManifest.from_dict(manifest_dict)

    # ── Check encryption ─────────────────────────────────────────
    is_encrypted = any(c.encrypted for c in manifest.chunks)
    pwd = password if password else None
    if is_encrypted and not pwd:
        raise HTTPException(400, "This file is encrypted. Provide a password.")

    # ── MIME type detection ───────────────────────────────────────
    media_type, _ = mimetypes.guess_type(manifest.original_filename)
    if not media_type:
        media_type = "application/octet-stream"

    # ── Chunk loader (local + peer fallback, erasure-aware) ──────
    erasure_mode = manifest.replication_mode == "erasure"
    chunks_by_hash = {c.chunk_hash: c for c in manifest.chunks}

    async def _peer_fetch_one(target_hash: str):
        import httpx
        peers = await _node.state.get_alive_peers()
        for _nid, peer in peers.items():
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(f"http://{peer.ip}:{peer.api_port}/chunk/{target_hash}")
                if resp.status_code == 200:
                    return resp.content
            except Exception:
                continue
        return None

    async def load_chunk(chunk_hash: str) -> bytes:
        # Erasure mode: reconstruct from any k of n shards.
        if erasure_mode:
            info = chunks_by_hash.get(chunk_hash)
            if info is not None and info.shards:
                from backend.strategies.erasure import fetch_and_decode_chunk
                try:
                    return await fetch_and_decode_chunk(
                        info, manifest.erasure_k, manifest.erasure_n,
                        _local_store, _peer_fetch_one,
                    )
                except ValueError as ve:
                    raise HTTPException(404, str(ve))
        # k-copy mode (or erasure manifest missing shard metadata): whole chunk
        data = _local_store.load_chunk(chunk_hash)
        if data is not None:
            return data
        data = await _peer_fetch_one(chunk_hash)
        if data is not None:
            _local_store.save_chunk(chunk_hash, data)
            return data
        raise HTTPException(404, f"Chunk {chunk_hash[:16]}... not found")

    logger.info(
        f"Streaming preview '{manifest.original_filename}' "
        f"({manifest.original_size} bytes, {media_type}) [O(1) memory]"
    )

    return StreamingResponse(
        pipeline_stream_file(manifest, load_chunk, password=pwd),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{manifest.original_filename}"',
            "Content-Length": str(manifest.original_size),
        },
    )


@router.get("/files")
async def list_files(local_only: bool = False):
    """List all stored file manifests — local and from discovered peers."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")

    import json
    import httpx

    # Local files (Phase 16: from SQLite)
    manifests = []
    seen_hashes = set()
    for data in _local_store.get_all_manifests():
        fh = data.get("file_hash")
        seen_hashes.add(fh)
        manifests.append({
            "file_hash": fh,
            "filename": data.get("original_filename"),
            "size": data.get("original_size"),
            "chunks": len(data.get("chunks", [])),
            "merkle_root": data.get("merkle_root", ""),
            "source": "local",
        })

    # Also fetch file lists from alive peers (skip if this is a peer-to-peer call)
    if _node and not local_only:
        peers = await _node.state.get_alive_peers()
        for nid, peer in peers.items():
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    # Pass local_only=true to prevent recursion
                    resp = await client.get(
                        f"http://{peer.ip}:{peer.api_port}/files",
                        params={"local_only": "true"},
                    )
                if resp.status_code == 200:
                    peer_files = resp.json().get("files", [])
                    for pf in peer_files:
                        fh = pf.get("file_hash")
                        if fh and fh not in seen_hashes:
                            seen_hashes.add(fh)
                            pf["source"] = f"peer:{peer.name}"
                            manifests.append(pf)
            except Exception:
                continue  # Peer unreachable, skip

    return {"files": manifests}


@router.get("/manifest/{file_hash}")
async def get_manifest(file_hash: str):
    """Fetch the full manifest for a file (for swarmed downloads)."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")

    manifest_dict = _local_store.load_manifest(file_hash)
    if not manifest_dict:
        raise HTTPException(404, f"Manifest not found: {file_hash}")
    return manifest_dict


@router.get("/chunk/{chunk_hash}")
async def get_chunk(chunk_hash: str):
    """Fetch a single raw chunk by its hash (for swarmed downloads)."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")

    from fastapi.responses import Response

    data = _local_store.load_chunk(chunk_hash)
    if data is None:
        raise HTTPException(404, f"Chunk not found: {chunk_hash[:16]}...")

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"X-Chunk-Hash": chunk_hash},
    )


# ── Phase 21: Resumable Downloads ────────────────────────────────────

def _build_chunk_loader(manifest=None):
    """
    Build the async chunk loader with local + peer fallback.

    If `manifest` is provided and is in erasure mode, the loader transparently
    reconstructs each chunk from any k of n Reed-Solomon shards (Phase 23).
    Otherwise the loader fetches whole chunks (k-copy mode).
    """
    import httpx

    async def _peer_fetch_one(target_hash: str):
        peers = await _node.state.get_alive_peers()
        for _nid, peer in peers.items():
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"http://{peer.ip}:{peer.api_port}/chunk/{target_hash}"
                    )
                if resp.status_code == 200:
                    return resp.content
            except Exception:
                continue
        return None

    erasure_mode = bool(manifest and manifest.replication_mode == "erasure")
    chunks_by_hash = {c.chunk_hash: c for c in manifest.chunks} if manifest else {}

    async def load_chunk(chunk_hash: str) -> bytes:
        # Erasure mode: reconstruct from shards.
        if erasure_mode:
            info = chunks_by_hash.get(chunk_hash)
            if info is not None and info.shards:
                from backend.strategies.erasure import fetch_and_decode_chunk
                return await fetch_and_decode_chunk(
                    info, manifest.erasure_k, manifest.erasure_n,
                    _local_store, _peer_fetch_one,
                )
        # k-copy mode: try local, then peers.
        data = _local_store.load_chunk(chunk_hash)
        if data is not None:
            return data
        data = await _peer_fetch_one(chunk_hash)
        if data is not None:
            _local_store.save_chunk(chunk_hash, data)
            return data
        raise FileNotFoundError(f"Chunk {chunk_hash[:16]}... not available")

    return load_chunk


@router.get("/downloads")
async def list_downloads():
    """List all active, paused, and completed downloads with progress."""
    if not _download_manager:
        raise HTTPException(503, "Download manager not initialized")
    return {"downloads": _download_manager.get_all_downloads()}


@router.post("/download/{file_hash}/start")
async def start_resumable_download(file_hash: str, password: str = ""):
    """
    Start a new resumable download or resume an existing one.
    Returns the download state with progress tracking.
    """
    if not _local_store or not _node or not _download_manager:
        raise HTTPException(503, "Node not initialized")

    import httpx
    from backend.file_engine.chunker import FileManifest

    # Load or fetch manifest
    manifest_dict = _local_store.load_manifest(file_hash)
    if not manifest_dict:
        peers = await _node.state.get_alive_peers()
        for nid, peer in peers.items():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"http://{peer.ip}:{peer.api_port}/manifest/{file_hash}"
                    )
                if resp.status_code == 200:
                    manifest_dict = resp.json()
                    _local_store.save_manifest(file_hash, manifest_dict)
                    break
            except Exception:
                continue

    if not manifest_dict:
        raise HTTPException(404, f"File not found: {file_hash}")

    parsed_manifest = FileManifest.from_dict(manifest_dict)
    state = await _download_manager.start_download(
        file_hash=file_hash,
        manifest_dict=manifest_dict,
        password=password,
        load_chunk_fn=_build_chunk_loader(parsed_manifest),
        local_store=_local_store,
    )

    return {"status": "started", "download": state.to_dict()}


@router.post("/download/{file_hash}/pause")
async def pause_download(file_hash: str):
    """Pause an active download and save its progress to a .resume file."""
    if not _download_manager:
        raise HTTPException(503, "Download manager not initialized")

    state = await _download_manager.pause_download(file_hash)
    if not state:
        raise HTTPException(404, f"No active download for: {file_hash}")

    return {"status": "paused", "download": state.to_dict()}


@router.post("/download/{file_hash}/resume")
async def resume_download(file_hash: str, password: str = ""):
    """Resume a paused download from its last saved checkpoint."""
    if not _download_manager or not _local_store:
        raise HTTPException(503, "Node not initialized")

    # Reload manifest so the loader knows the replication mode (k-copy vs erasure)
    from backend.file_engine.chunker import FileManifest
    manifest_dict = _local_store.load_manifest(file_hash)
    parsed_manifest = FileManifest.from_dict(manifest_dict) if manifest_dict else None

    state = await _download_manager.resume_download(
        file_hash=file_hash,
        password=password,
        load_chunk_fn=_build_chunk_loader(parsed_manifest),
        local_store=_local_store,
    )

    if not state:
        raise HTTPException(404, f"No paused download for: {file_hash}")

    return {"status": "resumed", "download": state.to_dict()}


@router.get("/download/{file_hash}/progress")
async def download_progress(file_hash: str):
    """Get the current progress of a download."""
    if not _download_manager:
        raise HTTPException(503, "Download manager not initialized")

    state = _download_manager.get_download(file_hash)
    if not state:
        raise HTTPException(404, f"No download tracked for: {file_hash}")

    return {"download": state.to_dict()}


@router.post("/downloads/clear")
async def clear_downloads():
    """Remove completed and errored downloads from the tracker."""
    if not _download_manager:
        raise HTTPException(503, "Download manager not initialized")

    _download_manager.clear_completed()
    return {"status": "cleared", "downloads": _download_manager.get_all_downloads()}


# ── Phase 19: WebSocket Chat ─────────────────────────────────────────

# Track seen message IDs to prevent echo loops in gossip
_seen_chat_ids: set = set()
_MAX_SEEN = 500


@router.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """
    WebSocket bridge for swarm chat.
    - Receives messages from the local user → broadcasts to local WS + TCP peers.
    - TCP peer messages are routed here via handle_tcp_chat().
    """
    await chat_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            text = data.get("text", "").strip()
            if not text or not _node:
                continue

            # Build chat message
            from backend.network.protocol import chat_msg
            msg_id = uuid.uuid4().hex[:12]
            msg = chat_msg(_node.state.node_id, _node.state.name, text)
            msg["msg_id"] = msg_id

            # Track this message so we don't echo it back from TCP
            _seen_chat_ids.add(msg_id)
            if len(_seen_chat_ids) > _MAX_SEEN:
                # Evict oldest (sets aren't ordered, but this prevents unbounded growth)
                _seen_chat_ids.pop()

            # 1. Broadcast to all local WebSocket clients (including sender)
            ws_payload = {
                "msg_id": msg_id,
                "sender_id": _node.state.node_id,
                "sender_name": _node.state.name,
                "text": text,
                "timestamp": msg["timestamp"],
                "source": "local",
            }
            await chat_manager.broadcast(ws_payload)

            # 2. Propagate to TCP swarm peers
            msg["msg_id"] = msg_id
            await _node.conn_mgr.broadcast_to_peers(msg)

    except WebSocketDisconnect:
        chat_manager.disconnect(ws)
    except Exception as e:
        logger.debug(f"WebSocket chat error: {e}")
        try:
            chat_manager.disconnect(ws)
        except ValueError:
            pass


async def handle_tcp_chat(msg: dict):
    """
    Called by the TCP message handler when a CHAT message arrives from a peer.
    Routes it to all local WebSocket clients.
    """
    msg_id = msg.get("msg_id", "")

    # Deduplicate: don't re-broadcast messages we've already seen
    if msg_id in _seen_chat_ids:
        return
    _seen_chat_ids.add(msg_id)
    if len(_seen_chat_ids) > _MAX_SEEN:
        _seen_chat_ids.pop()

    ws_payload = {
        "msg_id": msg_id,
        "sender_id": msg.get("sender_id", ""),
        "sender_name": msg.get("sender_name", "unknown"),
        "text": msg.get("text", ""),
        "timestamp": msg.get("timestamp", time.time()),
        "source": "peer",
    }
    await chat_manager.broadcast(ws_payload)

    # Gossip: re-broadcast to our TCP peers (flood protocol)
    if _node:
        await _node.conn_mgr.broadcast_to_peers(msg)


# ── Phase 24A: Invite-based 1:1 Chat ──────────────────────────────────
#
# Wire protocol:
#   1. Node A sends invite to node B  →  POST /peer/chat/invite on B's API.
#      A stores the thread as outgoing_pending; B stores it as incoming_pending.
#   2. B accepts the invite           →  POST /peer/chat/accept on A's API.
#      Both flip to accepted.
#   3. Either side sends a message    →  POST /peer/chat/message on the other.
#      Receiver appends to its local message log (thread must be accepted).
# All peer-to-peer requests carry {from_node_id, from_name} so the recipient
# knows who originated the action; HMAC swarm-auth is the existing UDP/TCP
# guard, so HTTP peer endpoints currently trust the LAN.

import httpx as _httpx_chat

CHAT_PEER_TIMEOUT = 8


async def _resolve_peer_endpoint(peer_id: str):
    """Look up a peer's (ip, api_port, name) from the alive-peer table.
    Returns None if the peer is not currently visible on the network."""
    if not _node:
        return None
    peers = await _node.state.get_alive_peers()
    peer = peers.get(peer_id)
    if not peer:
        return None
    return peer.ip, peer.api_port, peer.name


def _self_ident() -> dict:
    return {"from_node_id": _node.state.node_id, "from_name": _node.state.name}


def _enrich_thread(t: dict, alive_ids: set, last_msg: dict | None = None) -> dict:
    """Add UI-facing fields to a thread row: online status + last-message preview."""
    out = dict(t)
    out["online"] = t["peer_id"] in alive_ids
    if last_msg:
        out["last_message"] = last_msg["body"]
        out["last_message_at"] = last_msg["sent_at"]
        out["last_message_from_self"] = bool(last_msg["from_self"])
    else:
        out["last_message"] = None
        out["last_message_at"] = None
        out["last_message_from_self"] = None
    return out


# ── UI-facing endpoints ────────────────────────────────────────────

@router.get("/chats")
async def list_chats():
    """Return every chat thread with status + online indicator + last-message preview."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")

    threads = await _local_store.db.get_all_chat_threads()
    alive = await _node.state.get_alive_peers()
    alive_ids = set(alive.keys())

    out = []
    for t in threads:
        msgs = await _local_store.db.get_chat_messages(t["peer_id"], limit=1)
        # get_chat_messages returns ascending; take the last one if any
        msgs_all = await _local_store.db.get_chat_messages(t["peer_id"], limit=500)
        last = msgs_all[-1] if msgs_all else None
        out.append(_enrich_thread(t, alive_ids, last))
    return {"chats": out, "self_node_id": _node.state.node_id}


@router.post("/chats/invite")
async def invite_to_chat(payload: dict):
    """Send a chat invite to the peer with the given peer_id."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")
    peer_id = (payload or {}).get("peer_id", "").strip()
    if not peer_id:
        raise HTTPException(400, "peer_id is required")
    if peer_id == _node.state.node_id:
        raise HTTPException(400, "Cannot invite yourself")

    target = await _resolve_peer_endpoint(peer_id)
    if not target:
        raise HTTPException(404, f"Peer {peer_id[:12]}... is not currently visible on the LAN")
    ip, api_port, peer_name = target

    # Refuse if there is already an accepted thread or an incoming invite from them
    existing = await _local_store.db.get_chat_thread(peer_id)
    if existing and existing["status"] == "accepted":
        return {"status": "noop", "reason": "thread already accepted",
                "thread": _enrich_thread(existing, {peer_id})}
    if existing and existing["status"] == "incoming_pending":
        raise HTTPException(409, "This peer already invited you — accept their invite instead")

    # Send the invite to the peer's HTTP API
    try:
        async with _httpx_chat.AsyncClient(timeout=CHAT_PEER_TIMEOUT) as client:
            r = await client.post(
                f"http://{ip}:{api_port}/peer/chat/invite", json=_self_ident()
            )
        if r.status_code >= 400:
            raise HTTPException(502, f"Peer rejected invite: HTTP {r.status_code}")
    except _httpx_chat.HTTPError as e:
        raise HTTPException(502, f"Failed to reach peer: {e}")

    # Store outgoing_pending locally
    thread = await _local_store.db.upsert_chat_thread(
        peer_id=peer_id, status="outgoing_pending",
        invited_by_self=True, peer_name=peer_name,
    )
    logger.info(f"Sent chat invite to {peer_name} ({peer_id[:12]}...)")
    return {"status": "sent", "thread": _enrich_thread(thread, {peer_id})}


@router.post("/chats/{peer_id}/accept")
async def accept_chat(peer_id: str):
    """Accept an incoming chat invite from peer_id."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")

    thread = await _local_store.db.get_chat_thread(peer_id)
    if not thread:
        raise HTTPException(404, "No invite from this peer")
    if thread["status"] != "incoming_pending":
        raise HTTPException(409, f"Cannot accept — thread is in '{thread['status']}'")

    target = await _resolve_peer_endpoint(peer_id)
    if not target:
        raise HTTPException(503, "Peer is offline — try again when they are visible")
    ip, api_port, _ = target

    try:
        async with _httpx_chat.AsyncClient(timeout=CHAT_PEER_TIMEOUT) as client:
            r = await client.post(
                f"http://{ip}:{api_port}/peer/chat/accept", json=_self_ident()
            )
        if r.status_code >= 400:
            raise HTTPException(502, f"Peer did not acknowledge: HTTP {r.status_code}")
    except _httpx_chat.HTTPError as e:
        raise HTTPException(502, f"Failed to reach peer: {e}")

    updated = await _local_store.db.upsert_chat_thread(
        peer_id=peer_id, status="accepted",
        invited_by_self=False, peer_name=thread["peer_name"],
    )
    logger.info(f"Accepted chat invite from {peer_id[:12]}...")
    return {"status": "accepted", "thread": _enrich_thread(updated, {peer_id})}


@router.post("/chats/{peer_id}/reject")
async def reject_chat(peer_id: str):
    """Reject an incoming chat invite from peer_id (best-effort notify peer)."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")

    thread = await _local_store.db.get_chat_thread(peer_id)
    if not thread:
        raise HTTPException(404, "No invite from this peer")
    if thread["status"] != "incoming_pending":
        raise HTTPException(409, f"Cannot reject — thread is in '{thread['status']}'")

    # Try to notify the peer; do not fail the local action if the peer is unreachable
    target = await _resolve_peer_endpoint(peer_id)
    if target:
        ip, api_port, _ = target
        try:
            async with _httpx_chat.AsyncClient(timeout=CHAT_PEER_TIMEOUT) as client:
                await client.post(
                    f"http://{ip}:{api_port}/peer/chat/reject", json=_self_ident()
                )
        except Exception:
            pass

    await _local_store.db.delete_chat_thread(peer_id)
    return {"status": "rejected"}


@router.post("/chats/{peer_id}/messages")
async def send_chat_message(peer_id: str, payload: dict):
    """Send a 1:1 message in an accepted thread."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")
    body = (payload or {}).get("text", "").strip()
    if not body:
        raise HTTPException(400, "text is required")

    thread = await _local_store.db.get_chat_thread(peer_id)
    if not thread or thread["status"] != "accepted":
        raise HTTPException(409, "Thread is not accepted yet")

    target = await _resolve_peer_endpoint(peer_id)
    if not target:
        raise HTTPException(503, "Peer is offline — message not delivered")
    ip, api_port, _ = target

    try:
        async with _httpx_chat.AsyncClient(timeout=CHAT_PEER_TIMEOUT) as client:
            r = await client.post(
                f"http://{ip}:{api_port}/peer/chat/message",
                json={**_self_ident(), "body": body},
            )
        if r.status_code >= 400:
            raise HTTPException(502, f"Peer rejected message: HTTP {r.status_code}")
    except _httpx_chat.HTTPError as e:
        raise HTTPException(502, f"Failed to reach peer: {e}")

    msg = await _local_store.db.append_chat_message(peer_id, from_self=True, body=body)
    return {"status": "sent", "message": msg}


@router.get("/chats/{peer_id}/messages")
async def get_chat_messages(peer_id: str, limit: int = 500):
    """Return the local message log for a thread, oldest first."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    thread = await _local_store.db.get_chat_thread(peer_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    msgs = await _local_store.db.get_chat_messages(peer_id, limit=limit)
    return {"thread": thread, "messages": msgs}


@router.delete("/chats/{peer_id}")
async def delete_chat(peer_id: str):
    """Delete a thread + all its messages (local-only; peer keeps theirs)."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    ok = await _local_store.db.delete_chat_thread(peer_id)
    if not ok:
        raise HTTPException(404, "Thread not found")
    return {"status": "deleted"}


# ── Peer-to-peer endpoints (called by other nodes) ─────────────────

@router.post("/peer/chat/invite")
async def peer_chat_invite(payload: dict):
    """Receive a chat invite from another node."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")
    sender_id = (payload or {}).get("from_node_id", "").strip()
    sender_name = (payload or {}).get("from_name", "").strip()
    if not sender_id:
        raise HTTPException(400, "from_node_id required")

    existing = await _local_store.db.get_chat_thread(sender_id)
    # If we already accepted this peer, treat invite as a no-op refresh
    if existing and existing["status"] == "accepted":
        return {"status": "already_accepted"}
    # If we already invited THEM and now they invited us, auto-accept (mutual invite)
    if existing and existing["status"] == "outgoing_pending":
        await _local_store.db.upsert_chat_thread(
            peer_id=sender_id, status="accepted",
            invited_by_self=True, peer_name=sender_name,
        )
        logger.info(f"Mutual invite — auto-accepted with {sender_id[:12]}...")
        return {"status": "auto_accepted"}

    await _local_store.db.upsert_chat_thread(
        peer_id=sender_id, status="incoming_pending",
        invited_by_self=False, peer_name=sender_name,
    )
    logger.info(f"Received chat invite from {sender_name} ({sender_id[:12]}...)")
    return {"status": "received"}


@router.post("/peer/chat/accept")
async def peer_chat_accept(payload: dict):
    """A peer we invited has accepted — flip our local thread to accepted."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    sender_id = (payload or {}).get("from_node_id", "").strip()
    sender_name = (payload or {}).get("from_name", "").strip()
    if not sender_id:
        raise HTTPException(400, "from_node_id required")

    existing = await _local_store.db.get_chat_thread(sender_id)
    if not existing or existing["status"] != "outgoing_pending":
        # Peer is acknowledging an invite we don't remember; accept it anyway so
        # we don't desync if our DB was wiped — this is a low-risk LAN tool.
        pass
    await _local_store.db.upsert_chat_thread(
        peer_id=sender_id, status="accepted",
        invited_by_self=True, peer_name=sender_name,
    )
    logger.info(f"Peer {sender_name} ({sender_id[:12]}...) accepted our invite")
    return {"status": "accepted"}


@router.post("/peer/chat/reject")
async def peer_chat_reject(payload: dict):
    """A peer rejected our invite — remove the local thread."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    sender_id = (payload or {}).get("from_node_id", "").strip()
    if not sender_id:
        raise HTTPException(400, "from_node_id required")
    await _local_store.db.delete_chat_thread(sender_id)
    return {"status": "removed"}


@router.post("/peer/chat/message")
async def peer_chat_message(payload: dict):
    """Receive a 1:1 chat message from a peer in an accepted thread."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    sender_id = (payload or {}).get("from_node_id", "").strip()
    sender_name = (payload or {}).get("from_name", "").strip()
    body = (payload or {}).get("body", "").strip()
    if not sender_id or not body:
        raise HTTPException(400, "from_node_id and body required")

    thread = await _local_store.db.get_chat_thread(sender_id)
    if not thread or thread["status"] != "accepted":
        # Reject silently from sender's perspective so they cannot probe our state
        raise HTTPException(403, "No accepted thread with this peer")

    # Keep peer_name fresh in case it changed
    if sender_name and sender_name != thread.get("peer_name", ""):
        await _local_store.db.upsert_chat_thread(
            peer_id=sender_id, status="accepted",
            invited_by_self=bool(thread["invited_by_self"]), peer_name=sender_name,
        )

    msg = await _local_store.db.append_chat_message(sender_id, from_self=False, body=body)
    return {"status": "received", "message": msg}


# ── Phase 24C: File sharing (selective download by peer ref) ──────────
#
# Sender POSTs /share with {to_peer_id, file_hashes, note}. The backend
# resolves each hash to its manifest (filename, size), then POSTs to the
# recipient's /peer/share/notify which inserts one row per file into the
# recipient's file_shares inbox. Recipient's UI lists the inbox and lets
# them download whichever ones they want via the existing /download flow.
#
# No accept/reject — receiving the share is just metadata; the recipient
# only pays bandwidth for the specific files they choose to pull.

@router.post("/share")
async def send_share(payload: dict):
    """Share a list of file hashes with a peer (requires an accepted chat thread)."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")
    to_peer = (payload or {}).get("to_peer_id", "").strip()
    file_hashes = (payload or {}).get("file_hashes") or []
    note = (payload or {}).get("note", "")
    if not to_peer:
        raise HTTPException(400, "to_peer_id required")
    if to_peer == _node.state.node_id:
        raise HTTPException(400, "Cannot share with yourself")
    if not isinstance(file_hashes, list) or not file_hashes:
        raise HTTPException(400, "file_hashes must be a non-empty list")

    # Phase 24C+: file sharing piggy-backs on the chat invite as the consent gate
    thread = await _local_store.db.get_chat_thread(to_peer)
    if not thread or thread["status"] != "accepted":
        raise HTTPException(
            403,
            "You can only share files with peers you have an accepted chat with. "
            "Send a chat invite first.",
        )

    target = await _resolve_peer_endpoint(to_peer)
    if not target:
        raise HTTPException(404, f"Peer {to_peer[:12]}... is not currently visible")
    ip, api_port, _ = target

    # Look up each manifest locally to capture filename + size for the share record
    files_payload = []
    missing = []
    for fh in file_hashes:
        m = _local_store.load_manifest(fh)
        if not m:
            missing.append(fh)
            continue
        files_payload.append({
            "file_hash": fh,
            "filename": m.get("original_filename", ""),
            "size": m.get("original_size", 0),
        })
    if missing:
        raise HTTPException(404, f"Unknown file hash(es): {', '.join(h[:12] + '...' for h in missing)}")

    # Notify the recipient
    body = {**_self_ident(), "files": files_payload, "note": note}
    try:
        async with _httpx_chat.AsyncClient(timeout=CHAT_PEER_TIMEOUT) as client:
            r = await client.post(f"http://{ip}:{api_port}/peer/share/notify", json=body)
        if r.status_code >= 400:
            raise HTTPException(502, f"Peer rejected share: HTTP {r.status_code}")
    except _httpx_chat.HTTPError as e:
        raise HTTPException(502, f"Failed to reach peer: {e}")

    logger.info(
        f"Shared {len(files_payload)} file(s) with {to_peer[:12]}..."
    )
    return {"status": "sent", "shared_count": len(files_payload)}


@router.get("/shares")
async def list_shares():
    """Return the inbox of file references shared with this node by other peers."""
    if not _node or not _local_store:
        raise HTTPException(503, "Node not initialized")
    rows = await _local_store.db.get_all_file_shares()
    alive = await _node.state.get_alive_peers()
    alive_ids = set(alive.keys())

    # Detect already-downloaded files by checking the local manifest store
    local_hashes = {
        m.get("file_hash") for m in _local_store.get_all_manifests()
    }

    out = []
    for r in rows:
        out.append({
            **r,
            "online": r["from_peer_id"] in alive_ids,
            "downloaded": r["file_hash"] in local_hashes,
        })
    return {"shares": out}


@router.delete("/shares/{share_id}")
async def delete_share(share_id: int):
    """Remove a share record from the inbox (does not delete the file itself)."""
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    ok = await _local_store.db.delete_file_share(share_id)
    if not ok:
        raise HTTPException(404, "Share not found")
    return {"status": "deleted"}


@router.post("/peer/share/notify")
async def peer_share_notify(payload: dict):
    """Receive a file-share notification from another peer.

    Gated by chat consent: we only accept shares from peers we have an
    accepted chat thread with. This makes file sharing piggy-back on the
    same invite handshake as chats — one consent covers both.
    """
    if not _local_store:
        raise HTTPException(503, "Node not initialized")
    sender_id = (payload or {}).get("from_node_id", "").strip()
    sender_name = (payload or {}).get("from_name", "").strip()
    files = (payload or {}).get("files") or []
    note = (payload or {}).get("note", "")
    if not sender_id or not files:
        raise HTTPException(400, "from_node_id and files required")

    # Phase 24C+: only accept shares from peers we have an accepted chat with
    thread = await _local_store.db.get_chat_thread(sender_id)
    if not thread or thread["status"] != "accepted":
        # Refuse uninvited shares — sender must initiate a chat first
        raise HTTPException(403, "No accepted chat thread with this peer")

    inserted = []
    for f in files:
        fh = (f.get("file_hash") or "").strip()
        if not fh:
            continue
        rec = await _local_store.db.insert_file_share(
            from_peer_id=sender_id,
            from_peer_name=sender_name,
            file_hash=fh,
            filename=f.get("filename", ""),
            size=int(f.get("size", 0) or 0),
            note=note,
        )
        inserted.append(rec["id"])

    logger.info(
        f"Received {len(inserted)} share(s) from {sender_name} ({sender_id[:12]}...)"
    )
    return {"status": "received", "inserted_ids": inserted}
