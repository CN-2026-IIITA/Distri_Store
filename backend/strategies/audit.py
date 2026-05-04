"""
DistriStore — Phase 25B: Proof-of-Storage Audits

The auditor periodically picks a (peer, chunk_hash) pair where:
  - we have the chunk locally (so we can independently verify the proof), and
  - the peer is supposed to be holding that chunk (per the routing table).

The audit itself is a simple challenge-response:

    Auditor → Peer:   POST /peer/audit/{chunk_hash}  body={"nonce": <hex>}
    Peer    → Auditor: {"proof": SHA256(chunk_bytes || nonce_bytes)}

    Auditor recomputes the proof from its own copy of the chunk and compares.

Pass         → reputation up.
Fail (404)   → peer claimed to hold it but doesn't.
Fail (mismatch) → peer's bytes are corrupted or tampered.

Every result is appended to peer_audits in SQLite so the UI can show a
running scoreboard of how reliable each peer has been historically.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
from typing import Optional

from backend.utils.logger import get_logger

logger = get_logger("strategies.audit")


def compute_proof(chunk_bytes: bytes, nonce_hex: str) -> str:
    """Compute the audit proof: SHA256(chunk || nonce_bytes) as hex."""
    nonce = bytes.fromhex(nonce_hex)
    return hashlib.sha256(chunk_bytes + nonce).hexdigest()


def fresh_nonce() -> str:
    return os.urandom(16).hex()


async def run_one_audit(
    state,
    routing,
    local_store,
    db,
    httpx_client_cls,
    target_peer_id: Optional[str] = None,
) -> Optional[dict]:
    """Run a single audit. If target_peer_id is None, picks a random alive peer.

    Returns the audit log row, or None if no eligible (peer, chunk) pair was
    available (e.g. we have no chunks locally yet).
    """
    alive = await state.get_alive_peers()
    if not alive:
        return None

    # Pick a peer to audit
    peer_id = target_peer_id or random.choice(list(alive.keys()))
    peer = alive.get(peer_id)
    if peer is None:
        logger.debug(f"Audit target {peer_id[:12]}... is offline")
        return None

    # Find a chunk we hold locally that the routing table says this peer holds too.
    # We require BOTH conditions so a fail is unambiguous: a 404 from the peer
    # means they cheated (deleted a chunk they were assigned), not "we asked
    # about a chunk they were never supposed to have."
    local_hashes = set(local_store.list_chunks())
    chunks_for_peer = []
    routing_chunks = getattr(routing, "_table", None) or {}
    for ch, holders in routing_chunks.items():
        if ch in local_hashes and peer_id in holders:
            chunks_for_peer.append(ch)

    if not chunks_for_peer:
        # No co-held chunk → nothing meaningful to audit yet. Skip silently.
        return None
    chunk_hash = random.choice(chunks_for_peer)

    nonce = fresh_nonce()
    chunk_bytes = local_store.load_chunk(chunk_hash)
    if chunk_bytes is None:
        return None
    expected = compute_proof(chunk_bytes, nonce)

    url = f"http://{peer.ip}:{peer.api_port}/peer/audit/{chunk_hash}"
    proof_received = ""
    result = "fail"
    error = ""
    try:
        async with httpx_client_cls(timeout=8) as client:
            r = await client.post(url, json={"nonce": nonce})
        if r.status_code == 404:
            result = "fail"
            error = "peer returned 404 — chunk not held"
        elif r.status_code >= 400:
            result = "error"
            error = f"HTTP {r.status_code}"
        else:
            proof_received = (r.json() or {}).get("proof", "")
            result = "pass" if proof_received == expected else "fail"
            if result == "fail" and proof_received:
                error = "proof mismatch — chunk bytes differ"
    except Exception as e:
        result = "error"
        error = f"{type(e).__name__}: {e}"

    rec = await db.insert_audit(
        peer_id=peer_id, peer_name=peer.name, chunk_hash=chunk_hash,
        nonce=nonce, proof_received=proof_received, expected_proof=expected,
        result=result, error=error,
    )
    logger.info(
        f"Audit {peer.name} ({peer_id[:12]}...) chunk {chunk_hash[:12]}... -> {result}"
        + (f" ({error})" if error else "")
    )
    return rec


async def auditor_loop(state, routing, local_store, db, httpx_client_cls,
                       interval_seconds: int = 30):
    """Background task: every interval_seconds, pick a random alive peer and audit them."""
    logger.info(f"Auditor loop started (interval={interval_seconds}s)")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await run_one_audit(state, routing, local_store, db, httpx_client_cls)
        except asyncio.CancelledError:
            logger.info("Auditor loop cancelled")
            raise
        except Exception as e:
            logger.warning(f"Auditor iteration failed: {e}")
