# -*- coding: utf-8 -*-
"""Клиент публичного каталога Dharana API.

Бот читает каталог асан теми же ручками, что и сайт: они не требуют JWT,
поэтому ключ бота здесь не нужен. Ответы кешируются в памяти: каталог
меняется только при деплое, а карточка асаны запрашивается на каждый клик.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300.0
LIST_TTL = 900.0

# Максимальный размер фото асаны, который имеет смысл тащить в MAX.
# Файлы в каталоге ~0.7 МБ, лимит платформы — 50 МБ.
MAX_PHOTO_BYTES = 8 * 1024 * 1024


class CatalogClient:
    def __init__(
        self,
        base_url: str,
        api_prefix: str = "/api/v1",
        timeout: float = 15.0,
        ttl: float = DEFAULT_TTL,
    ) -> None:
        self._prefix = "/" + (api_prefix or "").strip("/")
        self._ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}{self._prefix}",
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Транспорт
    # ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        ttl: float | None = None,
    ) -> Any | None:
        key = path
        if params:
            key += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

        try:
            response = await self._http.get(path, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Каталог: запрос %s не удался: %s", path, exc)
            return None

        self._cache[key] = (now + (self._ttl if ttl is None else ttl), data)
        return data

    # ------------------------------------------------------------------
    # Каталог
    # ------------------------------------------------------------------

    async def categories(self, lang: str) -> list[dict] | None:
        data = await self._get("/categories", {"lang": lang}, ttl=LIST_TTL)
        return data if isinstance(data, list) else None

    async def category_asanas(self, category_id: str, lang: str) -> list[dict] | None:
        data = await self._get(
            f"/categories/{category_id}/asanas", {"lang": lang}, ttl=LIST_TTL
        )
        if isinstance(data, dict):
            items = data.get("items")
            return items if isinstance(items, list) else None
        return data if isinstance(data, list) else None

    async def search(self, query: str, lang: str, limit: int = 10) -> dict | None:
        data = await self._get(
            "/asanas",
            {"search": query, "lang": lang, "limit": limit},
        )
        return data if isinstance(data, dict) else None

    async def asana(self, name: str, lang: str) -> dict | None:
        data = await self._get(f"/asanas/{name}", {"lang": lang})
        if not isinstance(data, dict) or data.get("error"):
            return None
        return data

    async def daily(self, lang: str) -> dict | None:
        data = await self._get("/asanas/day", {"lang": lang})
        if not isinstance(data, dict) or data.get("error"):
            return None
        return data

    async def random_asana(self, lang: str) -> dict | None:
        data = await self._get("/asanas/random", {"lang": lang})
        if not isinstance(data, dict) or data.get("error"):
            return None
        return data

    async def basics(self, lang: str) -> list[dict] | None:
        data = await self._get("/basics", {"lang": lang}, ttl=LIST_TTL)
        return data if isinstance(data, list) else None

    async def steps(self, lang: str) -> list[dict] | None:
        data = await self._get("/steps", {"lang": lang}, ttl=LIST_TTL)
        return data if isinstance(data, list) else None

    # ------------------------------------------------------------------
    # Картинки
    # ------------------------------------------------------------------

    async def photo(self, image_url: str) -> bytes | None:
        """Скачивает фото асаны по относительному URL из карточки.

        ``image_url`` приходит как ``/api/v1/media/photos/...`` — префикс API
        убираем, потому что он уже в base_url. Путь отдаём httpx как есть: он
        сам закодирует кириллицу и пробелы.
        """
        path = (image_url or "").strip()
        if not path:
            return None
        if path.startswith(self._prefix):
            path = path[len(self._prefix) :]
        if not path.startswith("/"):
            path = "/" + path

        try:
            response = await self._http.get(path)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Каталог: фото %s не скачалось: %s", path, exc)
            return None

        data = response.content
        if not data or len(data) > MAX_PHOTO_BYTES:
            logger.warning(
                "Каталог: фото %s пропущено (размер %s байт)", path, len(data)
            )
            return None
        return data
