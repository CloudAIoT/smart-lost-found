"""Rolling record of Gemini call timings, so latency can be read instead of guessed.

Only labels that may be published are kept: an operation name, a model name, a duration and an
outcome code. Prompts, item descriptions, API keys and provider bodies never enter this module.
"""

from collections import Counter, deque
from dataclasses import dataclass
from threading import Lock
from time import time


DEFAULT_WINDOW_SECONDS = 300
CAPACITY = 500


@dataclass(frozen=True)
class Call:
    at: float
    operation: str
    model: str
    latency_ms: int
    outcome: str


_calls: deque[Call] = deque(maxlen=CAPACITY)
_lock = Lock()


def record(operation: str, model: str, latency_ms: int, outcome: str) -> None:
    with _lock:
        _calls.append(Call(time(), operation, model, latency_ms, outcome))


def clear() -> None:
    with _lock:
        _calls.clear()


def _percentile(values: list[int], share: float) -> int:
    # Nearest rank over a short window; a sampling estimate is enough to spot a slow provider.
    return values[min(len(values) - 1, int(len(values) * share))]


def summary(window_seconds: float = DEFAULT_WINDOW_SECONDS) -> dict:
    cutoff = time() - window_seconds
    with _lock:
        recent = [call for call in _calls if call.at >= cutoff]
    latencies = sorted(call.latency_ms for call in recent)
    operations: dict[str, dict[str, int]] = {}
    for call in recent:
        entry = operations.setdefault(call.operation, {"calls": 0, "failed": 0, "slowest_ms": 0})
        entry["calls"] += 1
        entry["failed"] += 0 if call.outcome == "ok" else 1
        entry["slowest_ms"] = max(entry["slowest_ms"], call.latency_ms)
    return {
        "window_seconds": int(window_seconds),
        "calls": len(recent),
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95),
                       "max": latencies[-1]} if latencies else None,
        "outcomes": dict(Counter(call.outcome for call in recent)),
        "models": dict(Counter(call.model for call in recent)),
        "operations": operations,
    }
