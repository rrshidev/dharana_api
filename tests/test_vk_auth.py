"""Вход по VK ID (POST /auth/vk) — тем же флою входит MAX."""

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
def vk():
    """Включаем VK_CLIENT_ID/SECRET и мокаем обмен кода на токен + профиль."""
    from unittest.mock import patch

    profile = {
        "id": "vk123456789",
        "email": "vk.user@mail.ru",
        "name": "VK User",
        "avatar_url": "https://sun9-1.userapi.com/vk.jpg",
    }
    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.settings.VK_CLIENT_SECRET", "vk-secret"), \
         patch("app.routers.auth.vk_login_with_code", return_value=profile) as mock_login:
        yield mock_login


async def test_vk_not_configured(client):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.VK_CLIENT_ID", ""), \
         patch("app.routers.auth.settings.VK_CLIENT_SECRET", ""):
        r = await client.post("/api/v1/auth/vk", json={"code": "any"})
        assert r.status_code == 501
        assert r.json()["detail"] == "VK_NOT_CONFIGURED"


async def test_vk_secret_only_not_configured(client):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.settings.VK_CLIENT_SECRET", ""):
        r = await client.post("/api/v1/auth/vk", json={"code": "any"})
        assert r.status_code == 501
        assert r.json()["detail"] == "VK_NOT_CONFIGURED"


async def test_vk_invalid_code(client, vk):
    vk.return_value = None
    r = await client.post("/api/v1/auth/vk", json={"code": "garbage"})
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_VK_TOKEN"


async def test_vk_redirect_uri_built_from_config(client, vk):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.OAUTH_REDIRECT_BASE_URL", "https://dharana.ru/"):
        r = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert r.status_code == 200
    kwargs = vk.call_args.kwargs
    # redirect_uri собирает сервер, из клиента не берём (точное совпадение в консоли VK).
    assert kwargs["redirect_uri"] == "https://dharana.ru/api/auth/vk/callback"
    assert kwargs["code"] == "valid"


async def test_vk_new_user(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert r.status_code == 200
    data = r.json()
    assert data["access_token"]
    assert data["user"]["email"] == "vk.user@mail.ru"
    assert data["user"]["name"] == "VK User"
    assert data["user"]["vk_id"] == "vk123456789"

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email_verified"] is True


async def test_vk_same_user_second_login(client, vk):
    r1 = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    uid1 = r1.json()["user"]["id"]

    r2 = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == uid1
    assert r2.json()["user"]["vk_id"] == "vk123456789"


async def test_vk_max_uses_same_identity(client, vk):
    """MAX — тот же VK ID: вход с MAX-подписью не плодит второго юзера."""
    r = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    uid = r.json()["user"]["id"]

    r_max = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert r_max.json()["user"]["id"] == uid


async def test_vk_links_existing_email_user(client, vk):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "vk.user@mail.ru", "password": "secret123", "name": "Old Name", "website": ""},
    )
    assert r.status_code == 201
    uid = r.json()["user"]["id"]

    rv = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert rv.status_code == 200
    assert rv.json()["user"]["id"] == uid
    assert rv.json()["user"]["vk_id"] == "vk123456789"


async def test_vk_without_email_uses_fallback_name(client):
    from unittest.mock import patch

    profile = {"id": "vk999", "email": None, "name": None, "avatar_url": None}
    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.settings.VK_CLIENT_SECRET", "vk-secret"), \
         patch("app.routers.auth.vk_login_with_code", return_value=profile):
        r = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["email"] is None
    assert data["user"]["name"] == "user_vk999"
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.json()["email_verified"] is False


async def test_vk_does_not_touch_google_id(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"code": "valid"})
    uid = r.json()["user"]["id"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    )
    assert me.json()["id"] == uid
    assert me.json().get("google_id") in (None, "")