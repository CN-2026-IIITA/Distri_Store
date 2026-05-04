from dataclasses import dataclass
from math import ceil
from typing import List


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


@dataclass(frozen=True)
class QuorumSignal:
    ack_latencies: List[float]
    stale_replica_ratio: float = 0.0
    transient_timeouts: int = 0
    quorum_size: int = 3


def build_quorum_drift_model(signal: QuorumSignal) -> dict:
    avg_latency = (
        sum(signal.ack_latencies) / len(signal.ack_latencies) if signal.ack_latencies else 0.0
    )
    latency_penalty = _clamp(avg_latency / 22, 0, 35)
    stale_penalty = _clamp(signal.stale_replica_ratio * 55, 0, 40)
    timeout_penalty = _clamp(signal.transient_timeouts * 2.3, 0, 22)
    quorum_factor = _clamp(signal.quorum_size, 1, 7)

    drift_score = int(
        round(
            _clamp(latency_penalty + stale_penalty + timeout_penalty + (7 - quorum_factor) * 1.8, 0, 100)
        )
    )

    return {
        "drift_score": drift_score,
        "avg_latency": int(round(avg_latency)),
        "mode": "unstable" if drift_score > 60 else "stable",
    }


def predict_reconciliation_rounds(drift_score: int = 0, batch_size: int = 25) -> int:
    normalized_batch = int(_clamp(batch_size, 1, 100))
    rounds = ceil((int(_clamp(drift_score, 0, 100)) / 10) * (30 / normalized_batch))
    return int(_clamp(rounds, 1, 12))
