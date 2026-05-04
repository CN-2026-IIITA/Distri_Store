"""
DistriStore — Phase 23: Reed-Solomon Erasure Coding

Each chunk is split into n shards (k data + (n-k) parity) using Reed-Solomon
codes via the zfec library. Any k of n shards is sufficient to reconstruct
the original chunk.

Default profile: k=6, n=9
  - 1.5x storage overhead (vs 3x for k-copy replication)
  - Survives the simultaneous loss of any 3 of 9 shards
  - Same fault tolerance as k=3 replication, but storage-efficient

Shard layout on disk:
  - Each shard is stored under its own SHA-256 hash via LocalStore.save_chunk()
  - Shard hashes + (k, n, index, original_chunk_size) recorded in the manifest
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import zfec

from backend.file_engine.crypto import sha256_hash
from backend.utils.logger import get_logger

logger = get_logger("strategies.erasure")


# ── Defaults ───────────────────────────────────────────────────

DEFAULT_K = 6   # data shards required to reconstruct
DEFAULT_N = 9   # total shards produced (n - k = parity)


# ── Data classes ───────────────────────────────────────────────

@dataclass
class Shard:
    """One Reed-Solomon shard belonging to a parent chunk."""
    chunk_index: int       # which chunk in the file this shard belongs to
    shard_index: int       # 0..n-1, identifies position for decode
    shard_hash: str        # SHA-256 of shard bytes (the storage key)
    size: int              # length of shard bytes
    data: Optional[bytes] = None  # shard bytes (in-memory only, not persisted in manifest)


@dataclass
class EncodedChunk:
    """Result of erasure-encoding a single chunk."""
    chunk_index: int
    chunk_hash: str        # hash of the original (encrypted) chunk bytes — keeps existing manifest semantics
    chunk_size: int        # original chunk size in bytes (needed to trim padding on decode)
    k: int
    n: int
    shards: List[Shard] = field(default_factory=list)

    def to_manifest_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "chunk_hash": self.chunk_hash,
            "chunk_size": self.chunk_size,
            "k": self.k,
            "n": self.n,
            "shards": [
                {"shard_index": s.shard_index, "shard_hash": s.shard_hash, "size": s.size}
                for s in self.shards
            ],
        }

    @classmethod
    def from_manifest_dict(cls, d: dict) -> "EncodedChunk":
        ec = cls(
            chunk_index=d["chunk_index"],
            chunk_hash=d["chunk_hash"],
            chunk_size=d["chunk_size"],
            k=d["k"],
            n=d["n"],
        )
        for s in d.get("shards", []):
            ec.shards.append(Shard(
                chunk_index=ec.chunk_index,
                shard_index=s["shard_index"],
                shard_hash=s["shard_hash"],
                size=s["size"],
            ))
        return ec


# ── Encode ─────────────────────────────────────────────────────

def encode_chunk(chunk_bytes: bytes, chunk_index: int = 0,
                 k: int = DEFAULT_K, n: int = DEFAULT_N) -> EncodedChunk:
    """
    Split one chunk into n Reed-Solomon shards. Any k shards reconstruct.

    The chunk is right-padded with zeros to a multiple of k, then split into
    k equal-size data blocks. zfec produces n shards (k data + (n-k) parity).

    Args:
        chunk_bytes: The chunk to encode (already encrypted/compressed).
        chunk_index: Index of this chunk inside its parent file's manifest.
        k: Number of data shards (default 6).
        n: Total shards produced (default 9). Must satisfy 1 <= k <= n <= 256.

    Returns:
        EncodedChunk with n shards. Original chunk_size stored so the decoder
        can trim trailing padding.
    """
    if not (1 <= k <= n <= 256):
        raise ValueError(f"Invalid (k,n)=({k},{n}); require 1 <= k <= n <= 256")
    if not chunk_bytes:
        raise ValueError("Cannot erasure-encode empty chunk")

    chunk_size = len(chunk_bytes)
    block_size = math.ceil(chunk_size / k)
    padded_size = block_size * k

    # Pre-build one bytes object that's a multiple of k, then split
    if padded_size > chunk_size:
        padded = chunk_bytes + b"\x00" * (padded_size - chunk_size)
    else:
        padded = chunk_bytes
    blocks = [padded[i * block_size:(i + 1) * block_size] for i in range(k)]

    encoder = zfec.Encoder(k, n)
    raw_shards = encoder.encode(blocks)

    chunk_hash = sha256_hash(chunk_bytes)
    encoded = EncodedChunk(
        chunk_index=chunk_index,
        chunk_hash=chunk_hash,
        chunk_size=chunk_size,
        k=k,
        n=n,
    )

    for idx, shard_bytes in enumerate(raw_shards):
        encoded.shards.append(Shard(
            chunk_index=chunk_index,
            shard_index=idx,
            shard_hash=sha256_hash(shard_bytes),
            size=len(shard_bytes),
            data=shard_bytes,
        ))

    return encoded


# ── Decode ─────────────────────────────────────────────────────

def decode_chunk(shards: Sequence[Shard], chunk_size: int,
                 k: int = DEFAULT_K, n: int = DEFAULT_N) -> bytes:
    """
    Reconstruct the original chunk from any k shards.

    Args:
        shards: At least k Shard instances (each must have .data populated).
                Order does not matter; shard_index is read from each Shard.
        chunk_size: Original chunk size, used to trim trailing zero padding.
        k: Data-shards parameter the chunk was encoded with.
        n: Total-shards parameter the chunk was encoded with.

    Returns:
        Original chunk bytes (len == chunk_size).
    """
    if len(shards) < k:
        raise ValueError(f"Need at least {k} shards to reconstruct, got {len(shards)}")

    # zfec wants exactly k blocks + their original indices
    selected = list(shards)[:k]
    block_data = [s.data for s in selected]
    block_idx = [s.shard_index for s in selected]

    if any(b is None for b in block_data):
        raise ValueError("Shards missing .data — load shard bytes before decoding")

    decoder = zfec.Decoder(k, n)
    recovered_blocks = decoder.decode(block_data, block_idx)
    full = b"".join(recovered_blocks)
    return full[:chunk_size]


# ── Verification ───────────────────────────────────────────────

def verify_shard(shard_bytes: bytes, expected_hash: str) -> bool:
    """Check that a shard's SHA-256 matches the expected hash from the manifest."""
    return sha256_hash(shard_bytes) == expected_hash


