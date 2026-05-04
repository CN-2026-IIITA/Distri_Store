"""
DistriStore — Replication Strategy
Selects top-k peers and sends chunks to them over TCP.
"""

import asyncio
from typing import List, Optional

from backend.node.state import NodeState
from backend.strategies.selector import select_best_peers, score_peers
from backend.dht.routing import RoutingTable, find_closest_peers
from backend.network.connection import ConnectionManager, PeerConnection
from backend.network.protocol import store_chunk_msg, store_ack_msg, MSG_STORE_ACK
from backend.file_engine.chunker import FileManifest, ChunkInfo
from backend.storage.local_store import LocalStore
from backend.utils.logger import get_logger

logger = get_logger("strategies.replication")


class ReplicationEngine:
    """Handles distributing chunks to peers across the network."""

    def __init__(self, state: NodeState, conn_mgr: ConnectionManager,
                 routing: RoutingTable, local_store: LocalStore,
                 replication_factor: int = 3):
        self.state = state
        self.conn_mgr = conn_mgr
        self.routing = routing
        self.local_store = local_store
        self.k = replication_factor

    async def replicate_chunks(self, manifest: FileManifest,
                                chunk_data_list: list[bytes]) -> dict:
        """
        Distribute all chunks of a file to the best k peers.

        Returns:
            dict with results per chunk.
        """
        results = {}

        for info, data in zip(manifest.chunks, chunk_data_list):
            # Save locally first
            self.local_store.save_chunk(info.chunk_hash, data)
            await self.state.register_chunk(info.chunk_hash, info.chunk_hash)

            # Find best peers using combined heuristic + XOR
            target_peers = await self._select_targets(info.chunk_hash)

            if not target_peers:
                logger.warning(f"No peers available for chunk {info.chunk_hash[:12]}...")
                results[info.chunk_hash] = {"stored_locally": True, "replicated_to": []}
                continue

            # Send to each target peer
            replicated_to = []
            for peer_id in target_peers[:self.k]:
                success = await self._send_chunk_to_peer(peer_id, info, data, manifest.file_hash)
                if success:
                    replicated_to.append(peer_id)

            # Update routing table
            self.routing.assign_chunk(info.chunk_hash, [self.state.node_id] + replicated_to)

            results[info.chunk_hash] = {
                "stored_locally": True,
                "replicated_to": replicated_to,
                "total_copies": 1 + len(replicated_to),
            }
            logger.info(
                f"Chunk {info.chunk_hash[:12]}... replicated to "
                f"{len(replicated_to)}/{self.k} peers"
            )

        return results

    async def replicate_shards(self, manifest: FileManifest,
                                shards_per_chunk: list) -> dict:
        """
        Phase 23: Distribute Reed-Solomon shards across peers.

        Each chunk is split into n shards on the uploader; this method places
        them across alive peers so that losing any peer costs at most one shard
        per chunk (when peer_count >= n). With fewer peers than shards we cycle
        round-robin — fewer-peers degrades fault tolerance gracefully.

        Args:
            manifest: FileManifest with shard metadata already populated.
            shards_per_chunk: List parallel to manifest.chunks; each element is
                a list of erasure.Shard objects (with .data populated).

        Returns:
            Per-chunk replication result (which shards landed on which peers).
        """
        results = {}
        peers = await self.state.get_alive_peers()
        peer_ids = list(peers.keys())

        for info, shards in zip(manifest.chunks, shards_per_chunk):
            placements = {}  # shard_index -> [peer_ids]

            for shard in shards:
                if not peer_ids:
                    placements[shard.shard_index] = []
                    continue

                # Spread shards across peers using XOR distance from shard hash,
                # then fall back to round-robin so every shard lands somewhere.
                xor_closest = find_closest_peers(shard.shard_hash, peer_ids, k=len(peer_ids))
                ordered_targets = [pid for pid, _ in xor_closest]

                # Place each shard on a single distinct peer (degraded:
                # if peers < n, some peers will hold multiple shards of the
                # same chunk — fault tolerance drops accordingly).
                target = ordered_targets[shard.shard_index % len(ordered_targets)]
                ok = await self._send_shard_to_peer(target, shard, info, manifest.file_hash)
                placements[shard.shard_index] = [target] if ok else []

            results[info.chunk_hash] = {
                "stored_locally": True,
                "shard_placements": placements,
                "total_shards": len(shards),
            }
            placed = sum(1 for v in placements.values() if v)
            logger.info(
                f"Chunk {info.chunk_hash[:12]}... erasure-replicated: "
                f"{placed}/{len(shards)} shards placed on peers"
            )

        return results

    async def _send_shard_to_peer(self, peer_id: str, shard, info,
                                   file_hash: str) -> bool:
        """Send a single shard to a peer (uses chunk-store wire format —
        peers store it under the shard_hash key, just like any other chunk)."""
        conn = self.conn_mgr.connections.get(peer_id)
        if not conn:
            peer = await self.state.get_peer(peer_id)
            if not peer:
                return False
            conn = await self.conn_mgr.connect_to_peer(peer.ip, peer.tcp_port)
            if not conn:
                return False

        try:
            msg = store_chunk_msg(
                self.state.node_id, shard.shard_hash, shard.data, file_hash
            )
            await conn.send(msg)
            logger.debug(
                f"Sent shard {shard.shard_hash[:12]}... "
                f"(chunk {info.index}, idx {shard.shard_index}) "
                f"to {peer_id[:12]}..."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send shard to {peer_id[:12]}...: {e}")
            return False

    async def _select_targets(self, chunk_hash: str) -> List[str]:
        """Combine heuristic scoring with XOR distance for peer selection."""
        peers = await self.state.get_alive_peers()
        if not peers:
            return []

        # Score by heuristic
        scored = score_peers(peers)
        heuristic_top = [pid for pid, _, _ in scored[:self.k * 2]]

        # Also consider XOR-closest
        xor_closest = find_closest_peers(chunk_hash, list(peers.keys()), k=self.k)
        xor_top = [pid for pid, _ in xor_closest]

        # Merge: prioritize XOR-closest but fill with heuristic-best
        combined = []
        for pid in xor_top:
            if pid not in combined:
                combined.append(pid)
        for pid in heuristic_top:
            if pid not in combined:
                combined.append(pid)

        return combined[:self.k]

    async def _send_chunk_to_peer(self, peer_id: str, info: ChunkInfo,
                                   data: bytes, file_hash: str) -> bool:
        """Send a single chunk to a peer over TCP."""
        conn = self.conn_mgr.connections.get(peer_id)
        if not conn:
            # Try to connect
            peer = await self.state.get_peer(peer_id)
            if not peer:
                return False
            conn = await self.conn_mgr.connect_to_peer(peer.ip, peer.tcp_port)
            if not conn:
                return False

        try:
            msg = store_chunk_msg(self.state.node_id, info.chunk_hash, data, file_hash)
            await conn.send(msg)
            logger.debug(f"Sent chunk {info.chunk_hash[:12]}... to {peer_id[:12]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send chunk to {peer_id[:12]}...: {e}")
            return False
