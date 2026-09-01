"""HTTP-мониторинг middleware: замер latency, счётчики, request-id, алерты на 5xx.

Также содержит глобальный обработчик исключений (5xx), который шлёт админу
уведомление в Telegram через notify_service.notify_error с дедупликацией,
чтобы не спамить повторяющимися ошибками.
"""
import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.services.metrics_service import metrics_collector
from app.services.notify_service import notify_error

logger = logging.getLogger(__name__)

# пути, которые не логируем считая "шумными" (health-check и т.п.)
IGNORED_PATHS = {"/api/v1/health", "/api/v1/media/videos"}


class _AlertCooldown:
    """Минимальный интервал между алертами для одного и того же пути."""

    def __init__(self, interval_seconds: float = 300.0) -> None:
        self.interval = interval_seconds
        self.last: dict = defaultdict(float)

    def allowed(self, key: str, now: float) -> bool:
        if now - self.last[key] >= self.interval:
            self.last[key] = now
            return True
        return False


_alert_cooldown = _AlertCooldown(interval_seconds=300.0)


class MonitoringMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        path = request.url.path
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:  # noqa: BLE001
            # обрабатываем и отдаём 500 через app.exception_handler
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            status_code = getattr(response, "status_code", 500)
            await metrics_collector.record(path, status_code, duration_ms)
            _log_request(request_id, path, request.method, status_code, duration_ms)


def _log_request(request_id, path, method, status_code, duration_ms) -> None:
    if path in IGNORED_PATHS:
        return
    record = {
        "event": "request",
        "request_id": request_id,
        "method": method,
        "path": path,
        "status": status_code,
        "duration_ms": round(duration_ms, 2),
    }
    if status_code >= 500:
        logger.error(json.dumps(record, ensure_ascii=False))
    else:
        logger.info(json.dumps(record, ensure_ascii=False))


async def exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик непойманных исключений -> 500 + алерт админу."""
    now = time.time()
    path = request.url.path
    request_id = getattr(request.state, "request_id", "")
    # metrics уже посчитает 5xx в middleware (response.status_code там 500)
    if _alert_cooldown.allowed(path, now):
        await notify_error(
            service=path,
            error=f"Unhandled exception\nRequest ID: {request_id}\n{type(exc).__name__}: {exc}",
        )
    logger.exception(
        json.dumps(
            {
                "event": "unhandled_error",
                "request_id": request_id,
                "path": path,
                "exception": f"{type(exc).__name__}: {exc}",
            },
            ensure_ascii=False,
        )
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
