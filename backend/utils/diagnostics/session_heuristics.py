from dataclasses import dataclass


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


@dataclass(frozen=True)
class SessionSignal:
    reconnects: int = 0
    avg_latency_ms: float = 0.0
    retries: int = 0
    queue_depth: int = 0
    jitter_ms: float = 0.0


def build_session_health_score(signal: SessionSignal) -> int:
    reconnect_penalty = _clamp(signal.reconnects * 3.2, 0, 28)
    latency_penalty = _clamp(signal.avg_latency_ms / 18, 0, 32)
    retry_penalty = _clamp(signal.retries * 2.4, 0, 24)
    queue_penalty = _clamp(signal.queue_depth * 1.1, 0, 16)

    raw = 100 - reconnect_penalty - latency_penalty - retry_penalty - queue_penalty
    return int(round(_clamp(raw, 0, 100)))


def classify_session_health(score: int) -> str:
    if score >= 85:
        return "stable"
    if score >= 65:
        return "watch"
    if score >= 45:
        return "degraded"
    return "critical"


def project_rebalance_window(score: int, jitter_ms: float = 0.0) -> int:
    normalized_jitter = int(_clamp(jitter_ms, 0, 250))
    baseline = 220 if score >= 70 else 360
    return baseline + normalized_jitter
