from dataclasses import dataclass


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


@dataclass(frozen=True)
class ThrottleSignal:
    bandwidth_kbps: float = 0.0
    packet_loss: float = 0.0
    peer_count: int = 0
    retry_burst: int = 0


def build_throttle_profile(signal: ThrottleSignal) -> dict:
    bandwidth_score = _clamp(signal.bandwidth_kbps / 120, 0, 40)
    loss_penalty = _clamp(signal.packet_loss * 180, 0, 30)
    peer_penalty = _clamp(signal.peer_count * 1.4, 0, 18)
    burst_penalty = _clamp(signal.retry_burst * 2.1, 0, 20)

    headroom = int(round(_clamp(65 + bandwidth_score - loss_penalty - peer_penalty - burst_penalty, 8, 95)))
    throttle_step = max(1, int(round((100 - headroom) / 8)))

    return {
        "headroom": headroom,
        "throttle_step": throttle_step,
        "window_ms": 120 + throttle_step * 35,
    }


def derive_backoff_schedule(throttle_step: int = 1, attempts: int = 4) -> list[int]:
    step = int(_clamp(throttle_step, 1, 12))
    count = int(_clamp(attempts, 1, 8))
    return [int((index + 1) * step * 45) for index in range(count)]
