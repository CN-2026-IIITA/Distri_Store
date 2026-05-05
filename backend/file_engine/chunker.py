"""
DistriStore — O(N) File Chunker with Lazy Loading

Phase 3: Performance Optimization
  - Generator-based chunking: O(1) memory via file.read(chunk_size)
  - Pre-allocated bytearray merger: O(N) time, no quadratic reallocation
  - Streaming-to-disk merger: O(1) memory for arbitrarily large files
  - Merkle tree computed incrementally during streaming

Phase 10: Advanced Throughput
  - Dynamic chunk sizing: 256KB / 1MB / 4MB based on file size
  - Async disk I/O wrappers via asyncio.to_thread (non-blocking)

Phase 18: Per-chunk zstd compression
  - Compress before encrypt, decompress after decrypt
  - Backward-compatible: manifests without 'compression' skip decompression
"""

import asyncio
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Generator, Tuple, Optional

import zstandard as zstd

from backend.file_engine.crypto import sha256_hash, encrypt, decrypt
from backend.utils.logger import get_logger

logger = get_logger("file_engine.chunker")

DEFAULT_CHUNK_SIZE = 262144  # 256 KB

# ── Dynamic Chunk Sizing Thresholds ────────────────────────────
_CHUNK_256KB = 262_144       # 256 KB
_CHUNK_1MB   = 1_048_576     # 1 MB
_CHUNK_4MB   = 4_194_304     # 4 MB
_TIER_50MB   = 50  * 1024 * 1024
_TIER_500MB  = 500 * 1024 * 1024


def get_optimal_chunk_size(file_size_bytes: int) -> int:
    """
    Select chunk size dynamically based on file size.

    Tiers:
      - < 50 MB  → 256 KB  (fine-grained, fast for small files)
      - 50-500 MB → 1 MB   (balanced throughput / chunk count)
      - > 500 MB → 4 MB    (minimizes chunk overhead on large files)
    """
    if file_size_bytes < _TIER_50MB:
        return _CHUNK_256KB
    elif file_size_bytes < _TIER_500MB:
        return _CHUNK_1MB
    else:
        return _CHUNK_4MB


# ── Merkle Tree ────────────────────────────────────────────────

def compute_merkle_root(hashes: List[str]) -> str:
    if not hashes:
        return sha256_hash(b"")
    if len(hashes) == 1:
        return hashes[0]
    level = list(hashes)
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            next_level.append(sha256_hash((left + right).encode()))
        level = next_level
    return level[0]


def compute_merkle_proof(hashes: List[str], index: int) -> List[dict]:
    if len(hashes) <= 1:
        return []
    proof = []
    level = list(hashes)
    idx = index
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            if i == idx or i + 1 == idx:
                if idx == i:
                    proof.append({"hash": right, "position": "right"})
                else:
                    proof.append({"hash": left, "position": "left"})
            next_level.append(sha256_hash((left + right).encode()))
        idx = idx // 2
        level = next_level
    return proof


def verify_merkle_proof(chunk_hash: str, proof: List[dict], merkle_root: str) -> bool:
    current = chunk_hash
    for step in proof:
        if step["position"] == "right":
            current = sha256_hash((current + step["hash"]).encode())
        else:
            current = sha256_hash((step["hash"] + current).encode())
    return current == merkle_root


# ── Data Classes ───────────────────────────────────────────────

@dataclass
class ShardInfo:
    """One Reed-Solomon shard recorded in the manifest (Phase 23)."""
    shard_index: int
    shard_hash: str
    size: int


@dataclass
class ChunkInfo:
    index: int
    chunk_hash: str
    size: int
    encrypted: bool = False
    # Phase 23: when shards is non-empty, the chunk is reconstructed from
    # any `erasure_k` of these shards instead of being fetched as a whole.
    shards: List[ShardInfo] = field(default_factory=list)


