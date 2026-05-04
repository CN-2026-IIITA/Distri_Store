"""
DistriStore — Phase 25A: Onion-Routed Chunk Fetches

Every cross-peer chunk fetch is wrapped in a multi-hop onion circuit so the
peer holding the chunk does not learn who actually requested it. The
intermediate hops only see the next hop, not the full path.

Wire format
-----------
A circuit is built as a list of peer node_ids ending in the holder:
    requester  →  hop1  →  hop2  →  ...  →  holder

The requester wraps the inner request (a JSON dict) in N concentric
SealedBox layers, one per hop, addressed to that hop's X25519 pubkey:

    L_outermost = SealedBox(hop1.pub).encrypt({
        "next_hop": hop2_id,
        "next_endpoint": (hop2_ip, hop2_api_port),
        "payload":  L_2,
    })
    ...
    L_innermost = SealedBox(holder.pub).encrypt({
        "next_hop": "",                # no next hop — we are the holder
        "request":  {"op": "fetch_chunk", "chunk_hash": "..."},
    })

The requester POSTs L_outermost to hop1's /relay endpoint. hop1 peels its
layer with its private key, sees next_hop=hop2, POSTs the inner payload to
hop2's /relay, etc. The holder peels the innermost layer, executes the
request locally, and returns the chunk bytes as the HTTP response. The
response naturally flows back through the open connection chain — no onion
wrapping needed since the chunk is already AES-GCM encrypted at rest.

Privacy properties:
  - hop1 knows the requester but not the holder
  - middle hops know neither end
  - the holder knows the previous hop but not the requester
  - no single hop sees both endpoints (with hops >= 2)
"""

from __future__ import annotations

import json
import random
from typing import List, Tuple

from nacl.public import PrivateKey, PublicKey, SealedBox

from backend.utils.logger import get_logger

logger = get_logger("network.onion")

DEFAULT_HOPS = 2  # number of intermediaries; total path length = hops + 1 (holder)


# ── Circuit selection ──────────────────────────────────────────

def pick_circuit(holder_id: str, candidate_peers: dict, self_id: str,
                 hops: int = DEFAULT_HOPS) -> List[Tuple[str, str, int, str]]:
    """
    Pick a circuit of intermediary hops + holder.

    Args:
        holder_id: node_id of the peer holding the chunk.
        candidate_peers: dict of node_id -> PeerInfo (alive peers, must include holder).
        self_id: this node's id (excluded from circuit).
        hops: number of intermediaries to insert before the holder.

    Returns:
        Ordered list of (node_id, ip, api_port, public_key_hex) starting with
        the first hop and ending with the holder. Excludes the requester.
        If too few peers are available, returns a shorter circuit (degraded
        privacy with a warning logged) — never returns less than just the
        direct holder hop.
    """
    holder = candidate_peers.get(holder_id)
    if holder is None or not getattr(holder, "public_key", ""):
        raise ValueError(f"Holder {holder_id[:12]}... has no known pubkey — cannot onion-route")

    # Eligible relays = alive peers excluding self AND the holder, and with a known pubkey
    eligible = [
        (pid, p) for pid, p in candidate_peers.items()
        if pid != self_id and pid != holder_id and getattr(p, "public_key", "")
    ]
    chosen_relays_count = min(hops, len(eligible))
    if chosen_relays_count < hops:
        logger.warning(
            f"Only {len(eligible)} relay(s) available; circuit will use "
            f"{chosen_relays_count} hop(s) (requested {hops}). Privacy degraded."
        )

    relays = random.sample(eligible, chosen_relays_count)
    circuit = []
    for pid, p in relays:
        circuit.append((pid, p.ip, p.api_port, p.public_key))
    circuit.append((holder_id, holder.ip, holder.api_port, holder.public_key))
    return circuit


# ── Layered encryption / decryption ────────────────────────────

def _seal(pubkey_hex: str, data: bytes) -> bytes:
    return SealedBox(PublicKey(bytes.fromhex(pubkey_hex))).encrypt(data)


def _open(privkey: PrivateKey, ciphertext: bytes) -> bytes:
    return SealedBox(privkey).decrypt(ciphertext)


def pack_circuit(circuit: List[Tuple[str, str, int, str]],
                 inner_request: dict) -> bytes:
    """
    Build the outermost ciphertext for a circuit. circuit[0] is the first hop
    (the one the requester directly contacts); circuit[-1] is the holder.

    The innermost layer (addressed to the holder) carries the actual request.
    Each outer layer carries the next hop's identity + endpoint and the
    next inner ciphertext.

    Returns the bytes the requester POSTs to circuit[0]'s /relay endpoint.
    """
    if not circuit:
        raise ValueError("Empty circuit")

    # Innermost layer: addressed to the holder, carries the real request
    holder_id, holder_ip, holder_port, holder_pub = circuit[-1]
    inner = json.dumps({"next_hop": "", "request": inner_request}).encode()
    layer = _seal(holder_pub, inner)

    # Wrap each preceding hop, working outward
    for i in range(len(circuit) - 2, -1, -1):
        next_id, next_ip, next_port, _ = circuit[i + 1]
        envelope = json.dumps({
            "next_hop": next_id,
            "next_endpoint": [next_ip, next_port],
            "payload_b64": layer.hex(),  # hex so it survives JSON
        }).encode()
        _, _, _, this_pub = circuit[i]
        layer = _seal(this_pub, envelope)

    return layer


def peel_layer(privkey: PrivateKey, ciphertext: bytes) -> dict:
    """
    Peel one onion layer.

    Returns a dict with one of two shapes:
      - Forward case:   {"next_hop": "...", "next_endpoint": [ip, port], "payload": <bytes>}
      - Innermost case: {"next_hop": "", "request": {...}}
    """
    plain = _open(privkey, ciphertext)
    obj = json.loads(plain.decode())
    if obj.get("next_hop"):
        # Forward layer — convert hex payload back to bytes
        obj["payload"] = bytes.fromhex(obj.pop("payload_b64"))
    return obj