# ── Async chunk reconstruction (download path) ────────────────

async def fetch_and_decode_chunk(chunk_info, k: int, n: int,
                                  local_store, peer_fetch_fn) -> bytes:
    """
    Fetch enough shards (any k of n) and decode them back into the original chunk.

    Tries every shard locally first, then asks peers for the rest, in shard-index
    order. Stops as soon as k shards are in hand.

    Args:
        chunk_info: ChunkInfo with .shards populated and .index, .chunk_hash set.
        k, n: erasure parameters from the parent manifest.
        local_store: LocalStore-like object with .load_chunk(hash) -> bytes|None.
        peer_fetch_fn: async callable(shard_hash) -> bytes|None.

    Returns:
        Reconstructed chunk bytes (verified against chunk_info.chunk_hash).
    """
    if not chunk_info.shards:
        raise ValueError(f"Chunk {chunk_info.index} has no shard metadata")
    if len(chunk_info.shards) != n:
        logger.warning(
            f"Chunk {chunk_info.index} manifest has {len(chunk_info.shards)} "
            f"shards but expected n={n}; proceeding with what's there"
        )

    shards_meta = list(chunk_info.shards)
    collected: List[Shard] = []

    # Pass 1: try local store for each shard — cheap
    remaining_meta: List = []
    for sm in shards_meta:
        if len(collected) >= k:
            break
        data = local_store.load_chunk(sm.shard_hash)
        if data is None:
            remaining_meta.append(sm)
            continue
        if not verify_shard(data, sm.shard_hash):
            logger.warning(f"Local shard {sm.shard_hash[:12]}... hash mismatch — refetching")
            remaining_meta.append(sm)
            continue
        collected.append(Shard(
            chunk_index=chunk_info.index,
            shard_index=sm.shard_index,
            shard_hash=sm.shard_hash,
            size=sm.size,
            data=data,
        ))

    # Pass 2: fetch remaining from peers, one by one, stop at k
    for sm in remaining_meta:
        if len(collected) >= k:
            break
        data = await peer_fetch_fn(sm.shard_hash)
        if data is None:
            continue
        if not verify_shard(data, sm.shard_hash):
            logger.warning(f"Peer shard {sm.shard_hash[:12]}... hash mismatch — skipping")
            continue
        # Cache fetched shard locally so future reads are free
        local_store.save_chunk(sm.shard_hash, data)
        collected.append(Shard(
            chunk_index=chunk_info.index,
            shard_index=sm.shard_index,
            shard_hash=sm.shard_hash,
            size=sm.size,
            data=data,
        ))

    if len(collected) < k:
        raise ValueError(
            f"Chunk {chunk_info.index}: only {len(collected)}/{k} shards available "
            f"after exhausting local + peer sources — chunk unrecoverable"
        )

    # Decode and verify against the original chunk hash recorded in the manifest
    chunk_bytes = decode_chunk(collected[:k], chunk_info.size, k=k, n=n)
    if sha256_hash(chunk_bytes) != chunk_info.chunk_hash:
        raise ValueError(
            f"Chunk {chunk_info.index}: reconstructed bytes hash mismatch — "
            f"expected {chunk_info.chunk_hash[:16]}..."
        )
    logger.debug(
        f"Reconstructed chunk {chunk_info.index} from "
        f"{len(collected)} shards (need {k} of {n})"
    )
    return chunk_bytes
