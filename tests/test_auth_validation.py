"""Валидация регистрации: пароль, формат email, одноразовые домены, honeypot."""

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


async def _register(client, **overrides):
    payload = {
        "email": "new_user@example.com",
        "password": "secret123",
        "name": "Tester",
        "website": "",
    }
    payload.update(overrides)
    return await client.post("/api/v1/auth/register", json=payload)


async def test_register_ok(client):
    r = await _register(client)
    assert r.status_code == 201
    assert "access_token" in r.json()


async def test_password_too_short(client):
    r = await _register(client, password="qwerty7")
    assert r.status_code == 400
    assert r.json()["detail"] == "PASSWORD_TOO_SHORT"


async def test_password_too_long(client):
    r = await _register(client, password="x" * 129)
    assert r.status_code == 400
    assert r.json()["detail"] == "PASSWORD_TOO_LONG"


async def test_name_too_long(client):
    r = await _register(client, name="N" * 61)
    assert r.status_code == 400
    assert r.json()["detail"] == "NAME_TOO_LONG"


async def test_name_trimmed_on_register(client):
    payload = {
        "email": "trim_name@example.com",
        "password": "secret123",
        "name": "  Иван Петров  ",
        "website": "",
    }
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 201
    assert r.json()["user"]["name"] == "Иван Петров"


async def test_invalid_email_syntax(client):
    r = await _register(client, email="not-an-email")
    assert r.status_code == 400
    assert r.json()["detail"] == "EMAIL_INVALID"


async def test_disposable_domain_rejected(client):
    r = await _register(client, email="bot@mailinator.com")
    assert r.status_code == 400
    assert r.json()["detail"] == "EMAIL_DISPOSABLE"


async def test_disposable_domain_also_covers_yopmail(client):
    r = await _register(client, email="bot@yopmail.com")
    assert r.status_code == 400
    assert r.json()["detail"] == "EMAIL_DISPOSABLE"


async def test_honeypot_rejected(client):
    r = await _register(client, website="http://spam.example")
    assert r.status_code == 400
    assert r.json()["detail"] == "SPAM"


async def test_email_casefolded_on_register(client):
    r = await _register(client, email="MiXeD@Example.COM")
    assert r.status_code == 201
    data = r.json()
    assert data["user"]["email"] == "mixed@example.com"