"""Bounded background pools for the AI calls that are too slow to hold a request thread.

Ownership search and photo analysis both run several provider calls with model fallback, so their
duration follows the provider rather than the request. Each gets its own pool: a photo analysis
holds two decoded images in memory, so it must not share a worker budget with text searches.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from threading import Lock


SEARCH = "ownership-search"
ANALYSIS = "photo-analysis"

_pools: dict[str, ThreadPoolExecutor] = {}
_counts: dict[str, dict[str, int]] = {}
_lock = Lock()


def pool(name: str, max_workers: int) -> ThreadPoolExecutor:
    """One pool per name per process. Its size caps concurrent provider calls and DB sessions."""
    with _lock:
        if name not in _pools:
            _pools[name] = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name)
            _counts.setdefault(name, {"accepted": 0, "running": 0})
        return _pools[name]


def shutdown() -> None:
    with _lock:
        for executor in _pools.values():
            executor.shutdown(wait=False, cancel_futures=True)
        _pools.clear()


def snapshot(name: str) -> dict[str, int]:
    with _lock:
        counts = _counts.get(name, {"accepted": 0, "running": 0})
        return {"accepted": counts["accepted"], "running": counts["running"],
                "queued": max(0, counts["accepted"] - counts["running"])}


def _count(name: str, field: str, delta: int) -> None:
    with _lock:
        counts = _counts.setdefault(name, {"accepted": 0, "running": 0})
        counts[field] += delta


@contextmanager
def running(name: str) -> Iterator[None]:
    _count(name, "running", 1)
    try:
        yield
    finally:
        _count(name, "running", -1)


def dispatch(name: str, executor: ThreadPoolExecutor | None, run: Callable[[], None], *,
             wait_seconds: float, reraise: bool) -> None:
    """Run the job on the pool and wait briefly, so a fast job still answers in its own request.

    With `reraise`, a failure inside the wait window reaches the caller as it did when the work ran
    on the request thread. Without it, the caller reads the stored outcome instead.
    """
    if executor is None:
        run()
        return
    _count(name, "accepted", 1)
    try:
        future = executor.submit(run)
    except RuntimeError:
        # The pool is shutting down; finishing on this thread still answers the caller.
        _count(name, "accepted", -1)
        run()
        return
    future.add_done_callback(lambda _: _count(name, "accepted", -1))
    try:
        future.result(timeout=wait_seconds)
    except FutureTimeout:
        pass  # Still running: the caller polls from here on.
    except Exception:
        if reraise:
            raise
