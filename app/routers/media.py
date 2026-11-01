import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.services.media_urls import verify_receipt_url

router = APIRouter(prefix="/media", tags=["media"])

# Раздаём только изображения — иначе через media можно было вытащить .db/.env.
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

RECEIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "receipts")
_RECEIPT_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
_RECEIPT_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


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


@router.get("/receipts/{filename}")
async def get_receipt(filename: str, exp: str | None = None, sig: str | None = None):
    """Чек оплаты только по подписанной ссылке (см. sign_receipt_url)."""
    if not verify_receipt_url(filename, exp, sig):
        raise HTTPException(status_code=403, detail="Forbidden")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _RECEIPT_EXT:
        raise HTTPException(status_code=404, detail="Not found")

    file_path = _safe_file(RECEIPT_DIR, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(
        file_path,
        media_type=_RECEIPT_MEDIA_TYPES[ext],
        headers={"Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"'},
    )
