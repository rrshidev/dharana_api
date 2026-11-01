import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import unquote

from app.config import settings
from app.services import rate_limit
from app.services.media_urls import verify_media_url
from app.database import init_db
from app.routers import (
    auth, asanas, categories, media, subscription, content,
    profile, favorites, practice, sequences, videos, admin, admin_bot, payments,
)

# Пространство имён "broadcast" занято стандартной функцией Python — импортируем модуль отдельно.
from app.routers import broadcast as broadcast_router


def _setup_logging() -> None:
    """Структурированное логирование: struture {level} {name}: {message}, в одну строку."""
    class _CompactFormatter(logging.Formatter):
        def format(self, record):
            # однострочный формат с levelname/logger name, безопасно для JSON-строк
            return f"{record.levelname} {record.name}: {record.getMessage()}"

    handler = logging.StreamHandler()
    handler.setFormatter(_CompactFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


_setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting dharana-api...")
    if (
        settings.JWT_SECRET == "your-super-secret-key-change-in-production"
        or len(settings.JWT_SECRET) < 16
    ):
        raise RuntimeError("JWT_SECRET is not configured securely")
    await init_db()
    logger.info("Database initialized")

    from app.services.video_service import video_service
    from app.database import async_session
    async with async_session() as db:
        try:
            stats = await video_service.scan_and_sync(db)
            logger.info(f"Video scan complete: {stats}")
        except Exception as e:
            logger.error(f"Video scan failed: {e}")

    yield
    logger.info("Shutting down dharana-api...")


app = FastAPI(
    title="Dharana API",
    description="Yoga Encyclopedia API for Dharana mobile app",
    version="1.0.0",
    lifespan=lifespan,
    # В проде документация выключена (открытый /docs упрощает разведку).
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.services.monitoring import MonitoringMiddleware, exception_handler

# Мониторинг: замер latency, счётчики, request-id, алерт на 5xx.
app.add_middleware(MonitoringMiddleware)
app.add_exception_handler(Exception, exception_handler)

MEDIA_URL_PREFIX = "/api/v1/media/videos/"


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Подписанные ссылки на видео + rate limit + security headers."""
    path = unquote(request.url.path)

    if path.startswith(MEDIA_URL_PREFIX):
        rel = path[len(MEDIA_URL_PREFIX):]
        if not verify_media_url(
            rel, request.query_params.get("exp"), request.query_params.get("sig")
        ):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)

    ip = rate_limit.client_ip(
        dict(request.headers),
        request.client.host if request.client else None,
    )
    retry_after = rate_limit.check(request.method, path, ip)
    if retry_after is not None:
        return JSONResponse(
            {"detail": "TOO_MANY_REQUESTS"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Server"] = "dharana"
    return response

app.include_router(auth.router, prefix="/api/v1")
app.include_router(asanas.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(media.router, prefix="/api/v1")
app.include_router(subscription.router, prefix="/api/v1")
app.include_router(content.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(favorites.router, prefix="/api/v1")
app.include_router(practice.router, prefix="/api/v1")
app.include_router(sequences.router, prefix="/api/v1")
app.include_router(videos.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(admin_bot.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(broadcast_router.router, prefix="/api/v1")
from app.routers import metrics as metrics_router
app.include_router(metrics_router.router, prefix="/api/v1")

import os
uploads_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

MEDIA_DIR = settings.MEDIA_DIR
if os.path.exists(MEDIA_DIR):
    app.mount("/api/v1/media/videos", StaticFiles(directory=MEDIA_DIR), name="media_videos")


@app.get("/api/v1/health")
async def health_check():
    return {"status": "ok", "service": "dharana-api"}
