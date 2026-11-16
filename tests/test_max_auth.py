"""Вход через мини-приложение MAX: подпись initData, одноразовость сессии,
привязка MAX к профилю и защита служебных эндпоинтов бота.

Проверяем ровно те свойства, от которых зависит безопасность: подделанный
initData не принимается, сессия выдаёт JWT один раз, чужой MAX-аккаунт не
привязывается ко второму профилю.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import pytest

from tests.support import MAX_KEY

MAX_TOKEN = "test-max-bot-token"
MAX_HEADERS = {"X-Max-Bot-Key": MAX_KEY}


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


def build_init_data(
    max_id: int,
    *,
    token: str = MAX_TOKEN,
    auth_date: int | None = None,
    start_param: str | None = None,
    username: str | None = "max_user",
) -> str:
    """Собрать подписанный initData так, как это делает MAX."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "0f5c1d2e-0000-4000-8000-000000000001",
        "user": json.dumps(
            {
                "id": max_id,
                "first_name": "Max",
                "last_name": "User",
                "username": username,
                "language_code": "ru",
                "photo_url": None,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    if start_param is not None:
        fields["start_param"] = start_param

    launch_params = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(
        secret_key, launch_params.encode(), hashlib.sha256
    ).hexdigest()

    body = "&".join(f"{k}={quote(v, safe='')}" for k, v in fields.items())
    return f"{body}&hash={signature}"


async def create_session(client, *, consent=True, headers=None):
    r = await client.post(
        "/api/v1/auth/max/session",
        json={"consent": consent},
        headers=headers or {},
    )
    return r


async def test_session_requires_consent(client):
    r = await create_session(client, consent=False)
    assert r.status_code == 400
    assert r.json()["detail"] == "CONSENT_REQUIRED"


async def test_session_returns_deep_link(client):
    r = await create_session(client)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mode"] == "login"
    assert data["session_id"]
    assert data["deep_link"] == (
        f"https://max.ru/dharana_test_bot?startapp=login_{data['session_id']}"
    )
    assert data["expires_in"] > 0


async def test_confirm_and_poll_issues_token_once(client):
    session_id = (await create_session(client)).json()["session_id"]

    r = await client.post(
        "/api/v1/auth/max/confirm",
        json={
            "session_id": session_id,
            "init_data": build_init_data(900001, start_param=f"login_{session_id}"),
        },
    )
    assert r.status_code == 200, r.text

    poll = await client.get(f"/api/v1/auth/max/session/{session_id}")
    assert poll.status_code == 200
    data = poll.json()
    assert data["status"] == "confirmed"
    assert data["access_token"]
    assert data["user"]["max_id"] == 900001

    again = await client.get(f"/api/v1/auth/max/session/{session_id}")
    assert again.json()["status"] == "consumed"


async def test_pending_session_stays_pending(client):
    session_id = (await create_session(client)).json()["session_id"]
    r = await client.get(f"/api/v1/auth/max/session/{session_id}")
    assert r.json()["status"] == "pending"


async def test_bad_signature_rejected(client):
    session_id = (await create_session(client)).json()["session_id"]
    r = await client.post(
        "/api/v1/auth/max/confirm",
        json={
            "session_id": session_id,
            "init_data": build_init_data(900002, token="wrong-token"),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_INIT_DATA"


async def test_stale_init_data_rejected(client):
    session_id = (await create_session(client)).json()["session_id"]
    r = await client.post(
        "/api/v1/auth/max/confirm",
        json={
            "session_id": session_id,
            "init_data": build_init_data(
                900003, auth_date=int(time.time()) - 7200
            ),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_INIT_DATA"


async def test_start_param_must_match_session(client):
    session_id = (await create_session(client)).json()["session_id"]
    r = await client.post(
        "/api/v1/auth/max/confirm",
        json={
            "session_id": session_id,
            "init_data": build_init_data(900004, start_param="login_other"),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "SESSION_MISMATCH"


async def test_unknown_session_rejected(client):
    r = await client.post(
        "/api/v1/auth/max/confirm",
        json={
            "session_id": "a" * 32,
            "init_data": build_init_data(900005),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "MAX_SESSION_EXPIRED"


async def test_login_is_idempotent_for_same_max_id(client):
    for _ in range(2):
        session_id = (await create_session(client)).json()["session_id"]
        confirm = await client.post(
            "/api/v1/auth/max/confirm",
            json={"session_id": session_id, "init_data": build_init_data(900006)},
        )
        assert confirm.status_code == 200, confirm.text
        data = (await client.get(f"/api/v1/auth/max/session/{session_id}")).json()
        assert data["status"] == "confirmed"

    first = data["user"]["id"]

    session_id = (await create_session(client)).json()["session_id"]
    await client.post(
        "/api/v1/auth/max/confirm",
        json={"session_id": session_id, "init_data": build_init_data(900006)},
    )
    data = (await client.get(f"/api/v1/auth/max/session/{session_id}")).json()
    assert data["user"]["id"] == first


async def test_link_mode_attaches_max_to_logged_in_user(client):
    # Профиль по почте
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "max-link@example.com",
            "password": "very-secret-pass",
            "name": "Linker",
            "consent": True,
        },
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Сначала MAX-пользователь входит сам по себе
    s1 = (await create_session(client)).json()["session_id"]
    await client.post(
        "/api/v1/auth/max/confirm",
        json={"session_id": s1, "init_data": build_init_data(900007)},
    )
    other = (await client.get(f"/api/v1/auth/max/session/{s1}")).json()
    other_user_id = other["user"]["id"]

    # Теперь привязываем тот же MAX к профилю по почте: аккаунты сливаются
    s2 = (await create_session(client, headers=auth)).json()["session_id"]
    confirm = await client.post(
        "/api/v1/auth/max/confirm",
        json={"session_id": s2, "init_data": build_init_data(900007)},
    )
    assert confirm.status_code == 200, confirm.text

    linked = (await client.get(f"/api/v1/auth/max/session/{s2}")).json()
    assert linked["status"] == "confirmed"
    assert linked["user"]["max_id"] == 900007
    assert linked["user"]["id"] != other_user_id

    me = await client.get("/api/v1/auth/me", headers=auth)
    assert me.json()["max_id"] == 900007


async def test_bot_register_requires_key(client):
    r = await client.post(
        "/api/v1/auth/max/bot/register", json={"max_id": 900008, "name": "No Key"}
    )
    assert r.status_code == 403


async def test_bot_register_creates_user_and_language(client):
    r = await client.post(
        "/api/v1/auth/max/bot/register",
        json={"max_id": 900009, "name": "Bot User", "language": "en"},
        headers=MAX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["language"] == "en"

    lang = await client.get("/api/v1/auth/max/900009/language", headers=MAX_HEADERS)
    assert lang.json()["language"] == "en"

    upd = await client.put(
        "/api/v1/auth/max/900009/language",
        json={"language": "ru"},
        headers=MAX_HEADERS,
    )
    assert upd.json()["language"] == "ru"