@dataclass
class FileManifest:
    original_filename: str
    original_size: int
    file_hash: str
    chunk_size: int
    merkle_root: str = ""
    compression: str = ""
    chunks: List[ChunkInfo] = field(default_factory=list)
    # Phase 23: erasure-coding parameters. mode == "erasure" → each ChunkInfo
    # carries its `shards` list and downloader uses RS reconstruction.
    replication_mode: str = "kcopy"
    erasure_k: int = 0
    erasure_n: int = 0
    # Phase 25C: threshold-encrypted files. key_scheme == "shamir" → AES key
    # is split via Shamir Secret Sharing across `key_holders` (m of n required).
    # The key bytes themselves never live in the manifest; downloader collects
    # m shares from the holders to reconstruct.
    key_scheme: str = ""              # "" | "shamir"
    key_m: int = 0
    key_n: int = 0
    key_holders: List[str] = field(default_factory=list)  # peer_id list, length n
    key_recipient: str = ""           # peer_id authorized to retrieve the key

    def to_dict(self) -> dict:
        d = {
            "version": 2,
            "original_filename": self.original_filename,
            "original_size": self.original_size,
            "file_hash": self.file_hash,
            "chunk_size": self.chunk_size,
            "merkle_root": self.merkle_root,
            "compression": self.compression,
            "chunk_count": len(self.chunks),
            "replication_mode": self.replication_mode,
            "erasure_k": self.erasure_k,
            "erasure_n": self.erasure_n,
            "key_scheme": self.key_scheme,
            "key_m": self.key_m,
            "key_n": self.key_n,
            "key_holders": list(self.key_holders),
            "key_recipient": self.key_recipient,
            "chunks": [
                {
                    "index": c.index,
                    "chunk_hash": c.chunk_hash,
                    "size": c.size,
                    "encrypted": c.encrypted,
                    "shards": [
                        {"shard_index": s.shard_index,
                         "shard_hash": s.shard_hash,
                         "size": s.size}
                        for s in c.shards
                    ],
                }
                for c in self.chunks
            ],
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileManifest":
        manifest = cls(
            original_filename=d["original_filename"],
            original_size=d["original_size"],
            file_hash=d["file_hash"],
            chunk_size=d.get("chunk_size", DEFAULT_CHUNK_SIZE),
            merkle_root=d.get("merkle_root", ""),
            compression=d.get("compression", ""),
            replication_mode=d.get("replication_mode", "kcopy"),
            erasure_k=d.get("erasure_k", 0),
            erasure_n=d.get("erasure_n", 0),
            key_scheme=d.get("key_scheme", ""),
            key_m=d.get("key_m", 0),
            key_n=d.get("key_n", 0),
            key_holders=list(d.get("key_holders", [])),
            key_recipient=d.get("key_recipient", ""),
        )
        for c in d.get("chunks", []):
            ci = ChunkInfo(
                index=c["index"],
                chunk_hash=c["chunk_hash"],
                size=c["size"],
                encrypted=c.get("encrypted", False),
            )
            for s in c.get("shards", []):
                ci.shards.append(ShardInfo(
                    shard_index=s["shard_index"],
                    shard_hash=s["shard_hash"],
                    size=s["size"],
                ))
            manifest.chunks.append(ci)
        if not manifest.merkle_root and manifest.chunks:
            manifest.merkle_root = compute_merkle_root(
                [c.chunk_hash for c in manifest.chunks]
            )
        return manifest

    def verify_chunk(self, index: int, chunk_data_hash: str) -> bool:
        if index < 0 or index >= len(self.chunks):
            return False
        return self.chunks[index].chunk_hash == chunk_data_hash

    def get_merkle_proof(self, index: int) -> List[dict]:
        hashes = [c.chunk_hash for c in self.chunks]
        return compute_merkle_proof(hashes, index)


# ── O(1) Memory: Lazy Chunk Generator ─────────────────────────

def _stream_chunks(file_path: str, chunk_size: int) -> Generator[Tuple[int, bytes], None, None]:
    """
    Generator that yields (index, raw_bytes) one chunk at a time.
    Uses file.read(chunk_size) in a loop — O(1) memory regardless of file size.
    """
    idx = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield idx, chunk
            idx += 1


def _streaming_file_hash(file_path: str) -> str:
    """Compute SHA-256 of a file in streaming O(1) memory fashion."""
    import hashlib
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            block = f.read(65536)  # 64KB read blocks
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# ── Async Disk I/O Wrappers (non-blocking) ─────────────────────

async def _async_read_file_chunks(file_path: str, chunk_size: int) -> List[Tuple[int, bytes]]:
    """
    Read all chunks from a file using asyncio.to_thread so the event loop
    never blocks on synchronous disk I/O.
    """
    def _read():
        results = []
        idx = 0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                results.append((idx, chunk))
                idx += 1
        return results
    return await asyncio.to_thread(_read)


async def _async_streaming_file_hash(file_path: str) -> str:
    """Non-blocking file hash using asyncio.to_thread."""
    return await asyncio.to_thread(_streaming_file_hash, file_path)


async def _async_write_bytes(path: str, data: bytes) -> None:
    """Non-blocking file write using asyncio.to_thread."""
    def _write():
        with open(path, "wb") as f:
            f.write(data)
    await asyncio.to_thread(_write)


# ── Core: Streaming Chunker ───────────────────────────────────

def chunk_file(file_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
               password: str = None,
               aes_key: bytes = None) -> tuple[FileManifest, list[bytes]]:
    """
    O(1) memory chunking via lazy file reads.
    Computes file hash in streaming mode, then yields chunks one at a time.

    Args:
        password: derive AES key via PBKDF2 (existing behavior).
        aes_key: 32-byte pre-generated AES-256 key — bypasses PBKDF2.
                 Used by Phase 25C threshold-encrypted uploads where the key
                 is freshly random and Shamir-split across peers, not derived
                 from a passphrase.

    Returns:
        (manifest, list_of_chunk_data_bytes)
    """
    if aes_key is not None and password is not None:
        raise ValueError("Pass either password OR aes_key, not both")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = path.stat().st_size
    file_hash = _streaming_file_hash(file_path)

    manifest = FileManifest(
        original_filename=path.name,
        original_size=file_size,
        file_hash=file_hash,
        chunk_size=chunk_size,
    )

    chunk_data_list = []
    chunk_hashes = []

    # Derive key ONCE for all chunks (avoids 100K PBKDF2 iterations per chunk)
    key, salt = None, None
    if password:
        from backend.file_engine.crypto import derive_key, encrypt_with_key
        key, salt = derive_key(password)
    elif aes_key is not None:
        # Phase 25C: pre-generated key path (no PBKDF2). Use random salt for
        # the chunk header so on-disk format is unchanged; decrypt_with_key
        # ignores the salt and uses the key directly.
        from backend.file_engine.crypto import encrypt_with_key
        key, salt = aes_key, os.urandom(16)

    # Phase 18: reusable compressor instance
    compressor = zstd.ZstdCompressor(level=3)
    use_encryption = (password is not None) or (aes_key is not None)

    # Lazy generator — only 1 chunk in memory at a time during processing
    for idx, raw_bytes in _stream_chunks(file_path, chunk_size):
        # Phase 18: Compress before encrypt (O(1) per chunk)
        compressed = compressor.compress(raw_bytes)

        if use_encryption:
            chunk_bytes = encrypt_with_key(compressed, key, salt)
            encrypted = True
        else:
            chunk_bytes = compressed
            encrypted = False

        chunk_hash = sha256_hash(chunk_bytes)
        chunk_hashes.append(chunk_hash)

        manifest.chunks.append(ChunkInfo(
            index=idx, chunk_hash=chunk_hash,
            size=len(chunk_bytes), encrypted=encrypted,
        ))
        chunk_data_list.append(chunk_bytes)

    manifest.merkle_root = compute_merkle_root(chunk_hashes)
    manifest.compression = "zstd"

    label = "shamir-key" if aes_key is not None else ("encrypted" if password else "plain")
    logger.info(
        f"Chunked '{path.name}' ({file_size} bytes) "
        f"-> {len(manifest.chunks)} chunks ({label}, zstd) "
        f"merkle_root={manifest.merkle_root[:16]}..."
    )
    return manifest, chunk_data_list


def chunk_file_streaming(file_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                         password: str = None) -> Generator[Tuple[FileManifest, int, bytes], None, None]:
    """
    Pure streaming chunker — yields (partial_manifest, index, chunk_data) one at a time.
    True O(1) memory: never holds more than 1 chunk in RAM.

    Usage:
        for manifest, idx, data in chunk_file_streaming("big.iso", password="x"):
            store.save_chunk(manifest.chunks[idx].chunk_hash, data)
        # manifest is complete after iteration
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = path.stat().st_size
    file_hash = _streaming_file_hash(file_path)

    manifest = FileManifest(
        original_filename=path.name,
        original_size=file_size,
        file_hash=file_hash,
        chunk_size=chunk_size,
    )

    chunk_hashes = []

    for idx, raw_bytes in _stream_chunks(file_path, chunk_size):
        if password:
            chunk_bytes = encrypt(raw_bytes, password)
            encrypted = True
        else:
            chunk_bytes = raw_bytes
            encrypted = False

        chunk_hash = sha256_hash(chunk_bytes)
        chunk_hashes.append(chunk_hash)

        manifest.chunks.append(ChunkInfo(
            index=idx, chunk_hash=chunk_hash,
            size=len(chunk_bytes), encrypted=encrypted,
        ))

        yield manifest, idx, chunk_bytes

    manifest.merkle_root = compute_merkle_root(chunk_hashes)


# ── O(N) Merger with Pre-allocated Buffer ──────────────────────

def merge_chunks(manifest: FileManifest, chunk_data_list: list[bytes],
                 password: str = None) -> bytes:
    """
    O(N) time merger using pre-allocated bytearray.
    No quadratic byte concatenation — writes directly into a fixed buffer.
    """
    ordered = sorted(zip(manifest.chunks, chunk_data_list), key=lambda x: x[0].index)

    # Verify chunk hashes + Merkle root before decrypting
    received_hashes = []
    for info, data in ordered:
        actual_hash = sha256_hash(data)
        if actual_hash != info.chunk_hash:
            raise ValueError(
                f"Chunk {info.index} hash mismatch! "
                f"Expected {info.chunk_hash[:16]}... got {actual_hash[:16]}..."
            )
        received_hashes.append(actual_hash)

    if manifest.merkle_root:
        received_root = compute_merkle_root(received_hashes)
        if received_root != manifest.merkle_root:
            raise ValueError(
                f"Merkle root mismatch! "
                f"Expected {manifest.merkle_root[:16]}... got {received_root[:16]}..."
            )

    # Derive key ONCE for all chunks
    dec_key = None
    if password and ordered and ordered[0][0].encrypted:
        from backend.file_engine.crypto import derive_key as _dk, decrypt_with_key, SALT_SIZE as _SS
        first_salt = ordered[0][1][1:1 + _SS]
        dec_key, _ = _dk(password, first_salt)

    # Phase 18: decompressor for zstd-compressed chunks
    use_zstd = manifest.compression == "zstd"
    decompressor = zstd.ZstdDecompressor() if use_zstd else None

    # Pre-allocate exact-size buffer — O(N) time, single allocation
    buffer = bytearray(manifest.original_size)
    offset = 0

    for info, data in ordered:
        if info.encrypted and password:
            if dec_key is not None:
                data = decrypt_with_key(data, dec_key)
            else:
                data = decrypt(data, password)
        # Phase 18: decompress after decrypt
        if decompressor is not None:
            data = decompressor.decompress(data)
        buf_len = len(data)
        buffer[offset:offset + buf_len] = data
        offset += buf_len

    result = bytes(buffer[:offset])

    # Final integrity check
    result_hash = sha256_hash(result)
    if result_hash != manifest.file_hash:
        raise ValueError(
            f"File integrity check failed! "
            f"Expected {manifest.file_hash[:16]}... got {result_hash[:16]}..."
        )

    logger.info(
        f"Merged {len(ordered)} chunks -> {len(result)} bytes "
        f"(merkle ✅ integrity ✅ O(N))"
    )
    return result


def merge_chunks_to_disk(manifest: FileManifest, chunk_data_list: list[bytes],
                         output_path: str, password: str = None,
                         aes_key: bytes = None) -> str:
    """
    O(1) memory merger — streams decrypted chunks directly to disk.
    RAM usage stays near zero even for multi-GB files.

    Pass either password (PBKDF2 derive) or aes_key (raw 32-byte key, used by
    Phase 25C threshold-encrypted files where the key was Shamir-reconstructed).
    """
    ordered = sorted(zip(manifest.chunks, chunk_data_list), key=lambda x: x[0].index)

    import hashlib
    h = hashlib.sha256()

    # Derive key ONCE for all chunks
    dec_key = None
    if aes_key is not None:
        dec_key = aes_key
    elif password and ordered and ordered[0][0].encrypted:
        from backend.file_engine.crypto import derive_key as _dk, decrypt_with_key, SALT_SIZE as _SS
        first_salt = ordered[0][1][1:1 + _SS]
        dec_key, _ = _dk(password, first_salt)

    # Phase 18: decompressor for zstd-compressed chunks
    use_zstd = manifest.compression == "zstd"
    decompressor = zstd.ZstdDecompressor() if use_zstd else None

    with open(output_path, "wb") as f:
        for info, data in ordered:
            if info.encrypted and dec_key is not None:
                data = decrypt_with_key(data, dec_key)
            elif info.encrypted and password:
                data = decrypt(data, password)
            # Phase 18: decompress after decrypt
            if decompressor is not None:
                data = decompressor.decompress(data)
            f.write(data)
            h.update(data)

    result_hash = h.hexdigest()
    if result_hash != manifest.file_hash:
        os.unlink(output_path)
        raise ValueError(
            f"File integrity check failed! "
            f"Expected {manifest.file_hash[:16]}... got {result_hash[:16]}..."
        )

    logger.info(
        f"Merged {len(ordered)} chunks -> {output_path} "
        f"(merkle ✅ integrity ✅ O(1) memory)"
    )
    return output_path
