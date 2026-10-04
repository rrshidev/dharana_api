"""Вход по Яндекс ID (POST /auth/yandex)."""

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
def ya():
    from unittest.mock import patch

    profile = {
        "id": "ya123456789",
        "email": "yandex.user@yandex.ru",
        "name": "Яндекс User",
        "avatar_url": None,
    }
    with patch("app.routers.auth.settings.YANDEX_CLIENT_ID", "ya-app-1"), \
         patch("app.routers.auth.settings.YANDEX_CLIENT_SECRET", "ya-secret"), \
         patch("app.routers.auth.yandex_login_with_code", return_value=profile) as mock_login:
        yield mock_login


async def test_yandex_not_configured(client):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.YANDEX_CLIENT_ID", ""), \
         patch("app.routers.auth.settings.YANDEX_CLIENT_SECRET", ""):
        r = await client.post("/api/v1/auth/yandex", json={"code": "any"})
        assert r.status_code == 501
        assert r.json()["detail"] == "YANDEX_NOT_CONFIGURED"


async def test_yandex_invalid_code(client, ya):
    ya.return_value = None
    r = await client.post("/api/v1/auth/yandex", json={"code": "garbage"})
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_YANDEX_TOKEN"


async def test_yandex_redirect_uri_built_from_config(client, ya):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.OAUTH_REDIRECT_BASE_URL", "https://dharana.ru"):
        r = await client.post("/api/v1/auth/yandex", json={"code": "valid"})
    assert r.status_code == 200
    assert ya.call_args.kwargs["redirect_uri"] == "https://dharana.ru/api/auth/yandex/callback"


async def test_yandex_client_redirect_uri_allowed(client, ya):
    """Приложение Android присылает свой колбэк (App Link) — он в allowlist."""
    from unittest.mock import patch

    app_link = "https://dharana.ru/app/auth/yandex/callback"
    with patch("app.routers.auth.settings.YANDEX_ALLOWED_REDIRECT_URIS", app_link):
        r = await client.post(
            "/api/v1/auth/yandex",
            json={"code": "valid", "redirect_uri": app_link},
        )
    assert r.status_code == 200
    assert ya.call_args.kwargs["redirect_uri"] == app_link


async def test_yandex_client_redirect_uri_rejected(client, ya):
    """Чужой redirect_uri (например, перехват кода) — 400, обмена не было."""
    from unittest.mock import patch

    with patch("app.routers.auth.settings.YANDEX_ALLOWED_REDIRECT_URIS", ""):
        r = await client.post(
            "/api/v1/auth/yandex",
            json={"code": "valid", "redirect_uri": "https://evil.example/steal"},
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "OAUTH_REDIRECT_NOT_ALLOWED"
    ya.assert_not_called()


async def test_yandex_new_user(client, ya):
    r = await client.post("/api/v1/auth/yandex", json={"code": "valid"})
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"]
    assert data["user"]["email"] == "yandex.user@yandex.ru"
    assert data["user"]["name"] == "Яндекс User"
    assert data["user"]["yandex_id"] == "ya123456789"

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.json()["email_verified"] is True


async def test_yandex_same_user_second_login(client, ya):
    r1 = await client.post("/api/v1/auth/yandex", json={"code": "valid"})
    uid1 = r1.json()["user"]["id"]

    r2 = await client.post("/api/v1/auth/yandex", json={"code": "valid"})
    assert r2.json()["user"]["id"] == uid1
    assert r2.json()["user"]["yandex_id"] == "ya123456789"


async def test_yandex_links_existing_email_user(client, ya):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "yandex.user@yandex.ru", "password": "secret123", "name": "Old Name", "website": ""},
    )
    assert r.status_code == 201
    uid = r.json()["user"]["id"]

    ry = await client.post("/api/v1/auth/yandex", json={"code": "valid"})
    assert ry.status_code == 200
    assert ry.json()["user"]["id"] == uid
    assert ry.json()["user"]["yandex_id"] == "ya123456789"


async def test_yandex_and_vk_are_separate_accounts(client, ya):
    """Один человек, вошедший и через VK, и через Яндекс — два разных юзера:
    почта у обоих провайдеров своя, склеиваем только по id провайдера."""
    from unittest.mock import patch

    vk_profile = {
        "id": "vk123456789",
        "email": "vk.user@mail.ru",
        "name": "VK User",
        "avatar_url": None,
    }
    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.settings.VK_CLIENT_SECRET", "vk-secret"), \
         patch("app.routers.auth.vk_login_with_code", return_value=vk_profile):
        rv = await client.post("/api/v1/auth/vk", json={"code": "valid"})
        ry = await client.post("/api/v1/auth/yandex", json={"code": "valid"})

    assert rv.status_code == 200
    assert ry.status_code == 200
    assert rv.json()["user"]["id"] != ry.json()["user"]["id"]