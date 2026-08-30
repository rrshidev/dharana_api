import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/photos/{category}/{filename}")
async def get_photo(category: str, filename: str):
    catalog_dir = os.path.join(settings.BOT_DATA_DIR, "catalog")
    file_path = os.path.join(catalog_dir, category, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Photo not found")

    media_type = "image/png" if filename.endswith(".png") else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/basics/{filename}")
async def get_basic_image(filename: str):
    basics_dir = os.path.join(settings.BOT_DATA_DIR, "basics")
    file_path = os.path.join(basics_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "image/png" if filename.endswith(".png") else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)
