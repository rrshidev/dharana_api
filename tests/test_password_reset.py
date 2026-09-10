"""Флоу сброса пароля: запрос по email, rate-limit, анти-энумерация, смена пароля."""

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


async def _register(client, email="reset@example.com", password="secret123"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "R", "website": ""},
    )
    assert r.status_code == 201
    return r.json()["user"]["id"]


def _make_token(email: str, user_id: int) -> str:
    from app.services.auth_service import create_password_reset_token

    return create_password_reset_token(user_id, email)


async def test_send_returns_ok_for_existing_user(client):
    await _register(client, "reset_ok@example.com")

    r = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "reset_ok@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_send_does_not_leak_unknown_email(client):
    # Несуществующий email — тот же 200 {ok:true} (анти-энумерация).
    r = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "nobody@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_send_rejects_invalid_email(client):
    r = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "not-an-email"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "EMAIL_INVALID"


async def test_send_rate_limit(client):
    await _register(client, "reset_rl@example.com")
    first = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "reset_rl@example.com"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "reset_rl@example.com"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "TOO_FREQUENT"


async def test_confirm_changes_password(client):
    from app.services.auth_service import verify_password
    from app.database import async_session
    from app.models.models import User
    from sqlalchemy import select

    user_id = await _register(client, "reset_change@example.com")

    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": _make_token("reset_change@example.com", user_id), "password": "brand_new_pw"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Старый пароль больше не работает, новый — работает.
    old = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset_change@example.com", "password": "secret123"},
    )
    assert old.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset_change@example.com", "password": "brand_new_pw"},
    )
    assert new_login.status_code == 200

    # Email помечен верифицированным (доступ к почте подтверждён).
    async with async_session() as db:
        res = await db.execute(select(User).where(User.id == user_id))
        assert res.scalar_one().email_verified is True


async def test_confirm_invalid_token(client):
    user_id = await _register(client, "reset_bad@example.com")

    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "garbage", "password": "new_password"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_RESET_TOKEN"


async def test_confirm_token_email_lock(client):
    user_id = await _register(client, "reset_lock@example.com")

    # Токен на другой email того же юзера не должен проходить.
    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": _make_token("other@example.com", user_id), "password": "new_password"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_RESET_TOKEN"


async def test_confirm_password_too_short(client):
    user_id = await _register(client, "reset_short@example.com")

    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": _make_token("reset_short@example.com", user_id), "password": "short"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "PASSWORD_TOO_SHORT"


async def test_confirm_expired_token(client):
    from app.services.auth_service import create_password_reset_token
    from datetime import datetime, timedelta
    import jwt
    from app.config import settings

    user_id = await _register(client, "reset_exp@example.com")

    expired = jwt.encode(
        {
            "sub": str(user_id),
            "purpose": "password_reset",
            "email": "reset_exp@example.com",
            "exp": datetime.utcnow() - timedelta(minutes=1),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": expired, "password": "new_password"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_RESET_TOKEN"