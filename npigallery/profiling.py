"""Opt-in timing aid for start-up and scrolling. Does nothing unless NPI_PROFILE=1 is set.

    set NPI_PROFILE=1            (Windows cmd)       $env:NPI_PROFILE=1   (PowerShell)
    python main.py

The log goes to stderr and to `profile.log` in the cache folder (next to thumbnails.db). It holds:

* `mark` / `span` lines: when start-up steps happen and how long they take (time since the process began);
* `slow paint` lines: a gallery repaint that took 16 ms or more;
* `STALL` blocks: the UI thread did not answer for 150 ms or more. Each lists where the UI thread
  was and what every other Python thread was doing at that moment, which shows who held it up;
* a once-per-second summary of thumbnails loaded/built and the loader queue (first 30 s only).

Every hook costs one boolean test when profiling is off.
"""

import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import contextmanager, nullcontext

ENABLED = os.environ.get("NPI_PROFILE") == "1"

STALL_SECONDS = 0.15
HEARTBEAT_MS = 10
REPORT_SECONDS = 30.0
SLOW_PAINT_MS = 16.0

_START = time.perf_counter()
_lock = threading.Lock()
_counters: dict[str, float] = {}
_last_report: dict[str, float] = {}
_probes: dict[str, Callable[[], object]] = {}
_seen: set[str] = set()
_log_file = None
_heartbeat = time.perf_counter()
_keep: list = []  # timers / threads must stay referenced
_NULL = nullcontext()


def _write(line: str) -> None:
    global _log_file
    text = f"+{(time.perf_counter() - _START) * 1000:9.1f} ms  {line}"
    with _lock:
        print(text, file=sys.stderr, flush=True)
        try:
            if _log_file is None:
                from .config import cache_directory

                _log_file = open(cache_directory() / "profile.log", "w", encoding="utf-8")
            _log_file.write(text + "\n")
            _log_file.flush()
        except OSError:
            pass


def mark(label: str) -> None:
    if ENABLED:
        _write(f"[{threading.current_thread().name}] {label}")


def once(label: str) -> None:
    """`mark`, but only the first time this label is seen."""
    if ENABLED and label not in _seen:
        _seen.add(label)
        mark(label)


@contextmanager
def _timed(label: str, min_ms: float):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        if elapsed >= min_ms:
            mark(f"{label}: {elapsed:.1f} ms")


def span(label: str, min_ms: float = 0.0):
    """`with profiling.span("what"):` logs how long the block took (only when it took `min_ms` or more)."""
    return _timed(label, min_ms) if ENABLED else _NULL


def tick() -> float:
    return time.perf_counter() if ENABLED else 0.0


def slow(label: str, started: float, threshold_ms: float = SLOW_PAINT_MS, detail: str = "") -> None:
    if not ENABLED or not started:
        return
    elapsed = (time.perf_counter() - started) * 1000
    if elapsed >= threshold_ms:
        mark(f"slow {label}: {elapsed:.1f} ms {detail}".rstrip())


def count(name: str, amount: float = 1.0) -> None:
    if ENABLED:
        with _lock:
            _counters[name] = _counters.get(name, 0.0) + amount


def probe(name: str, read: Callable[[], object]) -> None:
    if ENABLED:
        _probes[name] = read


def _describe_threads() -> str:
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    main_id = threading.main_thread().ident
    own_id = threading.get_ident()  # the watchdog itself
    lines = []
    for ident, frame in sys._current_frames().items():
        if ident == own_id:
            continue
        stack = traceback.extract_stack(frame)
        recent = " <- ".join(
            f"{os.path.basename(entry.filename)}:{entry.lineno} {entry.name}" for entry in reversed(stack[-4:])
        )
        label = "UI thread" if ident == main_id else names.get(ident, f"thread {ident}")
        lines.append(f"      {label}: {recent}")
    # UI thread first, then the rest
    lines.sort(key=lambda line: 0 if "UI thread" in line else 1)
    return "\n".join(lines)


def _watch() -> None:
    reported = 0
    next_report_at = 0.0
    while True:
        time.sleep(0.02)
        late = time.perf_counter() - _heartbeat
        if late < STALL_SECONDS:
            reported = 0
            continue
        if reported >= 4 or time.perf_counter() < next_report_at:
            continue
        reported += 1
        next_report_at = time.perf_counter() + 0.4
        _write(f"STALL: UI thread silent for {late * 1000:.0f} ms. Threads right now:\n{_describe_threads()}")


def _report() -> None:
    if time.perf_counter() - _START > REPORT_SECONDS:
        return
    with _lock:
        delta = {name: value - _last_report.get(name, 0.0) for name, value in _counters.items()}
        _last_report.update(_counters)
    parts = [f"{name}={value:g}" for name, value in sorted(delta.items()) if value]
    tasks = delta.get("thumb_tasks", 0.0)
    if tasks:
        parts.append(f"avg_task={delta.get('thumb_task_ms', 0.0) / tasks:.1f}ms")
    for name, read in sorted(_probes.items()):
        try:
            parts.append(f"{name}={read()}")
        except Exception:
            pass
    if parts:
        mark("per second: " + "  ".join(parts))


def install_monitors(app) -> None:
    """Start the heartbeat, the stall watchdog and the per-second report (call once the window is up)."""
    if not ENABLED or _keep:
        return
    from PySide6.QtCore import QTimer

    def beat() -> None:
        global _heartbeat
        _heartbeat = time.perf_counter()

    beat_timer = QTimer(app)
    beat_timer.timeout.connect(beat)
    beat_timer.start(HEARTBEAT_MS)
    report_timer = QTimer(app)
    report_timer.timeout.connect(_report)
    report_timer.start(1000)
    watchdog = threading.Thread(target=_watch, name="npi-profile-watchdog", daemon=True)
    beat()
    watchdog.start()
    _keep.extend([beat_timer, report_timer, watchdog])
    mark("profiling on: log is profile.log in the cache folder")