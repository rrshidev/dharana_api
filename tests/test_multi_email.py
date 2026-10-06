"""Несколько почт-«дверей» у пользователя (app_user_emails).

Вход и сброс пароля работают по основной и любой ВЕРИФИЦИРОВАННОЙ
дополнительной почте; неверифицированная дверью не считается. Почта должна
быть уникальна глобально (включая вторичные) — нельзя занять чужой адрес.
"""

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


async def _register(client, email, password="secret123"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "R", "website": ""},
    )
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


async def _add_secondary(user_id: int, email: str, verified: bool = True) -> None:
    from app.database import async_session
    from app.models.models import UserEmail

    async with async_session() as s:
        s.add(UserEmail(user_id=user_id, email=email, email_verified=verified))
        await s.commit()


async def test_login_via_verified_secondary_alias(client):
    uid = await _register(client, "primary@example.com")
    await _add_secondary(uid, "alias@example.com", verified=True)

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "alias@example.com", "password": "secret123"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == uid

    # Вход по основной по-прежнему работает.
    r2 = await client.post(
        "/api/v1/auth/login",
        json={"email": "primary@example.com", "password": "secret123"},
    )
    assert r2.status_code == 200


async def test_unverified_secondary_is_not_a_login_door(client):
    uid = await _register(client, "primary2@example.com")
    await _add_secondary(uid, "ghost@example.com", verified=False)

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "secret123"},
    )
    assert r.status_code == 401


async def test_register_rejects_occupied_secondary_email(client):
    uid = await _register(client, "primary3@example.com")
    await _add_secondary(uid, "taken@example.com")

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "taken@example.com", "password": "secret123", "name": "X", "website": ""},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Email already registered"


async def test_reset_via_secondary_alias_sets_password(client):
    from sqlalchemy import select

    from app.database import async_session
    from app.models.models import UserEmail
    from app.services.auth_service import create_password_reset_token

    uid = await _register(client, "resetalias@example.com")
    await _add_secondary(uid, "resetalias2@example.com", verified=True)

    r = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "resetalias2@example.com"},
    )
    assert r.status_code == 200

    token = create_password_reset_token(uid, "resetalias2@example.com")
    confirm = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "password": "brand_new_pw"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["ok"] is True

    # Уже можно войти по дополнительной почте с новым паролем.
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "resetalias2@example.com", "password": "brand_new_pw"},
    )
    assert login.status_code == 200

    async with async_session() as s:
        alias = (await s.execute(select(UserEmail).where(
            UserEmail.user_id == uid, UserEmail.email == "resetalias2@example.com"
        ))).scalar_one()
    assert alias.email_verified is True


async def test_reset_silently_skips_unverified_secondary(client):
    """Письмо сброса не уходит на неверифицированную почту (она не дверь):
    ответ {ok:true}, но rate-limit не выставляется (запрос не дошёл до отправки)."""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.models import User

    uid = await _register(client, "resetalias3@example.com")
    await _add_secondary(uid, "resetalias3g@example.com", verified=False)

    r = await client.post(
        "/api/v1/auth/password-reset/send",
        json={"email": "resetalias3g@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    async with async_session() as s:
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
    assert user.password_reset_sent_at is None  # до rate-limit не дошли