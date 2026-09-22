"""Язык таймер-бота (@timerasana_bot): POST /auth/telegram сохраняет timer_language,
GET/PUT /auth/telegram/{id}/language читают и меняют его."""

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


async def _telegram_login(client, telegram_id=100500, language=None):
    payload = {"telegram_id": telegram_id, "name": "TimerUser", "username": "timeruser"}
    if language is not None:
        payload["language"] = language
    return await client.post("/api/v1/auth/telegram", json=payload)


async def test_telegram_login_sets_timer_language_default_ru(client):
    r = await _telegram_login(client, telegram_id=100501)
    assert r.status_code == 200
    assert r.json()["user"]["timer_language"] == "ru"


async def test_telegram_login_respects_tg_language(client):
    r = await _telegram_login(client, telegram_id=100502, language="en")
    assert r.status_code == 200
    assert r.json()["user"]["timer_language"] == "en"


async def test_telegram_login_does_not_overwrite_timer_language(client):
    await _telegram_login(client, telegram_id=100503, language="ru")
    r = await _telegram_login(client, telegram_id=100503, language="en")
    # Второй контакт не перезаписывает ручной/ранее сохранённый выбор
    assert r.json()["user"]["timer_language"] == "ru"


async def test_get_timer_language(client):
    await _telegram_login(client, telegram_id=100504, language="en")
    r = await client.get("/api/v1/auth/telegram/100504/language")
    assert r.status_code == 200
    assert r.json() == {"language": "en"}


async def test_get_timer_language_missing_user(client):
    r = await client.get("/api/v1/auth/telegram/999999/language")
    assert r.status_code == 404


async def test_set_timer_language(client):
    await _telegram_login(client, telegram_id=100505, language="ru")
    r = await client.put("/api/v1/auth/telegram/100505/language", json={"language": "en"})
    assert r.status_code == 200
    assert r.json() == {"language": "en"}

    r = await client.get("/api/v1/auth/telegram/100505/language")
    assert r.json() == {"language": "en"}


async def test_set_timer_language_normalizes(client):
    await _telegram_login(client, telegram_id=100506, language="ru")
    r = await client.put("/api/v1/auth/telegram/100506/language", json={"language": "EN-US"})
    assert r.status_code == 200
    assert r.json() == {"language": "en"}

    r = await client.put("/api/v1/auth/telegram/100506/language", json={"language": "fr"})
    assert r.json() == {"language": "ru"}


async def test_set_timer_language_missing_user(client):
    r = await client.put("/api/v1/auth/telegram/999999/language", json={"language": "en"})
    assert r.status_code == 404