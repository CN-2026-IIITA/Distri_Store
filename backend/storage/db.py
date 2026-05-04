"""
DistriStore — SQLite Persistence Layer (Phase 16)
Replaces in-memory dicts and flat JSON files with a persistent
SQLite database for File Manifests and Peer Routing Tables.

All writes use asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from backend.utils.logger import get_logger

logger = get_logger("storage.db")


class NodeDatabase:
    """Persistent SQLite store for manifests and peer routing."""

    def __init__(self, storage_dir: str = ".storage"):
        db_dir = Path(storage_dir)
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "distristore.db"

        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")      # concurrent reads
        self._conn.execute("PRAGMA synchronous=NORMAL")     # safe + fast
        self._conn.row_factory = sqlite3.Row

        self._create_tables()
        logger.info(f"NodeDatabase initialized at {db_path.resolve()}")

    # ── Schema ─────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                node_id     TEXT PRIMARY KEY,
                ip          TEXT,
                tcp_port    INTEGER,
                api_port    INTEGER,
                name        TEXT DEFAULT '',
                health_score REAL DEFAULT 0.0,
                last_seen   REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS manifests (
                file_hash        TEXT PRIMARY KEY,
                filename         TEXT,
                total_size       INTEGER,
                merkle_root      TEXT,
                chunks_json      TEXT,
                compression      TEXT DEFAULT '',
                chunk_size       INTEGER DEFAULT 262144,
                replication_mode TEXT DEFAULT 'kcopy',
                erasure_k        INTEGER DEFAULT 0,
                erasure_n        INTEGER DEFAULT 0
            )
        """)
        # Phase 24A: invite-based 1:1 chat threads + messages
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                peer_id          TEXT PRIMARY KEY,
                peer_name        TEXT DEFAULT '',
                status           TEXT NOT NULL,          -- outgoing_pending | incoming_pending | accepted | rejected
                invited_by_self  INTEGER NOT NULL DEFAULT 0,
                created_at       REAL NOT NULL,
                updated_at       REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id     TEXT NOT NULL,
                from_self   INTEGER NOT NULL,    -- 1 = sent by us, 0 = received
                body        TEXT NOT NULL,
                sent_at     REAL NOT NULL,
                FOREIGN KEY(peer_id) REFERENCES chat_threads(peer_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_peer "
            "ON chat_messages(peer_id, sent_at)"
        )
        # Phase 24C: file shares — recipient-side inbox of file refs sent by peers
        cur.execute("""
            CREATE TABLE IF NOT EXISTS file_shares (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                from_peer_id    TEXT NOT NULL,
                from_peer_name  TEXT DEFAULT '',
                file_hash       TEXT NOT NULL,
                filename        TEXT DEFAULT '',
                size            INTEGER DEFAULT 0,
                note            TEXT DEFAULT '',
                sent_at         REAL NOT NULL
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_shares_peer "
            "ON file_shares(from_peer_id, sent_at DESC)"
        )
        # Phase 18: migrate existing tables that lack the compression column
        # Phase 23: migrate existing tables that lack erasure-mode columns
        for ddl in (
            "ALTER TABLE manifests ADD COLUMN compression TEXT DEFAULT ''",
            "ALTER TABLE manifests ADD COLUMN chunk_size INTEGER DEFAULT 262144",
            "ALTER TABLE manifests ADD COLUMN replication_mode TEXT DEFAULT 'kcopy'",
            "ALTER TABLE manifests ADD COLUMN erasure_k INTEGER DEFAULT 0",
            "ALTER TABLE manifests ADD COLUMN erasure_n INTEGER DEFAULT 0",
        ):
            try:
                cur.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    # ── Manifest operations (sync, called via to_thread) ───────────

    def _save_manifest_sync(self, file_hash: str, manifest_dict: dict) -> None:
        chunks_json = json.dumps(manifest_dict.get("chunks", []))
        self._conn.execute(
            """INSERT OR REPLACE INTO manifests
               (file_hash, filename, total_size, merkle_root, chunks_json,
                compression, chunk_size, replication_mode, erasure_k, erasure_n)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_hash,
                manifest_dict.get("original_filename", ""),
                manifest_dict.get("original_size", 0),
                manifest_dict.get("merkle_root", ""),
                chunks_json,
                manifest_dict.get("compression", ""),
                manifest_dict.get("chunk_size", 262144),
                manifest_dict.get("replication_mode", "kcopy"),
                manifest_dict.get("erasure_k", 0),
                manifest_dict.get("erasure_n", 0),
            ),
        )
        self._conn.commit()
        logger.debug(f"DB: Saved manifest {file_hash[:12]}...")

    def _row_to_manifest(self, row) -> dict:
        keys = row.keys()
        return {
            "file_hash": row["file_hash"],
            "original_filename": row["filename"],
            "original_size": row["total_size"],
            "merkle_root": row["merkle_root"],
            "chunks": json.loads(row["chunks_json"]),
            "compression": row["compression"] if "compression" in keys else "",
            "chunk_size": row["chunk_size"] if "chunk_size" in keys else 262144,
            "replication_mode": row["replication_mode"] if "replication_mode" in keys else "kcopy",
            "erasure_k": row["erasure_k"] if "erasure_k" in keys else 0,
            "erasure_n": row["erasure_n"] if "erasure_n" in keys else 0,
        }

    def _get_manifest_sync(self, file_hash: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM manifests WHERE file_hash = ?", (file_hash,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_manifest(row)

    def _get_all_manifests_sync(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM manifests")
        return [self._row_to_manifest(row) for row in cur.fetchall()]

    # ── Peer operations (sync, called via to_thread) ───────────────

    def _upsert_peer_sync(
        self, node_id: str, ip: str, tcp_port: int,
        api_port: int = 8888, name: str = "",
        health_score: float = 0.0, last_seen: float = None,
    ) -> None:
        if last_seen is None:
            last_seen = time.time()
        self._conn.execute(
            """INSERT OR REPLACE INTO peers
               (node_id, ip, tcp_port, api_port, name, health_score, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (node_id, ip, tcp_port, api_port, name, health_score, last_seen),
        )
        self._conn.commit()

    def _get_all_peers_sync(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM peers")
        return [dict(row) for row in cur.fetchall()]

    # ── Phase 24A: chat thread + message operations ────────────────

    def _upsert_chat_thread_sync(
        self, peer_id: str, status: str,
        invited_by_self: bool, peer_name: str = "",
    ) -> dict:
        """Insert or update a chat thread row, returning the resulting row dict."""
        now = time.time()
        cur = self._conn.execute(
            "SELECT * FROM chat_threads WHERE peer_id = ?", (peer_id,)
        )
        existing = cur.fetchone()

        if existing is None:
            self._conn.execute(
                """INSERT INTO chat_threads
                   (peer_id, peer_name, status, invited_by_self, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (peer_id, peer_name, status, 1 if invited_by_self else 0, now, now),
            )
        else:
            self._conn.execute(
                """UPDATE chat_threads
                   SET status = ?, peer_name = COALESCE(NULLIF(?, ''), peer_name),
                       updated_at = ?
                   WHERE peer_id = ?""",
                (status, peer_name, now, peer_id),
            )
        self._conn.commit()

        cur = self._conn.execute(
            "SELECT * FROM chat_threads WHERE peer_id = ?", (peer_id,)
        )
        return dict(cur.fetchone())

    def _get_chat_thread_sync(self, peer_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM chat_threads WHERE peer_id = ?", (peer_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def _get_all_chat_threads_sync(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM chat_threads ORDER BY updated_at DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    def _delete_chat_thread_sync(self, peer_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM chat_messages WHERE peer_id = ?", (peer_id,)
        )
        cur = self._conn.execute(
            "DELETE FROM chat_threads WHERE peer_id = ?", (peer_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def _append_chat_message_sync(
        self, peer_id: str, from_self: bool, body: str,
    ) -> dict:
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO chat_messages (peer_id, from_self, body, sent_at)
               VALUES (?, ?, ?, ?)""",
            (peer_id, 1 if from_self else 0, body, now),
        )
        self._conn.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE peer_id = ?",
            (now, peer_id),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "peer_id": peer_id,
            "from_self": 1 if from_self else 0,
            "body": body,
            "sent_at": now,
        }

    def _get_chat_messages_sync(self, peer_id: str, limit: int = 500) -> list[dict]:
        cur = self._conn.execute(
            """SELECT * FROM chat_messages
               WHERE peer_id = ? ORDER BY sent_at ASC LIMIT ?""",
            (peer_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── Phase 24C: file share inbox ─────────────────────────────────

    def _insert_file_share_sync(
        self, from_peer_id: str, from_peer_name: str,
        file_hash: str, filename: str, size: int, note: str = "",
    ) -> dict:
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO file_shares
               (from_peer_id, from_peer_name, file_hash, filename, size, note, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (from_peer_id, from_peer_name, file_hash, filename, size, note, now),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "from_peer_id": from_peer_id,
            "from_peer_name": from_peer_name,
            "file_hash": file_hash,
            "filename": filename,
            "size": size,
            "note": note,
            "sent_at": now,
        }

    def _get_all_file_shares_sync(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM file_shares ORDER BY sent_at DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    def _delete_file_share_sync(self, share_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM file_shares WHERE id = ?", (share_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── Async wrappers ─────────────────────────────────────────────

    async def save_manifest(self, file_hash: str, manifest_dict: dict) -> None:
        await asyncio.to_thread(self._save_manifest_sync, file_hash, manifest_dict)

    async def get_manifest(self, file_hash: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get_manifest_sync, file_hash)

    async def get_all_manifests(self) -> list[dict]:
        return await asyncio.to_thread(self._get_all_manifests_sync)

    async def upsert_peer(self, **kwargs) -> None:
        await asyncio.to_thread(self._upsert_peer_sync, **kwargs)

    async def get_all_peers(self) -> list[dict]:
        return await asyncio.to_thread(self._get_all_peers_sync)

    # Phase 24A: chat thread + message async wrappers
    async def upsert_chat_thread(self, peer_id: str, status: str,
                                  invited_by_self: bool, peer_name: str = "") -> dict:
        return await asyncio.to_thread(
            self._upsert_chat_thread_sync,
            peer_id, status, invited_by_self, peer_name,
        )

    async def get_chat_thread(self, peer_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get_chat_thread_sync, peer_id)

    async def get_all_chat_threads(self) -> list[dict]:
        return await asyncio.to_thread(self._get_all_chat_threads_sync)

    async def delete_chat_thread(self, peer_id: str) -> bool:
        return await asyncio.to_thread(self._delete_chat_thread_sync, peer_id)

    async def append_chat_message(self, peer_id: str, from_self: bool, body: str) -> dict:
        return await asyncio.to_thread(
            self._append_chat_message_sync, peer_id, from_self, body
        )

    async def get_chat_messages(self, peer_id: str, limit: int = 500) -> list[dict]:
        return await asyncio.to_thread(self._get_chat_messages_sync, peer_id, limit)

    # Phase 24C: file-share async wrappers
    async def insert_file_share(self, from_peer_id: str, from_peer_name: str,
                                 file_hash: str, filename: str, size: int,
                                 note: str = "") -> dict:
        return await asyncio.to_thread(
            self._insert_file_share_sync,
            from_peer_id, from_peer_name, file_hash, filename, size, note,
        )

    async def get_all_file_shares(self) -> list[dict]:
        return await asyncio.to_thread(self._get_all_file_shares_sync)

    async def delete_file_share(self, share_id: int) -> bool:
        return await asyncio.to_thread(self._delete_file_share_sync, share_id)

    def close(self) -> None:
        self._conn.close()
        logger.info("NodeDatabase connection closed")
