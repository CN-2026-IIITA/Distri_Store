from dataclasses import dataclass
from math import ceil
from typing import Iterable, List


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = int(_clamp((len(sorted_values) - 1) * p, 0, len(sorted_values) - 1))
    return float(sorted_values[index])


@dataclass(frozen=True)
class PrefetchSignal:
    latencies: List[float]
    miss_rate: float
    hot_keys: List[str]
    concurrency: int = 4


@dataclass(frozen=True)
class PrefetchPlan:
    budget: int
    lane_count: int
    p90_latency: float
    lanes: List[List[str]]


def build_prefetch_plan(signal: PrefetchSignal) -> PrefetchPlan:
    p90_latency = _percentile(signal.latencies, 0.9)
    normalized_miss_rate = _clamp(signal.miss_rate, 0.0, 1.0)
    budget = int(round(_clamp(12 + normalized_miss_rate * 40 - p90_latency / 40, 4, 40)))
    lane_count = int(_clamp(signal.concurrency, 1, 8))

    selected_keys = signal.hot_keys[:budget]
    lanes: List[List[str]] = [[] for _ in range(lane_count)]

    for index, key in enumerate(selected_keys):
        lanes[index % lane_count].append(key)

    return PrefetchPlan(
        budget=budget,
        lane_count=lane_count,
        p90_latency=p90_latency,
        lanes=lanes,
    )


def estimate_warmup_window_ms(plan: PrefetchPlan) -> int:
    effective_lanes = int(_clamp(plan.lane_count, 1, 8))
    batches = ceil(int(_clamp(plan.budget, 0, 200)) / effective_lanes)
    return batches * int(_clamp(round(plan.p90_latency), 10, 500))
