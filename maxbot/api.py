# -*- coding: utf-8 -*-
"""Клиент Dharana API для MAX-бота.

Бот ходит в API с ключом ``X-Max-Bot-Key``: JWT ему не нужен и не должен
выдаваться — бот только синхронизирует профиль и язык.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DharanaApi:
    def __init__(
        self,
        base_url: str,
        api_prefix: str = "/api/v1",
        bot_key: str = "",
        timeout: float = 10.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if bot_key:
            headers["X-Max-Bot-Key"] = bot_key
        else:
            logger.warning(
                "MAX_BOT_KEY не задан — API отклонит запросы бота (403)"
            )
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}{api_prefix}",
            headers=headers,
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def register_user(
        self,
        max_id: int,
        name: str | None = None,
        username: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any] | None:
        """Создаёт или обновляет пользователя, возвращает его профиль."""
        try:
            response = await self._client.post(
                "/auth/max/bot/register",
                json={
                    "max_id": max_id,
                    "name": (name or "")[:255] or None,
                    "username": (username or "")[:100] or None,
                    "language": language,
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("MAX bot register failed for %s: %s", max_id, exc)
            return None
        return data.get("user") if isinstance(data, dict) else None

    async def get_language(self, max_id: int) -> str | None:
        try:
            response = await self._client.get(f"/auth/max/{max_id}/language")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("MAX bot get language failed for %s: %s", max_id, exc)
            return None
        return data.get("language") if isinstance(data, dict) else None

    async def set_language(self, max_id: int, language: str) -> str | None:
        try:
            response = await self._client.put(
                f"/auth/max/{max_id}/language",
                json={"language": language},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("MAX bot set language failed for %s: %s", max_id, exc)
            return None
        return data.get("language") if isinstance(data, dict) else None
