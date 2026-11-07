"""In-memory per-IP rate limiter with temporary blocking for abuse prevention."""

from __future__ import annotations

import asyncio
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


def get_client_ip(request_headers: dict[str, str], client_host: str | None = None) -> str:
    """Extract client IP address from proxy headers or client host."""
    forwarded = request_headers.get("x-forwarded-for") or request_headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For: client, proxy1, proxy2...
        return forwarded.split(",")[0].strip()

    real_ip = request_headers.get("x-real-ip") or request_headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    return client_host or "127.0.0.1"
