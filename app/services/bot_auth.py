"""Проверка ключей ботов (constant-time) для служебных эндпоинтов.

Ключи передаются заголовками:
  X-Bot-Key   — yoga/admin bot  (BOT_ADMIN_KEY)
  X-Timer-Key — timer bot       (TIMER_BOT_KEY)

Сравнение через hmac.compare_digest, чтобы не течь по времени.
"""

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from app.config import settings


def _check(value: Optional[str], expected: str, env_name: str) -> bool:
    if not expected:
        # Не даём тихо открыть эндпоинт, если ключ не сконфигурирован.
        raise HTTPException(status_code=500, detail=f"{env_name} not configured")
    if not value or not hmac.compare_digest(str(value), str(expected)):
        raise HTTPException(status_code=403, detail="Invalid bot key")
    return True


def require_bot_key(x_bot_key: Optional[str] = Header(default=None)) -> bool:
    return _check(x_bot_key, settings.BOT_ADMIN_KEY, "BOT_ADMIN_KEY")


def require_timer_key(x_timer_key: Optional[str] = Header(default=None)) -> bool:
    return _check(x_timer_key, settings.TIMER_BOT_KEY, "TIMER_BOT_KEY")
