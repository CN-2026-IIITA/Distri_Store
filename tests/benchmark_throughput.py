"""
Upload + download throughput benchmark against the running 3-node cluster.

For each size, measures:
  - Upload (alpha): wall-time from POST /upload to response (incl. chunk + encrypt + replicate gossip)
  - Download local (alpha): chunks already on disk, just decrypt + merge + serve
  - Download cross-node (beta): chunks may be local (replicated) or fetched via onion

Reports throughput in MB/s.
"""
from __future__ import annotations

import hashlib
import io
import os
import secrets
import time

import httpx

ALPHA = "http://127.0.0.1:8888"
BETA = "http://127.0.0.1:8889"

SIZES_MB = [1, 10, 50]
PASSWORD = "bench"

HEADER_FMT = "{:<10}  {:>14}  {:>14}  {:>14}  {:>14}"


def make_random_blob(size_bytes: int) -> bytes:
    """Random bytes — incompressible, so we measure raw throughput, not zstd's win."""
    return secrets.token_bytes(size_bytes)


def time_post(client: httpx.Client, url: str, files, data, timeout: float) -> tuple[float, dict]:
    t0 = time.perf_counter()
    r = client.post(url, files=files, data=data, timeout=timeout)
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    return elapsed, r.json()


def time_get(client: httpx.Client, url: str, params: dict, timeout: float) -> tuple[float, bytes]:
    t0 = time.perf_counter()
    r = client.get(url, params=params, timeout=timeout)
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    return elapsed, r.content


def fmt_mbps(size_bytes: int, seconds: float) -> str:
    if seconds <= 0:
        return "—"
    mbps = (size_bytes / (1024 * 1024)) / seconds
    return f"{mbps:6.1f} MB/s"


def fmt_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:5.0f} ms"
    return f"{seconds:6.2f} s"


def main():
    print("=" * 80)
    print("  DistriStore — Upload/Download Throughput Benchmark")
    print(f"  Sizes: {SIZES_MB} MB  Password: '{PASSWORD}'  Random (incompressible) data")
    print("=" * 80)
    print()
    print(HEADER_FMT.format("size", "upload(α)", "dl-local(α)", "dl-peer(β)", "round-trip"))
    print(HEADER_FMT.format("-" * 4, "-" * 14, "-" * 14, "-" * 14, "-" * 14))

    client = httpx.Client()

    for size_mb in SIZES_MB:
        size_bytes = size_mb * 1024 * 1024
        blob = make_random_blob(size_bytes)
        src_hash = hashlib.sha256(blob).hexdigest()
        filename = f"bench_{size_mb}MB.bin"

        # 1. Upload to alpha
        files = {"file": (filename, io.BytesIO(blob), "application/octet-stream")}
        data = {"password": PASSWORD}
        t_up, body = time_post(client, f"{ALPHA}/upload", files, data, timeout=300)
        file_hash = body["file_hash"]
        chunks = body["chunks"]

        # 2. Download from alpha (local chunks)
        t_dl_alpha, content_a = time_get(
            client, f"{ALPHA}/download/{file_hash}", {"password": PASSWORD}, timeout=120,
        )
        ok_a = hashlib.sha256(content_a).hexdigest() == src_hash

        # 3. Download from beta (may need onion fetch if chunks not replicated)
        t_dl_beta, content_b = time_get(
            client, f"{BETA}/download/{file_hash}", {"password": PASSWORD}, timeout=300,
        )
        ok_b = hashlib.sha256(content_b).hexdigest() == src_hash

        # round-trip = upload + alpha-side download
        rt = t_up + t_dl_alpha

        size_label = f"{size_mb} MB" + (" *" if not (ok_a and ok_b) else "")
        print(HEADER_FMT.format(
            size_label,
            f"{fmt_time(t_up)} {fmt_mbps(size_bytes, t_up).split()[0]}",
            f"{fmt_time(t_dl_alpha)} {fmt_mbps(size_bytes, t_dl_alpha).split()[0]}",
            f"{fmt_time(t_dl_beta)} {fmt_mbps(size_bytes, t_dl_beta).split()[0]}",
            fmt_time(rt),
        ))
        print(f"             {chunks} chunks · hash={file_hash[:16]}...  α_match={ok_a} β_match={ok_b}")

    print()
    print("Notes:")
    print("  - Upload includes: read + chunk + AES-256-GCM encrypt + zstd compress + manifest write +")
    print("    background replicate gossip to peers.")
    print("  - Random data: zstd compression is ~1.0× (no win); reflects worst-case throughput.")
    print("  - dl-peer(β) may benefit from prior replication — chunks already on β's disk = no network.")
    print("  - All on localhost; numbers reflect crypto + chunking, not real network.")


if __name__ == "__main__":
    main()
