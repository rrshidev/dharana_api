"""В-памяти счётчики запросов/ошибок/латентности для мониторинга.

Хранятся в памяти одного процесса (uvicorn single-worker). Достаточно для
основной метрики DAU/WAU/MAU и информации об эндпоинтах.
"""
import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class EndpointStats:
    requests: int = 0
    errors_4xx: int = 0
    errors_5xx: int = 0
    total_time_ms: float = 0.0
    # скользящее окно последних latency (для p95/p50)
    latencies: deque = field(default_factory=lambda: deque(maxlen=2000))


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * q))
    return round(s[idx], 2)


def _prom_line(name: str, value: float, labels: dict | None = None) -> str:
    """Строка в Prometheus text format (OpenMetrics)."""
    if labels:
        escaped = []
        for k, v in labels.items():
            # Prometheus: экранируем \ и " в значении label
            sv = str(v).replace("\\", "\\\\").replace('"', '\\"')
            escaped.append('{}="{}"'.format(k, sv))
        return name + "{" + ",".join(escaped) + "} " + str(value)
    return "{} {}".format(name, value)


class MetricsCollector:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.endpoints: Dict[str, EndpointStats] = defaultdict(EndpointStats)
        self.total_requests = 0
        self.total_5xx = 0
        self.started_at = time.time()

    async def record(self, path: str, status_code: int, duration_ms: float) -> None:
        async with self.lock:
            stats = self.endpoints[path]
            stats.requests += 1
            stats.total_time_ms += duration_ms
            stats.latencies.append(duration_ms)
            self.total_requests += 1
            if 400 <= status_code < 500:
                stats.errors_4xx += 1
            elif status_code >= 500:
                stats.errors_5xx += 1
                self.total_5xx += 1

    async def snapshot(self) -> dict:
        async with self.lock:
            eps = []
            for path, s in self.endpoints.items():
                eps.append(
                    {
                        "path": path,
                        "requests": s.requests,
                        "errors_4xx": s.errors_4xx,
                        "errors_5xx": s.errors_5xx,
                        "avg_ms": round(s.total_time_ms / s.requests, 2) if s.requests else 0.0,
                        "p50_ms": _percentile(list(s.latencies), 0.50),
                        "p95_ms": _percentile(list(s.latencies), 0.95),
                    }
                )
            eps.sort(key=lambda e: -e["requests"])
            uptime_seconds = max(0, int(time.time() - self.started_at))
            error_rate = round(self.total_5xx / self.total_requests, 4) if self.total_requests else 0.0
            return {
                "total_requests": self.total_requests,
                "total_5xx": self.total_5xx,
                "error_rate_5xx": error_rate,
                "uptime_seconds": uptime_seconds,
                "endpoints": eps,
            }

    async def to_prometheus(self) -> str:
        """Экспорт в Prometheus text format (для VictoriaMetrics scraper)."""
        async with self.lock:
            lines = [
                "# HELP dharana_requests_total Total requests served",
                "# TYPE dharana_requests_total gauge",
                _prom_line("dharana_requests_total", self.total_requests),
                "# HELP dharana_5xx_total Total 5xx responses",
                "# TYPE dharana_5xx_total gauge",
                _prom_line("dharana_5xx_total", self.total_5xx),
                "# HELP dharana_error_rate 5xx request rate (0..1)",
                "# TYPE dharana_error_rate gauge",
                _prom_line("dharana_error_rate", round(
                    self.total_5xx / self.total_requests, 4) if self.total_requests else 0.0),
                "# HELP dharana_uptime_seconds Process uptime",
                "# TYPE dharana_uptime_seconds gauge",
                _prom_line("dharana_uptime_seconds", int(time.time() - self.started_at)),
            ]
            for path, s in sorted(self.endpoints.items(), key=lambda e: -e[1].requests):
                label = {"path": path}
                lines.append(_prom_line("dharana_requests", s.requests, label))
                lines.append(_prom_line("dharana_4xx", s.errors_4xx, label))
                lines.append(_prom_line("dharana_5xx", s.errors_5xx, label))
                lines.append(_prom_line(
                    "dharana_latency_avg_ms",
                    round(s.total_time_ms / s.requests, 2) if s.requests else 0.0,
                    label,
                ))
                lines.append(_prom_line("dharana_latency_p50_ms", _percentile(list(s.latencies), 0.50), label))
                lines.append(_prom_line("dharana_latency_p95_ms", _percentile(list(s.latencies), 0.95), label))
            return "\n".join(lines) + "\n"


metrics_collector = MetricsCollector()