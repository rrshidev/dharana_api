import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
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
