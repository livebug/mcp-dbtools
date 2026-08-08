"""按客户端 IP 的 QPS 限流（令牌桶 + ASGI 中间件）。

防止持有 token 的客户端无限调用打爆后端数据库：
- 每客户端一个令牌桶，按配置的 rate（每秒补充）与 burst（突发容量）放行；
- 超出限额的请求返回 HTTP 429 + Retry-After；
- 可配置豁免路径（如 /health 供负载均衡探测）。
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TokenBucket:
    """线程安全的令牌桶。"""

    def __init__(self, rate: float, capacity: int):
        self.rate = max(0.0, rate)
        self.capacity = max(1, capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        """尝试取一个令牌；成功返回 True，不足返回 False。"""
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self.capacity, self._tokens + (now - self._last) * self.rate
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def idle_seconds(self) -> float:
        """距上次被取令牌已过去的时间（秒），用于清理空闲桶。"""
        return time.monotonic() - self._last


class RateLimitMiddleware:
    """按客户端 IP 限流的 ASGI 中间件。"""

    def __init__(
        self,
        inner: Any,
        qps: float = 10.0,
        burst: int = 20,
        exempt_paths: tuple[str, ...] = ("/health",),
        max_clients: int = 10000,
    ):
        self.inner = inner
        self.qps = qps
        self.burst = burst
        self.exempt_paths = exempt_paths
        self.max_clients = max(1, max_clients)
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, key: str) -> TokenBucket:
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = TokenBucket(self.qps, self.burst)
                self._buckets[key] = b
            if len(self._buckets) > self.max_clients:
                # 清理超过 idle 阈值的桶，防止字典无限增长
                for k, bb in list(self._buckets.items()):
                    if bb.idle_seconds() > 3600:
                        self._buckets.pop(k, None)
                if len(self._buckets) > self.max_clients:
                    # 兜底：清掉最久未活动的
                    oldest = min(self._buckets.items(), key=lambda kv: kv[1].idle_seconds())[0]
                    self._buckets.pop(oldest, None)
            return b

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith(self.exempt_paths):
            await self.inner(scope, receive, send)
            return
        client = scope.get("client") or ("", 0)
        key = client[0] if client and client[0] else "unknown"
        if not self._bucket(key).take():
            body = b'{"detail":"rate limit exceeded"}'
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", b"1"),
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": headers,
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.inner(scope, receive, send)
