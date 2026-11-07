"""In-memory per-IP rate limiter with temporary blocking for abuse prevention."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from collections import defaultdict
from typing import Dict, List


class IPRateLimiter:
    """Sliding-window per-IP rate limiter with temporary blocking.

    Defaults:
        - 60 requests per 60-second window
        - 300-second (5 minute) temporary block if limit is exceeded
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        block_duration_seconds: int = 300,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.block_duration_seconds = block_duration_seconds

        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._blocked_until: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Reset all rate limiter state (used in tests)."""
        self._requests.clear()
        self._blocked_until.clear()

    async def check(self, ip: str) -> tuple[bool, int | None, str | None]:
        """Check if request from ip is permitted.

        Returns:
            (allowed: bool, retry_after: int | None, error_message: str | None)
        """
        now = time.monotonic()

        async with self._lock:
            # 1. Check if currently blocked
            blocked_until = self._blocked_until.get(ip, 0.0)
            if now < blocked_until:
                retry_after = max(1, int(blocked_until - now))
                return (
                    False,
                    retry_after,
                    f"Rate limit exceeded. Temporary block in effect. Try again in {retry_after}s.",
                )

            # If block expired, clean it up
            if ip in self._blocked_until:
                del self._blocked_until[ip]

            # 2. Clean up timestamps older than window
            timestamps = [t for t in self._requests[ip] if now - t < self.window_seconds]
            self._requests[ip] = timestamps

            # 3. Check threshold
            if len(timestamps) >= self.max_requests:
                # Trigger temporary block
                self._blocked_until[ip] = now + self.block_duration_seconds
                self._requests[ip].clear()
                return (
                    False,
                    self.block_duration_seconds,
                    f"Rate limit exceeded. Temporary block in effect for {self.block_duration_seconds}s.",
                )

            # 4. Record new request
            self._requests[ip].append(now)
            return True, None, None


# Global instance
rate_limiter = IPRateLimiter()


DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = (
    "127.0.0.1",
    "::1",
    "172.16.0.0/12",  # Docker container bridge networks (172.16.0.0 - 172.31.255.255)
    "10.0.0.0/8",  # Private network range
    "192.168.0.0/16",  # Private network range
)


def is_trusted_proxy(
    client_host: str | None,
    trusted_list: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Check if the connecting host is a trusted reverse proxy."""
    if not client_host:
        return False

    if trusted_list is None:
        env_trusted = os.environ.get("TRUSTED_PROXIES")
        if env_trusted:
            trusted_list = [p.strip() for p in env_trusted.split(",") if p.strip()]
        else:
            trusted_list = DEFAULT_TRUSTED_PROXIES

    # Direct match for hostname identifiers like "testclient" or "localhost"
    if client_host in trusted_list:
        return True

    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    for item in trusted_list:
        try:
            if "/" in item:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            else:
                if ip == ipaddress.ip_address(item):
                    return True
        except ValueError:
            continue

    return False


def get_client_ip(
    request_headers: dict[str, str],
    client_host: str | None = None,
    trusted_proxies: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Extract client IP address.

    Only trusts X-Forwarded-For or X-Real-IP if client_host originates from
    a trusted reverse proxy (e.g. Caddy container, docker bridge network, or localhost).
    If client_host is untrusted (or direct connection), proxy headers are ignored
    to strictly prevent IP spoofing.
    """
    if client_host and is_trusted_proxy(client_host, trusted_list=trusted_proxies):
        forwarded = request_headers.get("x-forwarded-for") or request_headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip

        real_ip = request_headers.get("x-real-ip") or request_headers.get("X-Real-IP")
        if real_ip and real_ip.strip():
            return real_ip.strip()

    return client_host or "127.0.0.1"
