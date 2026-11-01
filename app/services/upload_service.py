"""Безопасная загрузка изображений/PDF.

- расширение берётся не из имени файла, а из реального содержимого (magic bytes);
- размер ограничен до чтения в память;
- имена генерируются UUID, пользовательские имена не используются.
"""

import os
import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_MB = 10

_EXT_BY_KIND = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "gif": ".gif",
    "pdf": ".pdf",
}


def sniff_kind(data: bytes) -> Optional[str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


async def save_upload(
    file: UploadFile,
    dest_dir: str,
    *,
    allow_pdf: bool = False,
    max_mb: int = MAX_UPLOAD_MB,
) -> tuple[str, str]:
    """Сохранить файл. Возвращает (filename, абсолютный путь)."""
    data = await read_limited(file, max_mb)
    kind = sniff_kind(data)
    if kind is None:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    if kind == "pdf" and not allow_pdf:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    os.makedirs(dest_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{_EXT_BY_KIND[kind]}"
    filepath = os.path.join(dest_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filename, filepath


async def read_limited(file: UploadFile, max_mb: int) -> bytes:
    """Прочитать upload с ограничением размера ДО полной загрузки в память."""
    limit = max(1, int(max_mb)) * 1024 * 1024
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb} MB)")
    return data


def sniff_video(data: bytes) -> bool:
    """Грубая проверка контейнера: mp4/mov, avi, mkv/webm, mpeg-ps."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return True
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return True
    if data[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return True
    return False
