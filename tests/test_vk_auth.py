"""Вход по VK ID (POST /auth/vk) — access_token из официального виджета OneTap.

Схема VK ID (2026-10-04): код на токен меняет браузер (`VKID.Auth.exchangeCode`),
бэкенд проверяет access_token через /oauth2/user_info и выдаёт свой JWT.
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


@pytest.fixture
def vk():
    """Включаем VK_CLIENT_ID и мокаем проверку access_token -> профиль."""
    from unittest.mock import patch

    profile = {
        "id": "vk123456789",
        "email": "vk.user@mail.ru",
        "name": "VK User",
        "avatar_url": "https://sun9-1.userapi.com/vk.jpg",
    }
    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.vk_login_with_access_token", return_value=profile) as mock_login:
        yield mock_login


async def test_vk_not_configured(client):
    from unittest.mock import patch

    with patch("app.routers.auth.settings.VK_CLIENT_ID", ""):
        r = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    assert r.status_code == 501
    assert r.json()["detail"] == "VK_NOT_CONFIGURED"


async def test_vk_requires_client_secret(client, vk):
    """Client secret больше не нужен: вход идёт по access_token."""
    from unittest.mock import patch

    with patch("app.routers.auth.settings.VK_CLIENT_SECRET", ""):
        r = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    assert r.status_code == 200


async def test_vk_empty_token_rejected(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"access_token": ""})
    assert r.status_code == 422
    vk.assert_not_called()


async def test_vk_invalid_token(client, vk):
    vk.return_value = None
    r = await client.post("/api/v1/auth/vk", json={"access_token": "garbage"})
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_VK_TOKEN"


async def test_vk_passes_token_and_client_id(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"access_token": "vk-token-1"})
    assert r.status_code == 200
    kwargs = vk.call_args.kwargs
    assert kwargs["access_token"] == "vk-token-1"
    assert kwargs["client_id"] == "vk-app-1"


async def test_vk_new_user(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
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
    r1 = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    uid1 = r1.json()["user"]["id"]

    r2 = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == uid1
    assert r2.json()["user"]["vk_id"] == "vk123456789"


async def test_vk_links_existing_email_user(client, vk):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "vk.user@mail.ru", "password": "secret123", "name": "Old Name", "website": ""},
    )
    assert r.status_code == 201
    uid = r.json()["user"]["id"]

    rv = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    assert rv.status_code == 200
    assert rv.json()["user"]["id"] == uid
    assert rv.json()["user"]["vk_id"] == "vk123456789"


async def test_vk_without_email_uses_fallback_name(client):
    from unittest.mock import patch

    profile = {"id": "vk999", "email": None, "name": None, "avatar_url": None}
    with patch("app.routers.auth.settings.VK_CLIENT_ID", "vk-app-1"), \
         patch("app.routers.auth.vk_login_with_access_token", return_value=profile):
        r = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["email"] is None
    assert data["user"]["name"] == "user_vk999"
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.json()["email_verified"] is False


async def test_vk_does_not_touch_google_id(client, vk):
    r = await client.post("/api/v1/auth/vk", json={"access_token": "tok"})
    uid = r.json()["user"]["id"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    )
    assert me.json()["id"] == uid
    assert me.json().get("google_id") in (None, "")


def test_user_info_request_shape():
    """Сервис VK: POST id.vk.ru/oauth2/user_info?client_id=... телом access_token."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "sub": "12345",
        "name": "Иван",
        "given_name": "Иван",
        "family_name": "Петров",
        "email": "Ivan@Mail.RU",
        "picture": "https://vk.com/photo.jpg",
    }

    with patch.object(vk_service.httpx, "post", return_value=response) as post:
        profile = vk_service.fetch_profile("tok", "54803294")

    assert post.call_args.args[0] == "https://id.vk.ru/oauth2/user_info"
    assert post.call_args.kwargs["params"] == {"client_id": "54803294"}
    assert post.call_args.kwargs["data"] == {"access_token": "tok"}
    assert profile == {
        "id": "12345",
        "email": "ivan@mail.ru",
        "name": "Иван",
        "avatar_url": "https://vk.com/photo.jpg",
    }


def test_user_info_falls_back_to_name_parts():
    """Без `name` имя собирается из given_name/family_name."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "sub": "777",
        "given_name": "Анна",
        "family_name": "Сидорова",
    }

    with patch.object(vk_service.httpx, "post", return_value=response):
        profile = vk_service.fetch_profile("tok", "id")

    assert profile["name"] == "Анна Сидорова"
    assert profile["email"] is None


def test_user_info_without_subject_is_none():
    """Нет `sub`/`id` — профиля нет (иначе юзер был бы без vk_id)."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {"name": "Без ID"}

    with patch.object(vk_service.httpx, "post", return_value=response):
        assert vk_service.fetch_profile("tok", "id") is None


def test_user_info_error_response_is_none():
    """Ответ с `error` (например, invalid_token) — не профиль."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {"error": "invalid_token"}

    with patch.object(vk_service.httpx, "post", return_value=response):
        assert vk_service.fetch_profile("tok", "id") is None


def test_user_info_http_error_is_none():
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=401, text="unauthorized")

    with patch.object(vk_service.httpx, "post", return_value=response):
        assert vk_service.fetch_profile("tok", "id") is None

def test_user_info_nested_user_shape():
    """Реальный ответ VK ID: профиль вложен в `user` (прод-логи 2026-10-04)."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "user": {
            "user_id": "1095679706",
            "first_name": "Руслан",
            "last_name": "Стёпин",
            "avatar": "https://sun9-33.userapi.com/avatar.jpg",
        }
    }

    with patch.object(vk_service.httpx, "post", return_value=response):
        profile = vk_service.fetch_profile("tok", "54803294")

    assert profile == {
        "id": "1095679706",
        "email": None,
        "name": "Руслан Стёпин",
        "avatar_url": "https://sun9-33.userapi.com/avatar.jpg",
    }


def test_user_info_nested_user_with_email():
    """Вложенный `user` + запрошенный email."""
    from unittest.mock import MagicMock, patch

    from app.services import vk as vk_service

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "user": {
            "user_id": "42",
            "first_name": "Иван",
            "last_name": "Петров",
            "avatar": "https://vk.com/a.jpg",
            "email": "Ivan@Mail.RU",
        }
    }

    with patch.object(vk_service.httpx, "post", return_value=response):
        profile = vk_service.fetch_profile("tok", "id")

    assert profile["id"] == "42"
    assert profile["email"] == "ivan@mail.ru"
    assert profile["name"] == "Иван Петров"
