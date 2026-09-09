"""Google Sign-In (POST /auth/google) — флоу и валидация."""

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


@pytest.fixture
def gh():
    """Активируем GOOGLE_CLIENT_ID на время теста и мокаем валидацию токена."""
    from unittest.mock import patch

    with patch("app.routers.auth.settings.GOOGLE_CLIENT_ID", "web-client.apps.googleusercontent.com"), \
         patch("app.routers.auth.settings.GOOGLE_ANDROID_CLIENT_ID", "android-client.apps.googleusercontent.com"), \
         patch("app.routers.auth.decode_google_id_token") as mock_decode:
        mock_decode.return_value = {
            "sub": "g123456789",
            "email": "google.user@gmail.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://lh3.googleusercontent.com/a/avatar",
            "aud": "web-client.apps.googleusercontent.com",
        }
        yield mock_decode


async def test_google_not_configured(client):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.GOOGLE_CLIENT_ID", ""):
        r = await client.post("/api/v1/auth/google", json={"id_token": "any"})
        assert r.status_code == 501
        assert r.json()["detail"] == "GOOGLE_NOT_CONFIGURED"


async def test_google_invalid_token(client, gh):
    gh.return_value = None
    r = await client.post("/api/v1/auth/google", json={"id_token": "garbage"})
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_GOOGLE_TOKEN"


async def test_google_new_user(client, gh):
    r = await client.post("/api/v1/auth/google", json={"id_token": "valid"})
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"]
    assert data["user"]["email"] == "google.user@gmail.com"
    assert data["user"]["name"] == "Google User"
    assert data["user"]["google_id"] == "g123456789"
    # Google уже верифицировал почту — наш юзер получает email_verified сразу.
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email_verified"] is True


async def test_google_same_user_second_login(client, gh):
    r1 = await client.post("/api/v1/auth/google", json={"id_token": "valid"})
    assert r1.status_code == 200
    uid1 = r1.json()["user"]["id"]

    r2 = await client.post("/api/v1/auth/google", json={"id_token": "valid"})
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == uid1
    assert r2.json()["user"]["google_id"] == "g123456789"


async def test_google_links_existing_email_user(client, gh):
    # Пользователь сначала зарегистрировался по email...
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "google.user@gmail.com", "password": "secret123", "name": "Old Name", "website": ""},
    )
    assert r.status_code == 201
    uid = r.json()["user"]["id"]

    # ...потом входит через Google — google_id привязывается к тому же юзеру.
    rg = await client.post("/api/v1/auth/google", json={"id_token": "valid"})
    assert rg.status_code == 200
    assert rg.json()["user"]["id"] == uid
    assert rg.json()["user"]["google_id"] == "g123456789"