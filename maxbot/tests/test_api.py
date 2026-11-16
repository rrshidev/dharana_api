# -*- coding: utf-8 -*-
"""Тесты клиента Dharana API: заголовки и разбор ответов."""

import json

import httpx
import pytest
from maxbot.api import DharanaApi


async def make_api(handler, bot_key: str = "secret") -> DharanaApi:
    api = DharanaApi("http://test", bot_key=bot_key)
    await api._client.aclose()
    api._client = httpx.AsyncClient(
        base_url="http://test/api/v1",
        headers={"X-Max-Bot-Key": bot_key},
        transport=httpx.MockTransport(handler),
    )
    return api


async def test_register_user_sends_bot_key_and_profile():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("X-Max-Bot-Key")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "user": {"language": "en"}})

    api = await make_api(handler)
    try:
        user = await api.register_user(
            max_id=42,
            name="Иван",
            username="ivan",
            language="ru",
        )
    finally:
        await api.close()

    assert user == {"language": "en"}
    assert seen["path"] == "/api/v1/auth/max/bot/register"
    assert seen["key"] == "secret"
    body = json.loads(str(seen["body"]))
    assert body["max_id"] == 42
    assert body["name"] == "Иван"
    assert body["username"] == "ivan"
    assert body["language"] == "ru"


async def test_language_methods():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"language": "ru"})
        return httpx.Response(200, json={"language": "en"})

    api = await make_api(handler)
    try:
        assert await api.get_language(7) == "ru"
        assert await api.set_language(7, "en") == "en"
    finally:
        await api.close()


async def test_register_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Forbidden"})

    api = await make_api(handler)
    try:
        assert await api.register_user(max_id=1) is None
    finally:
        await api.close()


async def test_register_returns_none_on_invalid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    api = await make_api(handler)
    try:
        assert await api.register_user(max_id=1) is None
    finally:
        await api.close()


@pytest.mark.parametrize("method", ["get_language", "set_language"])
async def test_language_returns_none_on_http_error(method: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    api = await make_api(handler)
    try:
        if method == "get_language":
            assert await api.get_language(1) is None
        else:
            assert await api.set_language(1, "ru") is None
    finally:
        await api.close()
