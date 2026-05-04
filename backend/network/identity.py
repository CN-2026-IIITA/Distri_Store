"""
DistriStore — Phase 25A: Per-Node Identity Keypair

Each node has an X25519 keypair used for the onion-routing layer:
  - Public key is gossiped via HELLO broadcasts so peers can wrap onion
    layers addressed to this node.
  - Private key is used to peel onion layers (SealedBox.decrypt).

The keypair is generated once on first boot and persisted in SQLite so
peers' identities (and therefore, peers' references to this node) stay
stable across restarts.
"""

from __future__ import annotations

import sqlite3
from typing import Optional, Tuple

from nacl.public import PrivateKey, PublicKey, SealedBox

from backend.utils.logger import get_logger

logger = get_logger("network.identity")


# ── Persistence schema (lives next to the existing tables in NodeDatabase) ──

def init_identity_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS node_identity (
               id           INTEGER PRIMARY KEY CHECK(id = 1),
               public_key   BLOB NOT NULL,
               private_key  BLOB NOT NULL,
               created_at   REAL NOT NULL
           )"""
    )
    conn.commit()


def load_or_create_keypair(conn: sqlite3.Connection) -> Tuple[PrivateKey, PublicKey]:
    """Return this node's persistent X25519 keypair, generating one on first boot."""
    init_identity_table(conn)
    row = conn.execute("SELECT public_key, private_key FROM node_identity WHERE id = 1").fetchone()
    if row is not None:
        priv = PrivateKey(bytes(row["private_key"]))
        pub = PublicKey(bytes(row["public_key"]))
        return priv, pub

    import time
    priv = PrivateKey.generate()
    pub = priv.public_key
    conn.execute(
        "INSERT OR REPLACE INTO node_identity (id, public_key, private_key, created_at) "
        "VALUES (1, ?, ?, ?)",
        (pub.encode(), priv.encode(), time.time()),
    )
    conn.commit()
    logger.info(f"Generated new node identity keypair (pub={pub.encode().hex()[:16]}...)")
    return priv, pub


# ── Helpers around SealedBox ───────────────────────────────────

def encrypt_to(pubkey_hex: str, plaintext: bytes) -> bytes:
    """Encrypt a payload to a peer's public key (anyone can encrypt)."""
    pub = PublicKey(bytes.fromhex(pubkey_hex))
    return SealedBox(pub).encrypt(plaintext)


def decrypt_with(privkey: PrivateKey, ciphertext: bytes) -> bytes:
    """Decrypt a SealedBox payload addressed to us."""
    return SealedBox(privkey).decrypt(ciphertext)
