"""Безопасность входа через Telegram-бота: код только у бота, одноразовый,
с TTL, без утечки «неверный/просроченный», плюс нормализация путей в лимитере."""

from datetime import datetime, timedelta

import pytest


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from app.database import Base, engine
    from app.main import app

    await engine.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


BOT_HEADERS = {"X-Bot-Key": "test-bot-admin-key-123"}


async def _create_code(client, telegram_id=800001, name="TG User"):
    r = await client.post(
        "/api/v1/auth/telegram/create-code",
        json={"telegram_id": telegram_id, "name": name},
        headers=BOT_HEADERS,
    )
    assert r.status_code == 200, r.text
    return r.json()["code"]


async def test_create_code_requires_bot_key(client):
    r = await client.post(
        "/api/v1/auth/telegram/create-code",
        json={"telegram_id": 800002, "name": "No Key"},
    )
    assert r.status_code == 403


async def test_create_and_verify_returns_token(client):
    code = await _create_code(client)
    assert len(code) == 6 and code.isdigit()

    r = await client.post("/api/v1/auth/telegram/verify", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


async def test_code_is_single_use(client):
    code = await _create_code(client, telegram_id=800003)
    first = await client.post("/api/v1/auth/telegram/verify", json={"code": code})
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/telegram/verify", json={"code": code})
    assert second.status_code == 400
    assert second.json()["detail"] == "Invalid or expired code"


async def test_unknown_code_is_generic(client):
    r = await client.post("/api/v1/auth/telegram/verify", json={"code": "000000"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired code"


async def test_expired_code_rejected(client):
    from app.database import async_session
    from app.models.models import PendingTelegramAuth

    async with async_session() as db:
        db.add(
            PendingTelegramAuth(
                code="424242",
                telegram_id=800004,
                created_at=datetime.utcnow() - timedelta(minutes=11),
            )
        )
        await db.commit()

    r = await client.post("/api/v1/auth/telegram/verify", json={"code": "424242"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired code"


async def test_new_code_invalidates_previous_pending_code(client):
    first = await _create_code(client, telegram_id=800005)
    second = await _create_code(client, telegram_id=800005)
    assert first != second

    old = await client.post("/api/v1/auth/telegram/verify", json={"code": first})
    assert old.status_code == 400
    fresh = await client.post("/api/v1/auth/telegram/verify", json={"code": second})
    assert fresh.status_code == 200


def test_rate_limit_path_normalization():
    from app.services.rate_limit import _normalize_path

    assert _normalize_path("/api/v1/auth/register/") == "/api/v1/auth/register"
    assert _normalize_path("//api//v1//auth/register") == "/api/v1/auth/register"
    assert _normalize_path("/") == "/"
