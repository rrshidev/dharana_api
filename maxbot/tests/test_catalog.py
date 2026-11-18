# -*- coding: utf-8 -*-
"""Тесты клиента каталога: кеш, разбор ответов, фото."""

import httpx

from maxbot.catalog import CatalogClient


async def make_catalog(handler, ttl: float = 300.0) -> CatalogClient:
    catalog = CatalogClient("http://test", ttl=ttl)
    await catalog._http.aclose()
    catalog._http = httpx.AsyncClient(
        base_url="http://test/api/v1",
        transport=httpx.MockTransport(handler),
    )
    return catalog


async def test_categories_are_cached():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[{"id": "stay+", "asana_count": 38}])

    catalog = await make_catalog(handler)
    try:
        first = await catalog.categories("ru")
        second = await catalog.categories("ru")
        other_lang = await catalog.categories("en")
    finally:
        await catalog.close()

    assert first == second == [{"id": "stay+", "asana_count": 38}]
    assert len(calls) == 2  # ru — один запрос, en — свой ключ кеша
    assert "lang=ru" in calls[0]
    assert "lang=en" in calls[1]
    assert other_lang == first


async def test_category_asanas_unwraps_items():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/categories/stay+/asanas"
        return httpx.Response(
            200,
            json={"category_id": "stay+", "items": [{"name": "Тадасана"}]},
        )

    catalog = await make_catalog(handler)
    try:
        items = await catalog.category_asanas("stay+", "ru")
    finally:
        await catalog.close()

    assert items == [{"name": "Тадасана"}]


async def test_asana_treats_error_payload_as_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "Asana not found"})

    catalog = await make_catalog(handler)
    try:
        assert await catalog.asana("Нет такой", "ru") is None
    finally:
        await catalog.close()


async def test_search_passes_query_and_limit():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"total": 0, "items": []})

    catalog = await make_catalog(handler)
    try:
        data = await catalog.search("Тада", "ru", limit=5)
    finally:
        await catalog.close()

    assert data == {"total": 0, "items": []}
    assert seen["search"] == "Тада"
    assert seen["limit"] == "5"


async def test_http_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    catalog = await make_catalog(handler)
    try:
        assert await catalog.categories("ru") is None
        assert await catalog.basics("ru") is None
    finally:
        await catalog.close()


async def test_photo_strips_api_prefix_and_encodes_path():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.raw_path.decode()
        return httpx.Response(200, content=b"\xff\xd8\xff jpeg")

    catalog = await make_catalog(handler)
    try:
        data = await catalog.photo(
            "/api/v1/media/photos/sit_lie+/Агни Стамбхасана.jpg"
        )
    finally:
        await catalog.close()

    assert data == b"\xff\xd8\xff jpeg"
    assert seen["path"].startswith("/api/v1/media/photos/sit_lie+/")
    assert "%D0%90" in seen["path"]  # «А» в percent-encoding
    assert " " not in seen["path"]


async def test_photo_rejects_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    catalog = await make_catalog(handler)
    try:
        assert await catalog.photo("/api/v1/media/photos/a/b.jpg") is None
    finally:
        await catalog.close()
