"""
DistriStore — Phase 25C: Threshold-Encrypted Files (Shamir Secret Sharing)

The file's AES-256 key is split into N shares using Shamir's Secret Sharing;
any M shares can reconstruct it. Shares are distributed across N peers, so
no single peer can decrypt the file alone — it takes a quorum of M peers
agreeing to release their shares.

Protocol:
  1. Sender generates a random 32-byte AES-256 key.
  2. Sender encrypts the file chunks with this key (existing encryption pipeline).
  3. Sender splits the key into N shares via Shamir (two parallel halves of
     16 bytes each, since pycryptodome's Shamir works on 16-byte blocks).
  4. Sender POSTs each share to the corresponding peer's
     /peer/keyshare/store endpoint (gated by accepted chat thread).
     Each share is wrapped in a SealedBox addressed to the holder's
     X25519 pubkey so even disk-snapshot leakage doesn't expose it.
  5. Manifest records the holders + (M, N) so downloader knows where to ask.

Reconstruction (downloader):
  6. Downloader asks each holder /peer/keyshare/release for its share.
  7. Holder verifies the requester is in allowed_requesters before releasing,
     and re-wraps the share addressed to the requester's pubkey.
  8. Downloader collects M shares, reconstructs the key, decrypts the file.
"""

from __future__ import annotations

from typing import List, Tuple

from Crypto.Protocol.SecretSharing import Shamir
from Crypto.Random import get_random_bytes

from backend.file_engine.crypto import KEY_SIZE  # 32
from backend.utils.logger import get_logger

logger = get_logger("strategies.threshold")


# A "packed share" is (index, 32_bytes) where the 32 bytes are the
# concatenation of the two 16-byte Shamir shares: [low_half_share || high_half_share]
PackedShare = Tuple[int, bytes]


def generate_aes_key() -> bytes:
    """Fresh random 32-byte AES-256 key."""
    return get_random_bytes(KEY_SIZE)


def split_key(key: bytes, m: int, n: int) -> List[PackedShare]:
    """
    Split a 32-byte AES-256 key into n Shamir shares (any m reconstruct).

    pycryptodome's Shamir works on exactly 16-byte secrets, so we split each
    half independently and concatenate the resulting share bytes. The share
    index is shared across both halves — calling Shamir.combine on either
    half independently with m shares of the same indices recovers that half.
    """
    if not (1 <= m <= n <= 254):
        raise ValueError(f"Invalid (m, n) = ({m}, {n}); require 1 <= m <= n <= 254")
    if len(key) != KEY_SIZE:
        raise ValueError(f"Key must be {KEY_SIZE} bytes, got {len(key)}")

    half_low = key[:16]
    half_high = key[16:]

    shares_low = Shamir.split(m, n, half_low)
    shares_high = Shamir.split(m, n, half_high)

    packed: List[PackedShare] = []
    for (idx_lo, lo), (idx_hi, hi) in zip(shares_low, shares_high):
        # Both halves use the same index sequence (1..n); sanity check
        assert idx_lo == idx_hi, f"Shamir index mismatch: {idx_lo} vs {idx_hi}"
        packed.append((idx_lo, bytes(lo) + bytes(hi)))
    return packed


def combine_key(shares: List[PackedShare]) -> bytes:
    """Reconstruct the 32-byte AES key from m or more packed shares."""
    if not shares:
        raise ValueError("No shares provided")
    halves_low = [(idx, payload[:16]) for idx, payload in shares]
    halves_high = [(idx, payload[16:]) for idx, payload in shares]
    return Shamir.combine(halves_low) + Shamir.combine(halves_high)


# ── Self-test (kept for debugging; called by tests/test_phase25c) ─

if __name__ == "__main__":
    import random
    k = generate_aes_key()
    shares = split_key(k, m=3, n=5)
    print(f"split into {len(shares)} shares of {len(shares[0][1])} bytes each")
    chosen = random.sample(shares, 3)
    recovered = combine_key(chosen)
    assert recovered == k
    print("PASS: key recovered from 3 of 5 shares")
