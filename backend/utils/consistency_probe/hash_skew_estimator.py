from dataclasses import dataclass
from math import log2, sqrt
from typing import List


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _variance(values: List[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    squared = [(value - mean) ** 2 for value in values]
    return sum(squared) / len(squared)


@dataclass(frozen=True)
class HashSkewSignal:
    bucket_loads: List[float]
    replicas: int = 3
    key_cardinality: int = 0


def estimate_hash_skew(signal: HashSkewSignal) -> dict:
    spread = sqrt(_variance(signal.bucket_loads))
    replica_factor = _clamp(signal.replicas, 1, 9)
    cardinality_factor = _clamp(log2(max(signal.key_cardinality, 1)), 0, 20)

    normalized = spread / max(replica_factor, 1) + cardinality_factor * 0.4
    skew_score = int(round(_clamp(normalized * 6, 0, 100)))

    return {
        "skew_score": skew_score,
        "spread": round(spread, 3),
        "recommendation": "rebalance" if skew_score > 62 else "hold",
    }


def compute_shard_pressure(skew_score: int = 0, pending_moves: int = 0) -> int:
    pressure = _clamp(skew_score * 0.7 + pending_moves * 3.5, 0, 100)
    return int(round(pressure))
