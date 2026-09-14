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

import ipaddress
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


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def truncate_ip(addr: str) -> str:
    """Truncate an IP for logging: /24 for IPv4, /48 for IPv6.

    Privacy posture: we keep enough to spot abuse patterns,
    not enough to identify individuals.
    """
    if not addr:
        return ""
    a = addr.strip()
    # Proxies and clients sometimes emit host:port. Strip it BEFORE parsing:
    # the previous string-split implementation treated any ':' as IPv6 and
    # persisted "203.0.113.77:1234::/48" — the full IPv4 — and kept host
    # hextets of compressed IPv6 ("2001::1::/48"). Found 2026-09-13 by the
    # review of the privacy-table test that was supposed to prove this
    # function; the fix parses the address instead of slicing its text.
    if a.startswith("["):                       # "[2001:db8::1]:443"
        a = a[1:].split("]", 1)[0]
    elif a.count(":") == 1:                     # "203.0.113.77:1234"
        a = a.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        # Fail-SHARED, deliberately: every unparseable address lands in the
        # one "unknown" bucket (one anchor quota, one founder-lockout key)
        # rather than leaking its raw text as its own key, which is what the
        # string slicer did. The global counters still backstop it.
        return "unknown"
    if ip.version == 6:
        # A /48 keeps hextets 1-3, and four transition formats carry a FULL
        # IPv4 inside exactly those bits: ::ffff:a.b.c.d (mapped), 2002:AABB:
        # CCDD:: (6to4), Teredo 2001:0::/32 (server in bits 32-63, client
        # XOR'd in 96-127) and NAT64 64:ff9b::/96. Left as IPv6, the label IS
        # the address (2002:cb00:7149::/48 decodes to 203.0.113.73) or every
        # such client collapses into ::/48. Unwrap to the IPv4 and truncate
        # that instead. (Review of PR #245, 2026-09-13.)
        embedded = ip.ipv4_mapped
        if embedded is None:
            embedded = ip.sixtofour
        if embedded is None and ip.teredo is not None and not ip.teredo[0].is_unspecified:
            # Python decodes anything under 2001:0::/32 as Teredo; a real
            # Teredo address names its server, so 2001::1 stays an IPv6 /48.
            embedded = ip.teredo[1]
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            ip = embedded
    if ip.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    return str(ipaddress.ip_network(f"{ip}/48", strict=False))
