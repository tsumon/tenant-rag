"""Prometheus-compatible metrics registry and dynamic per-tenant token-bucket rate limiter."""
from __future__ import annotations

import threading
import time
from typing import Any


class DynamicRateLimiter:
    """Thread-safe per-tenant token bucket supporting runtime RPM updates."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, tenant_id: str, rpm: int) -> tuple[bool, dict[str, int]]:
        capacity = float(max(1, int(rpm)))
        refill_per_sec = capacity / 60.0
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 10000:
                self._buckets = {
                    k: v for k, v in self._buckets.items() if now - v[1] < 3600.0
                }
            tokens, last = self._buckets.get(tenant_id, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill_per_sec)
            if tokens < 1.0:
                self._buckets[tenant_id] = (tokens, now)
                retry_after = int(max(1.0, (1.0 - tokens) / refill_per_sec + 0.999))
                return False, {
                    "limit": int(capacity),
                    "remaining": 0,
                    "retry_after": retry_after,
                }
            remaining = tokens - 1.0
            self._buckets[tenant_id] = (remaining, now)
            return True, {
                "limit": int(capacity),
                "remaining": int(remaining),
                "retry_after": 0,
            }


class MetricsRegistry:
    """In-process metrics exported as JSON and Prometheus text exposition format."""

    _BUCKETS = (5.0, 15.0, 50.0, 150.0, 500.0, 2000.0)

    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._http_requests: dict[tuple[str, str, int], int] = {}
        self._latencies_ms: list[float] = []
        self._hist_counts = [0] * (len(self._BUCKETS) + 1)
        self._hist_sum = 0.0
        self._hist_total = 0
        self._searches = 0
        self._abstentions = 0
        self._ingestions = 0
        self._rate_limited = 0
        self._auth_denied = 0

    def observe_http(self, method: str, route: str, status: int, latency_ms: float) -> None:
        with self._lock:
            key = (method.upper(), route, int(status))
            self._http_requests[key] = self._http_requests.get(key, 0) + 1
            if status == 429:
                self._rate_limited += 1
            elif status in (401, 403):
                self._auth_denied += 1
            val = max(0.0, float(latency_ms))
            self._latencies_ms.append(val)
            if len(self._latencies_ms) > 2000:
                self._latencies_ms = self._latencies_ms[-1000:]
            self._hist_sum += val
            self._hist_total += 1
            for idx, bound in enumerate(self._BUCKETS):
                if val <= bound:
                    self._hist_counts[idx] += 1
            self._hist_counts[-1] += 1

    def inc_search(self, *, abstained: bool = False) -> None:
        with self._lock:
            self._searches += 1
            if abstained:
                self._abstentions += 1

    def inc_ingestion(self) -> None:
        with self._lock:
            self._ingestions += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total_req = sum(self._http_requests.values())
            lats = sorted(self._latencies_ms)
            p50 = lats[int(0.50 * (len(lats) - 1))] if lats else 0.0
            p95 = lats[int(0.95 * (len(lats) - 1))] if lats else 0.0
            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "total_requests": total_req,
                "total_searches": self._searches,
                "total_abstentions": self._abstentions,
                "total_ingestions": self._ingestions,
                "rate_limited_total": self._rate_limited,
                "auth_denied_total": self._auth_denied,
                "latency_p50_ms": round(p50, 2),
                "latency_p95_ms": round(p95, 2),
            }

    def prometheus_text(self) -> str:
        with self._lock:
            lines = [
                "# HELP tenant_rag_http_requests_total Total HTTP requests processed by route and status.",
                "# TYPE tenant_rag_http_requests_total counter",
            ]
            for (method, route, status), count in sorted(self._http_requests.items()):
                safe_route = route.replace('"', '\\"')
                lines.append(
                    f'tenant_rag_http_requests_total{{method="{method}",route="{safe_route}",status="{status}"}} {count}'
                )
            lines.extend([
                "# HELP tenant_rag_search_total Total enterprise search executions.",
                "# TYPE tenant_rag_search_total counter",
                f"tenant_rag_search_total {self._searches}",
                "# HELP tenant_rag_abstention_total Total searches/chats gated by abstention.",
                "# TYPE tenant_rag_abstention_total counter",
                f"tenant_rag_abstention_total {self._abstentions}",
                "# HELP tenant_rag_ingestion_total Total document ingestions.",
                "# TYPE tenant_rag_ingestion_total counter",
                f"tenant_rag_ingestion_total {self._ingestions}",
                "# HELP tenant_rag_rate_limited_total Total HTTP 429 rate-limited responses.",
                "# TYPE tenant_rag_rate_limited_total counter",
                f"tenant_rag_rate_limited_total {self._rate_limited}",
                "# HELP tenant_rag_auth_denied_total Total HTTP 401/403 authentication or authorization denials.",
                "# TYPE tenant_rag_auth_denied_total counter",
                f"tenant_rag_auth_denied_total {self._auth_denied}",
                "# HELP tenant_rag_request_duration_ms HTTP request latency histogram in milliseconds.",
                "# TYPE tenant_rag_request_duration_ms histogram",
            ])
            for idx, bound in enumerate(self._BUCKETS):
                lines.append(f'tenant_rag_request_duration_ms_bucket{{le="{bound}"}} {self._hist_counts[idx]}')
            lines.append(f'tenant_rag_request_duration_ms_bucket{{le="+Inf"}} {self._hist_counts[-1]}')
            lines.append(f"tenant_rag_request_duration_ms_sum {round(self._hist_sum, 3)}")
            lines.append(f"tenant_rag_request_duration_ms_count {self._hist_total}")
            return "\n".join(lines) + "\n"
