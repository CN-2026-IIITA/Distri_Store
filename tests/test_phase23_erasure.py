"""
Phase 23 — Reed-Solomon Erasure Coding Tests

Verifies:
  1. Encode → decode roundtrip with all shards present
  2. Drop any (n - k) shards, reconstruction still works
  3. Drop more than (n - k) shards → reconstruction fails cleanly
  4. Shard hash verification rejects corrupted shards
  5. fetch_and_decode_chunk works with a mock local store + peer-fetch
  6. End-to-end: encrypted file → chunk → erasure-encode → drop shards →
     fetch_and_decode → merge → original file
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
from pathlib import Path

# Ensure backend importable when running as `python -m tests.test_phase23_erasure`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.strategies.erasure import (
    encode_chunk, decode_chunk, verify_shard,
    fetch_and_decode_chunk, Shard,
)
from backend.file_engine.chunker import chunk_file, ChunkInfo, ShardInfo
from backend.file_engine.crypto import sha256_hash


# ── Test 1: roundtrip with all shards ─────────────────────────

def test_roundtrip_all_shards():
    payload = bytes(range(256)) * 40  # 10240 bytes, varied
    encoded = encode_chunk(payload, chunk_index=0)
    assert encoded.k == 6 and encoded.n == 9
    assert len(encoded.shards) == 9
    recovered = decode_chunk(encoded.shards, encoded.chunk_size)
    assert recovered == payload, "all-shards roundtrip failed"
    print("  PASS: roundtrip with all 9 shards")


# ── Test 2: drop any (n-k)=3 shards, reconstruct from any 6 ───

def test_recover_from_any_6_of_9():
    payload = b"DistriStore Reed-Solomon erasure coding test " * 200
    encoded = encode_chunk(payload, chunk_index=2)
    rng = random.Random(42)
    for trial in range(20):
        keep = rng.sample(encoded.shards, 6)
        recovered = decode_chunk(keep, encoded.chunk_size)
        assert recovered == payload, (
            f"trial {trial} failed; kept indices {[s.shard_index for s in keep]}"
        )
    print("  PASS: 20 trials of 6-of-9 reconstruction")


# ── Test 3: too few shards must fail loudly ───────────────────

def test_too_few_shards_fails():
    payload = b"x" * 8000
    encoded = encode_chunk(payload)
    try:
        decode_chunk(encoded.shards[:5], encoded.chunk_size)
        raise AssertionError("expected ValueError for fewer than k=6 shards")
    except ValueError as e:
        assert "at least 6" in str(e)
        print(f"  PASS: rejected 5-shard decode ({e})")


# ── Test 4: shard hash verification ────────────────────────────

def test_shard_hash_verification():
    payload = b"verify-me" * 500
    encoded = encode_chunk(payload)
    for s in encoded.shards:
        assert verify_shard(s.data, s.shard_hash)
    # Corrupted shard must fail verification
    bad = encoded.shards[0].data[:-1] + bytes([encoded.shards[0].data[-1] ^ 0xFF])
    assert not verify_shard(bad, encoded.shards[0].shard_hash)
    print("  PASS: hash verification accepts good shards, rejects corrupted")


# ── Test 5: fetch_and_decode_chunk against a fake store + peer ─

class _FakeStore:
    """Hash-keyed in-memory store mimicking LocalStore.load_chunk/save_chunk."""
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def load_chunk(self, h):
        return self.data.get(h)

    def save_chunk(self, h, b):
        self.data[h] = b


async def _async_fetch_and_decode_test():
    # Use random payload so every shard hash is unique (a repetitive payload
    # can produce identical shards across positions, which would let pass 1
    # pick everything up locally and never exercise the peer fetch path).
    payload = os.urandom(12000)
    encoded = encode_chunk(payload, chunk_index=7)

    # Build the manifest-side ChunkInfo + ShardInfo
    chunk_info = ChunkInfo(
        index=7,
        chunk_hash=sha256_hash(payload),
        size=len(payload),
        encrypted=False,
    )
    chunk_info.shards = [
        ShardInfo(shard_index=s.shard_index, shard_hash=s.shard_hash, size=s.size)
        for s in encoded.shards
    ]

    # Local store has shards 0, 1, 2 only (3 shards locally — need 3 more from peers)
    local = _FakeStore()
    peer  = _FakeStore()
    for i, s in enumerate(encoded.shards):
        (local if i < 3 else peer).data[s.shard_hash] = s.data

    async def peer_fetch(h):
        return peer.load_chunk(h)

    recovered = await fetch_and_decode_chunk(chunk_info, k=6, n=9,
                                              local_store=local,
                                              peer_fetch_fn=peer_fetch)
    assert recovered == payload, "fetch_and_decode failed"
    # The 3 peer-fetched shards should now be cached locally
    assert len(local.data) == 6, f"expected 6 cached shards, got {len(local.data)}"
    print(f"  PASS: fetch_and_decode_chunk reconstructed from 3 local + 3 peer shards")


def test_fetch_and_decode_chunk():
    asyncio.run(_async_fetch_and_decode_test())


# ── Test 6: END-TO-END — chunk a file, erasure-encode, drop shards, restore ──

async def _async_end_to_end_test():
    """
    Take a real file, chunk it, erasure-encode each chunk, simulate losing 3 of 9
    shards per chunk by storing the rest in 'peer' fake stores, then reconstruct.
    """
    # Create a 200KB test file (multiple chunks given default 256KB threshold,
    # let's force a smaller chunk size to get multiple chunks).
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    payload = os.urandom(200 * 1024)  # 200 KB random
    tmp.write(payload)
    tmp.close()

    try:
        # Use small chunk_size to get multiple chunks
        manifest, chunks = chunk_file(tmp.name, chunk_size=64 * 1024, password=None)
        n_chunks = len(chunks)
        assert n_chunks >= 3, f"want at least 3 chunks for a real test, got {n_chunks}"

        local = _FakeStore()
        peer = _FakeStore()

        # Erasure-encode every chunk; spread shards: 3 local, 6 peer (worst-case mix)
        for idx, (info, data) in enumerate(zip(manifest.chunks, chunks)):
            encoded = encode_chunk(data, chunk_index=info.index, k=6, n=9)
            info.shards = [
                ShardInfo(shard_index=s.shard_index, shard_hash=s.shard_hash, size=s.size)
                for s in encoded.shards
            ]
            # Drop a random 3 shards entirely (gone — not in any store)
            rng = random.Random(idx)
            dropped = set(rng.sample(range(9), 3))
            for i, s in enumerate(encoded.shards):
                if i in dropped:
                    continue
                # Half land local, half on peer
                target = local if (i % 2 == 0) else peer
                target.data[s.shard_hash] = s.data

        manifest.replication_mode = "erasure"
        manifest.erasure_k = 6
        manifest.erasure_n = 9

        async def peer_fetch(h):
            return peer.load_chunk(h)

        # Reconstruct each chunk through the same code path the API uses
        reconstructed = []
        for info in manifest.chunks:
            chunk_bytes = await fetch_and_decode_chunk(
                info, manifest.erasure_k, manifest.erasure_n,
                local_store=local, peer_fetch_fn=peer_fetch,
            )
            reconstructed.append(chunk_bytes)

        # Each reconstructed chunk must hash to its manifest entry's chunk_hash
        for info, data in zip(manifest.chunks, reconstructed):
            assert sha256_hash(data) == info.chunk_hash, (
                f"chunk {info.index} hash mismatch after reconstruction"
            )

        # Now do the same final-merge the API does — it must yield the original file
        from backend.file_engine.chunker import merge_chunks
        merged = merge_chunks(manifest, reconstructed, password=None)
        assert merged == payload, "final merged file does not equal original"
        print(f"  PASS: end-to-end {n_chunks}-chunk file roundtrip "
              f"(3 of 9 shards dropped per chunk)")
    finally:
        os.unlink(tmp.name)


def test_end_to_end():
    asyncio.run(_async_end_to_end_test())


# ── Runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Phase 23 — Reed-Solomon erasure coding tests")
    print("-" * 60)
    test_roundtrip_all_shards()
    test_recover_from_any_6_of_9()
    test_too_few_shards_fails()
    test_shard_hash_verification()
    test_fetch_and_decode_chunk()
    test_end_to_end()
    print("-" * 60)
    print("ALL ERASURE TESTS PASSED")
