import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/media", tags=["media"])

# Раздаём только изображения — иначе через media можно было вытащить .db/.env.
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _safe_file(base_dir: str, *parts: str) -> str:
    """Собрать путь и убедиться, что он остался внутри base_dir.

    Без этой проверки GET /media/photos/../yoga_bot.db отдавал SQLite-базу бота.
    """
    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, *parts))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise HTTPException(status_code=404, detail="Not found")
    return candidate


def _image_response(file_path: str, filename: str) -> FileResponse:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=404, detail="Not found")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Not found")
    media_type = "image/png" if ext == ".png" else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/photos/{category}/{filename}")
async def get_photo(category: str, filename: str):
    catalog_dir = os.path.join(settings.BOT_DATA_DIR, "catalog")
    file_path = _safe_file(catalog_dir, category, filename)
    return _image_response(file_path, filename)


@router.get("/basics/{filename}")
async def get_basic_image(filename: str):
    basics_dir = os.path.join(settings.BOT_DATA_DIR, "basics")
    file_path = _safe_file(basics_dir, filename)
    return _image_response(file_path, filename)
