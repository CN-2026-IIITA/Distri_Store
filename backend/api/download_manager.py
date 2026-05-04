"""
DistriStore — Download Manager (Phase 21: Stateful Pause & Resume)

Manages chunk-level download tracking to allow users to pause, resume,
and automatically recover dropped multi-gigabyte file transfers without
restarting from scratch.

Architecture:
  - Each active download is an asyncio.Task managed by the DownloadManager.
  - Progress is tracked per-chunk in a `.resume` JSON file.
  - On pause, the task is cancelled and the resume state is flushed to disk.
  - On resume, only missing chunks are re-fetched using the existing swarm logic.
  - Out-of-order chunk writes use file.seek() for random-access assembly.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Callable, Awaitable

from backend.utils.logger import get_logger

logger = get_logger("api.download_manager")

# Flush resume state every N chunks
FLUSH_INTERVAL = 10


@dataclass
class DownloadState:
    """Persistent state for a single file download."""
    file_hash: str
    filename: str
    total_chunks: int
    total_size: int
    missing_chunks: list          # Indices not yet downloaded
    completed_chunks: list        # Indices already on disk
    started_at: float = 0.0
    updated_at: float = 0.0
    status: str = "pending"       # pending | downloading | finalizing | paused | completed | error
    error_message: str = ""
    password: str = ""            # NOT persisted — set at runtime only
    output_path: str = ""         # Path to the merged output file (for browser download)

    @property
    def progress(self) -> float:
        if self.total_chunks == 0:
            return 0.0
        return round(len(self.completed_chunks) / self.total_chunks * 100, 1)

    @property
    def downloaded_chunks(self) -> int:
        return len(self.completed_chunks)

    def to_dict(self) -> dict:
        """Serialize for API response (excludes password)."""
        return {
            "file_hash": self.file_hash,
            "filename": self.filename,
            "total_chunks": self.total_chunks,
            "total_size": self.total_size,
            "downloaded_chunks": self.downloaded_chunks,
            "missing_chunks_count": len(self.missing_chunks),
            "progress": self.progress,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "error_message": self.error_message,
            "output_path": self.output_path,
        }

    def to_resume_dict(self) -> dict:
        """Serialize for .resume file persistence (no password)."""
        return {
            "file_hash": self.file_hash,
            "filename": self.filename,
            "total_chunks": self.total_chunks,
            "total_size": self.total_size,
            "missing_chunks": self.missing_chunks,
            "completed_chunks": self.completed_chunks,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_resume_dict(cls, data: dict) -> "DownloadState":
        return cls(
            file_hash=data["file_hash"],
            filename=data["filename"],
            total_chunks=data["total_chunks"],
            total_size=data["total_size"],
            missing_chunks=data["missing_chunks"],
            completed_chunks=data["completed_chunks"],
            started_at=data.get("started_at", 0),
            updated_at=data.get("updated_at", 0),
            status=data.get("status", "paused"),
        )


class DownloadManager:
    """
    Singleton manager for all active and paused downloads.

    Tracks chunk-level progress, supports pause/resume, and persists
    state to .resume files for crash recovery.
    """

    def __init__(self, storage_dir: str = ".storage"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Active download state and task tracking
        self._downloads: Dict[str, DownloadState] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}

        # Recover paused downloads from .resume files on startup
        self._recover_resume_files()

    # ── Resume File I/O ──────────────────────────────────────────

    def _resume_path(self, file_hash: str) -> Path:
        return self.storage_dir / f"{file_hash}.resume"

    def _save_resume(self, state: DownloadState):
        """Flush resume state to disk atomically."""
        state.updated_at = time.time()
        path = self._resume_path(state.file_hash)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(state.to_resume_dict(), indent=2))
            tmp.replace(path)  # Atomic rename
        except Exception as e:
            logger.error(f"Failed to save resume state: {e}")

    def _load_resume(self, file_hash: str) -> Optional[DownloadState]:
        path = self._resume_path(file_hash)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return DownloadState.from_resume_dict(data)
        except Exception as e:
            logger.error(f"Corrupt resume file {path}: {e}")
            return None

    def _delete_resume(self, file_hash: str):
        path = self._resume_path(file_hash)
        if path.exists():
            path.unlink()
            logger.debug(f"Deleted resume file: {file_hash[:12]}...")

    def _recover_resume_files(self):
        """On startup, load any .resume files as paused downloads."""
        for rfile in self.storage_dir.glob("*.resume"):
            try:
                data = json.loads(rfile.read_text())
                state = DownloadState.from_resume_dict(data)
                state.status = "paused"
                self._downloads[state.file_hash] = state
                logger.info(
                    f"Recovered paused download: {state.filename} "
                    f"({state.progress}% complete, "
                    f"{len(state.missing_chunks)} chunks remaining)"
                )
            except Exception as e:
                logger.warning(f"Skipping corrupt resume file {rfile.name}: {e}")

    # ── Download Control ─────────────────────────────────────────

    async def start_download(
        self,
        file_hash: str,
        manifest_dict: dict,
        password: str,
        load_chunk_fn: Callable[[str], Awaitable[bytes]],
        local_store,
    ) -> DownloadState:
        """
        Start (or resume) a download for the given file hash.

        Args:
            file_hash: SHA-256 hash of the file.
            manifest_dict: Manifest dictionary from the API.
            password: Decryption password.
            load_chunk_fn: Async callable(chunk_hash) -> bytes.
            local_store: LocalStore instance for chunk persistence.
        """
        from backend.file_engine.chunker import FileManifest

        manifest = FileManifest.from_dict(manifest_dict)

        # Check for existing download in memory or on disk
        existing = self._downloads.get(file_hash) or self._load_resume(file_hash)

        if existing and existing.status == "downloading":
            logger.warning(f"Download already active: {file_hash[:12]}...")
            return existing

        if existing and existing.status == "completed":
            # Already completed — don't restart
            logger.info(f"Download already completed: {file_hash[:12]}...")
            return existing

        if existing and len(existing.missing_chunks) > 0:
            # Resume from existing state — has remaining work
            state = existing
            state.status = "downloading"
            state.password = password
            state.error_message = ""
            logger.info(
                f"Resuming download '{state.filename}': "
                f"{len(state.missing_chunks)} chunks remaining "
                f"({state.progress}% complete)"
            )
        else:
            # Fresh download (or previous had 0 missing = needs re-run from scratch)
            all_indices = list(range(len(manifest.chunks)))
            state = DownloadState(
                file_hash=file_hash,
                filename=manifest.original_filename,
                total_chunks=len(manifest.chunks),
                total_size=manifest.original_size,
                missing_chunks=all_indices,
                completed_chunks=[],
                started_at=time.time(),
                status="downloading",
                password=password,
            )
            logger.info(
                f"Starting fresh download '{state.filename}': "
                f"{state.total_chunks} chunks"
            )

        self._downloads[file_hash] = state
        self._save_resume(state)

        # Create a cancel event for pause support
        cancel_event = asyncio.Event()
        self._cancel_events[file_hash] = cancel_event

        # Launch the download task
        task = asyncio.create_task(
            self._download_worker(
                file_hash, manifest, state, load_chunk_fn, local_store, cancel_event
            )
        )
        self._tasks[file_hash] = task

        return state

    async def pause_download(self, file_hash: str) -> Optional[DownloadState]:
        """Signal a download to pause and save its state."""
        state = self._downloads.get(file_hash)
        if not state:
            return None

        if state.status != "downloading":
            return state

        # Signal the worker to stop
        cancel_event = self._cancel_events.get(file_hash)
        if cancel_event:
            cancel_event.set()

        # Wait briefly for the task to wind down
        task = self._tasks.get(file_hash)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        state.status = "paused"
        self._save_resume(state)
        self._tasks.pop(file_hash, None)
        self._cancel_events.pop(file_hash, None)

        logger.info(
            f"Paused download '{state.filename}': "
            f"{state.progress}% complete, "
            f"{len(state.missing_chunks)} chunks remaining"
        )
        return state

    async def resume_download(
        self,
        file_hash: str,
        password: str,
        load_chunk_fn: Callable[[str], Awaitable[bytes]],
        local_store,
    ) -> Optional[DownloadState]:
        """Resume a paused download."""
        state = self._downloads.get(file_hash) or self._load_resume(file_hash)
        if not state:
            return None

        if state.status == "downloading":
            return state

        if state.status == "completed":
            return state

        # Reload manifest to get chunk info
        manifest_dict = local_store.load_manifest(file_hash)
        if not manifest_dict:
            return None

        from backend.file_engine.chunker import FileManifest
        manifest = FileManifest.from_dict(manifest_dict)

        state.status = "downloading"
        state.password = password
        state.error_message = ""
        self._downloads[file_hash] = state

        cancel_event = asyncio.Event()
        self._cancel_events[file_hash] = cancel_event

        task = asyncio.create_task(
            self._download_worker(
                file_hash, manifest, state, load_chunk_fn, local_store, cancel_event
            )
        )
        self._tasks[file_hash] = task

        logger.info(
            f"Resumed download '{state.filename}': "
            f"{len(state.missing_chunks)} chunks remaining"
        )
        return state

    # ── The Download Worker ──────────────────────────────────────

    async def _download_worker(
        self,
        file_hash: str,
        manifest,
        state: DownloadState,
        load_chunk_fn: Callable[[str], Awaitable[bytes]],
        local_store,
        cancel_event: asyncio.Event,
    ):
        """
        Async worker that downloads missing chunks, caches them locally,
        then performs a final merge (decrypt+decompress) once all chunks
        are available.
        """
        ordered_chunks = sorted(manifest.chunks, key=lambda c: c.index)
        chunks_since_flush = 0

        try:
            # Work through only the missing chunks
            missing_set = set(state.missing_chunks)

            for info in ordered_chunks:
                if info.index not in missing_set:
                    continue

                # Check for pause signal
                if cancel_event.is_set():
                    logger.debug(f"Download paused at chunk {info.index}")
                    return

                try:
                    # Fetch the chunk (local store or peer fallback)
                    data = await load_chunk_fn(info.chunk_hash)

                    # Cache chunk locally for future use
                    if not local_store.has_chunk(info.chunk_hash):
                        local_store.save_chunk(info.chunk_hash, data)

                    # Mark chunk as completed
                    if info.index in state.missing_chunks:
                        state.missing_chunks.remove(info.index)
                    if info.index not in state.completed_chunks:
                        state.completed_chunks.append(info.index)

                    chunks_since_flush += 1

                    # Periodic flush of resume state
                    if chunks_since_flush >= FLUSH_INTERVAL:
                        self._save_resume(state)
                        chunks_since_flush = 0

                    # Yield control to the event loop so polling can respond
                    # This is critical: without this, the event loop is starved
                    # and the frontend never sees progress updates.
                    await asyncio.sleep(0)

                except Exception as e:
                    logger.error(
                        f"Failed to download chunk {info.index} "
                        f"({info.chunk_hash[:12]}...): {e}"
                    )
                    # Save progress before failing
                    self._save_resume(state)
                    state.status = "error"
                    state.error_message = str(e)
                    return

            # All chunks downloaded — now merge to final file
            state.status = "finalizing"
            self._save_resume(state)
            logger.info(
                f"All chunks downloaded for '{state.filename}', "
                f"performing final merge..."
            )

            # Final merge: load all chunks in order, decrypt, write
            await self._final_merge(
                file_hash, manifest, state, local_store
            )

        except asyncio.CancelledError:
            # Graceful cancellation — save state
            state.status = "paused"
            self._save_resume(state)
            raise

        except Exception as e:
            state.status = "error"
            state.error_message = str(e)
            self._save_resume(state)
            logger.error(f"Download error for {file_hash[:12]}...: {e}")

    async def _final_merge(self, file_hash, manifest, state, local_store):
        """
        Perform the final decrypt+decompress merge once all chunks are downloaded.
        Uses the existing pipeline_merge_to_disk for consistency.
        """
        from backend.file_engine.pipeline import pipeline_merge_to_disk

        output_path = str(self.storage_dir / f"dl_{file_hash[:16]}_{manifest.original_filename}")

        async def load_fn(chunk_hash: str) -> bytes:
            data = local_store.load_chunk(chunk_hash)
            if data is None:
                raise FileNotFoundError(f"Chunk {chunk_hash[:12]}... missing from store")
            return data

        try:
            await pipeline_merge_to_disk(
                manifest, load_fn, output_path, password=state.password
            )
            state.status = "completed"
            state.output_path = output_path
            state.updated_at = time.time()
            # Don't delete resume file yet — keep it so UI can show "completed"
            # It will be cleaned up when the user clears completed downloads.
            self._save_resume(state)

            logger.info(
                f"Download complete: '{state.filename}' "
                f"({state.total_chunks} chunks, {state.total_size} bytes) "
                f"-> {output_path}"
            )

        except Exception as e:
            state.status = "error"
            state.error_message = f"Merge failed: {e}"
            self._save_resume(state)
            if os.path.exists(output_path):
                os.unlink(output_path)
            logger.error(f"Merge failed for {file_hash[:12]}...: {e}")

    # ── Query ────────────────────────────────────────────────────

    def get_download(self, file_hash: str) -> Optional[DownloadState]:
        return self._downloads.get(file_hash)

    def get_all_downloads(self) -> Dict[str, dict]:
        """Return all tracked downloads (active + paused + completed)."""
        return {
            fh: state.to_dict()
            for fh, state in self._downloads.items()
        }

    def clear_specific_completed(self, file_hash: str):
        """Remove a specific download from the tracker and clean up its file."""
        state = self._downloads.get(file_hash)
        if state:
            self._delete_resume(file_hash)
            if state.output_path and os.path.exists(state.output_path):
                try:
                    os.unlink(state.output_path)
                except Exception:
                    pass
            self._downloads.pop(file_hash, None)
            self._tasks.pop(file_hash, None)
            self._cancel_events.pop(file_hash, None)

    def clear_completed(self):
        """Remove completed downloads from the tracker and clean up files."""
        to_remove = [
            fh for fh, s in self._downloads.items()
            if s.status in ("completed", "error")
        ]
        for fh in to_remove:
            state = self._downloads.get(fh)
            # Clean up the resume file
            self._delete_resume(fh)
            # Clean up the merged output file for errors (keep completed)
            if state and state.status == "error" and state.output_path:
                try:
                    if os.path.exists(state.output_path):
                        os.unlink(state.output_path)
                except Exception:
                    pass
            self._downloads.pop(fh, None)
            self._tasks.pop(fh, None)
            self._cancel_events.pop(fh, None)
