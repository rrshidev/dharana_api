"""Подписанные ссылки на видео.

Статическая раздача /api/v1/media/videos раньше позволяла скачать премиум-файл
по прямому URL. Теперь ссылка содержит exp+sig (HMAC от пути), а middleware
проверяет подпись и срок. Ссылку выдаёт только эндпоинт, который уже проверил
подписку пользователя.
"""

import hashlib
import hmac
import os
import time
from typing import Optional
from urllib.parse import quote

from app.config import settings

RECEIPTS_URL_PREFIX = "/api/v1/media/receipts/"


def _digest(relpath: str, exp: int) -> str:
    msg = f"{relpath}|{exp}".encode("utf-8")
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def sign_media_url(relpath: str) -> str:
    clean = relpath.lstrip("/")
    exp = int(time.time()) + max(60, int(settings.MEDIA_URL_TTL_SECONDS))
    sig = _digest(clean, exp)
    return f"/api/v1/media/videos/{quote(clean, safe='/')}?exp={exp}&sig={sig}"


def verify_media_url(relpath: str, exp: str | None, sig: str | None) -> bool:
    clean = relpath.lstrip("/")
    if not exp or not sig:
        return False
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    return hmac.compare_digest(_digest(clean, exp_int), sig)


def sign_receipt_url(stored: Optional[str]) -> Optional[str]:
    """Подписанная ссылка на чек оплаты.

    Раньше квитанции лежали в публичном `/uploads/receipts/...`: UUID спасал
    только от перебора, но ссылка утекала из логов/Referer. Теперь файл отдаёт
    только этот эндпоинт и только с валидной подписью.
    """
    if not stored:
        return None
    filename = os.path.basename(str(stored).rstrip("/"))
    if not filename:
        return None
    exp = int(time.time()) + max(60, int(settings.MEDIA_URL_TTL_SECONDS))
    sig = _digest(filename, exp)
    return f"{RECEIPTS_URL_PREFIX}{quote(filename)}?exp={exp}&sig={sig}"


def verify_receipt_url(filename: str, exp: str | None, sig: str | None) -> bool:
    clean = os.path.basename(filename)
    if not clean or not exp or not sig:
        return False
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    return hmac.compare_digest(_digest(clean, exp_int), sig)
