#!/usr/bin/env python3
"""rate_limit.py — token bucket per client key with optional persistence.

Stdlib only. Thread-safe across threads; persistence makes it
restart-safe (attacker cannot reset their quota by triggering a deploy).

Time base: wall-clock `time.time()`, so we can persist the
last-refill timestamp and resume across restarts. We clamp negative
elapsed time (NTP rollback) to zero to avoid token overflow.

Public API:
    limiter = TokenBucket(capacity, refill_per_sec, snapshot_path=...)
    allowed, retry_after = limiter.check(key)
    limiter.save()  # optional; auto-saves on a debounce
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path


class TokenBucket:
    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        max_keys: int = 50_000,
        snapshot_path: Path | None = None,
        snapshot_interval_sec: float = 5.0,
    ) -> None:
        if capacity <= 0 or refill_per_sec <= 0:
            raise ValueError("capacity and refill_per_sec must be positive")
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.max_keys = max_keys
        self.snapshot_path = Path(snapshot_path) if snapshot_path else None
        self.snapshot_interval_sec = snapshot_interval_sec
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._last_snapshot = 0.0
        self._dirty = False
        if self.snapshot_path:
            self._load()

    def _load(self) -> None:
        if not self.snapshot_path or not self.snapshot_path.exists():
            return
        try:
            data = json.loads(self.snapshot_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or "buckets" not in data:
            return
        # Only restore entries whose schema matches; ignore anything weird.
        for key, entry in data["buckets"].items():
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            try:
                tokens = float(entry[0])
                last = float(entry[1])
            except (TypeError, ValueError):
                continue
            tokens = max(0.0, min(self.capacity, tokens))
            self._buckets[key] = (tokens, last)
        # On restart, last_snapshot is fresh; first write will only fire after interval.
        self._last_snapshot = time.time()

    def save(self) -> None:
        """Persist snapshot immediately. Caller should hold no locks."""
        if not self.snapshot_path:
            return
        with self._lock:
            data = {
                "saved_at": time.time(),
                "capacity": self.capacity,
                "refill_per_sec": self.refill_per_sec,
                "buckets": {k: [t, l] for k, (t, l) in self._buckets.items()},
            }
            self._last_snapshot = data["saved_at"]
            self._dirty = False
        # Write outside the lock to keep critical section short.
        tmp = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.snapshot_path)

    def _maybe_snapshot(self) -> None:
        if not self.snapshot_path or not self._dirty:
            return
        if (time.time() - self._last_snapshot) >= self.snapshot_interval_sec:
            # Release the lock before disk I/O via save() — save reacquires.
            pass  # caller invokes after returning

    def check(self, key: str) -> tuple[bool, float]:
        """Consume 1 token for key. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        should_snapshot = False
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            # Clamp negative elapsed (NTP rollback) so refill is monotonic-ish.
            elapsed = max(0.0, now - last)
            tokens = min(self.capacity, tokens + elapsed * self.refill_per_sec)
            if tokens >= 1.0:
                tokens -= 1.0
                allowed = True
                retry_after = 0.0
            else:
                needed = 1.0 - tokens
                retry_after = needed / self.refill_per_sec
                allowed = False
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            self._evict_if_needed()
            self._dirty = True
            if self.snapshot_path and (now - self._last_snapshot) >= self.snapshot_interval_sec:
                should_snapshot = True
        if should_snapshot:
            try:
                self.save()
            except OSError:
                pass  # don't kill a request because the disk is grumpy
        return allowed, retry_after

    def peek(self, key: str) -> float:
        """Return the tokens currently available for `key` WITHOUT consuming.

        Refill is applied read-only (the stored bucket state is untouched), so
        callers can gate on quota before deciding whether an attempt should
        count. Used by the founder-token gate to make lockout failures-only:
        successful auth never consumes, so `peek` + consume-on-failure bounds
        brute force without ever throttling the legitimate holder.
        """
        now = time.time()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            elapsed = max(0.0, now - last)
            return min(self.capacity, tokens + elapsed * self.refill_per_sec)

    def _evict_if_needed(self) -> None:
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)


def truncate_ip(addr: str) -> str:
    """Truncate an IP for logging: /24 for IPv4, /48 for IPv6.

    Privacy posture: we keep enough to spot abuse patterns,
    not enough to identify individuals.
    """
    if not addr:
        return ""
    if ":" in addr:
        parts = addr.split(":")
        return ":".join(parts[:3]) + "::/48"
    parts = addr.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0/24"
    return "unknown"
