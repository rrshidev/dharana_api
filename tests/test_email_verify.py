"""Флоу ненавязчивой верификации email: токен, повторная отправка (rate-limit)."""

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


async def test_register_creates_unverified_user(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "verify_me@example.com",
            "password": "secret123",
            "name": "Verifier",
            "website": "",
        },
    )
    assert r.status_code == 201
    token = r.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email_verified"] is False


def _make_token(email: str, user_id: int) -> str:
    from app.services.auth_service import create_email_verify_token

    return create_email_verify_token(user_id, email)


async def test_verify_email_flow(client):
    from app.database import async_session
    from app.models.models import User
    from sqlalchemy import select

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "verify_ok@example.com", "password": "secret123", "name": "T", "website": ""},
    )
    user_id = r.json()["user"]["id"]

    bad = await client.get("/api/v1/auth/verify-email", params={"token": "garbage"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "INVALID_VERIFICATION_TOKEN"

    ok = await client.get(
        "/api/v1/auth/verify-email",
        params={"token": _make_token("verify_ok@example.com", user_id)},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    async with async_session() as db:
        res = await db.execute(select(User).where(User.id == user_id))
        assert res.scalar_one().email_verified is True

    # Идемпотентность: повторный переход не ошибка.
    again = await client.get(
        "/api/v1/auth/verify-email",
        params={"token": _make_token("verify_ok@example.com", user_id)},
    )
    assert again.status_code == 200


async def test_verify_token_email_lock(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "lock_a@example.com", "password": "secret123", "name": "A", "website": ""},
    )
    user_id = r.json()["user"]["id"]

    # Токен на другой email того же юзера не должен проходить.
    resp = await client.get(
        "/api/v1/auth/verify-email",
        params={"token": _make_token("other@example.com", user_id)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_VERIFICATION_TOKEN"


async def test_resend_rate_limit(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "resend@example.com", "password": "secret123", "name": "R", "website": ""},
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post("/api/v1/auth/verify-email/send", headers=headers)
    assert first.status_code == 200
    # API_SMPT в тестах не настроен — fail-open, но HTTP 200 ('sent' false).
    assert first.json()["ok"] is True
    assert first.json()["sent"] is False

    second = await client.post("/api/v1/auth/verify-email/send", headers=headers)
    assert second.status_code == 429
    assert second.json()["detail"] == "TOO_FREQUENT"


async def test_resend_requires_auth(client):
    r = await client.post("/api/v1/auth/verify-email/send")
    assert r.status_code == 401 or r.status_code == 403