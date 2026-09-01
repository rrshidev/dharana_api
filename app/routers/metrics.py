"""Публичный /metrics в Prometheus text format (для VictoriaMetrics scraper).

Отдаёт агрегатные счётчики запросов/ошибок/латентности. Как /health —
без auth, т.к. его скрейпят внешние сборщики (Prometheus/VictoriaMetrics).
"""

from fastapi import APIRouter, Response

from app.services.metrics_service import metrics_collector

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics():
    body = await metrics_collector.to_prometheus()
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )