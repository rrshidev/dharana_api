"""GET /asanas/day и настройки рассылки в GET/PATCH /profile.

- /day: детерминированная асана для гостей, асана из свежего лога рассылки
  для бота-пользователей, толерантный JWT (протухший токен ≠ 401).
- /profile: daily_asana_enabled / daily_asana_time / timezone / language
  с валидацией формата бота ("HH:MM", "UTC+3").
"""

from datetime import datetime, timedelta

import os

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
def catalog():
    from app.config import settings
    from app.services.asana_service import asana_service

    names = ["Тестасана дня 1", "Тестасана дня 2", "Тестасана дня 3"]
    base = os.path.join(settings.BOT_DATA_DIR, "catalog", "stay+")
    os.makedirs(base, exist_ok=True)
    for name in names:
        with open(os.path.join(base, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("RU описание")
        with open(os.path.join(base, name + ".en.txt"), "w", encoding="utf-8") as f:
            f.write("TEST DAILY ASANA\n\nEN description")
    asana_service.refresh_catalog_cache()
    yield names
    asana_service.refresh_catalog_cache()
    for name in names:
        for fn in (name + ".txt", name + ".en.txt"):
            p = os.path.join(base, fn)
            if os.path.exists(p):
                os.remove(p)


async def _tg_login(client, telegram_id=910000):
    r = await client.post(
        "/api/v1/auth/telegram",
        json={"telegram_id": telegram_id, "name": "DayUser", "username": f"dayuser{telegram_id}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _add_log(telegram_id, asana_name, sent_at):
    from app.database import async_session
    from app.models.models import DailyAsanaLog

    async with async_session() as s:
        s.add(DailyAsanaLog(telegram_id=telegram_id, asana_name=asana_name, sent_at=sent_at))
        await s.commit()


async def test_day_anonymous_deterministic(client, catalog):
    from app.services.asana_service import asana_service

    r1 = await client.get("/api/v1/asanas/day")
    r2 = await client.get("/api/v1/asanas/day")
    assert r1.status_code == 200
    assert r2.status_code == 200
    expected = asana_service.get_daily_asana_name()
    assert r1.json()["name"] == expected
    assert r2.json()["name"] == expected  # стабильно внутри суток
    assert r1.json()["name"] in catalog


async def test_day_lang_en(client, catalog):
    r = await client.get("/api/v1/asanas/day", params={"lang": "en"})
    assert r.status_code == 200
    assert r.json()["description"] == "TEST DAILY ASANA\n\nEN description"
    r_ru = await client.get("/api/v1/asanas/day", params={"lang": "ru"})
    assert r_ru.json()["name"] == r.json()["name"]  # lang не влияет на выбор


async def test_day_stale_token_is_anonymous_not_401(client, catalog):
    from app.services.asana_service import asana_service

    r = await client.get(
        "/api/v1/asanas/day", headers=_auth("not-a-valid-jwt-token")
    )
    assert r.status_code == 200
    assert r.json()["name"] == asana_service.get_daily_asana_name()


async def test_day_uses_fresh_mail_log(client, catalog):
    token = await _tg_login(client, telegram_id=910001)
    await _add_log(910001, catalog[1], datetime.utcnow())
    r = await client.get("/api/v1/asanas/day", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["name"] == catalog[1]


async def test_day_stale_log_falls_back(client, catalog):
    token = await _tg_login(client, telegram_id=910002)
    await _add_log(910002, catalog[1], datetime.utcnow() - timedelta(days=2))
    r = await client.get("/api/v1/asanas/day", headers=_auth(token))
    assert r.status_code == 200
    from app.services.asana_service import asana_service

    assert r.json()["name"] == asana_service.get_daily_asana_name()


async def test_day_log_name_missing_in_catalog_falls_back(client, catalog):
    token = await _tg_login(client, telegram_id=910003)
    await _add_log(910003, "Которой больше нет", datetime.utcnow())
    r = await client.get("/api/v1/asanas/day", headers=_auth(token))
    assert r.status_code == 200
    from app.services.asana_service import asana_service

    assert r.json()["name"] == asana_service.get_daily_asana_name()


async def test_day_no_log_deterministic_for_tg_user(client, catalog):
    from app.services.asana_service import asana_service

    token = await _tg_login(client, telegram_id=910004)
    r = await client.get("/api/v1/asanas/day", headers=_auth(token))
    assert r.json()["name"] == asana_service.get_daily_asana_name()


async def test_day_timezone_does_not_break_freshness(client, catalog):
    # Пояс с дробным смещением (формат бота "UTC+5:30"): лог свежий → берём его.
    token = await _tg_login(client, telegram_id=910005)
    r = await client.patch(
        "/api/v1/profile",
        json={"timezone": "UTC+5:30"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    await _add_log(910005, catalog[2], datetime.utcnow())
    r = await client.get("/api/v1/asanas/day", headers=_auth(token))
    assert r.json()["name"] == catalog[2]


async def test_profile_get_returns_mailing_settings(client):
    token = await _tg_login(client, telegram_id=910006)
    r = await client.get("/api/v1/profile", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert data["daily_asana_enabled"] is True
    assert data["daily_asana_time"] is None or data["daily_asana_time"].count(":") == 1
    assert data["timezone"] == "UTC"
    assert data["language"] == "ru"


async def test_profile_patch_mailing_settings(client):
    token = await _tg_login(client, telegram_id=910007)
    r = await client.patch(
        "/api/v1/profile",
        json={
            "daily_asana_enabled": False,
            "daily_asana_time": "07:30",
            "timezone": "utc+3",
            "language": "en",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    data = (await client.get("/api/v1/profile", headers=_auth(token))).json()
    assert data["daily_asana_enabled"] is False
    assert data["daily_asana_time"] == "07:30"
    assert data["timezone"] == "UTC+3"
    assert data["language"] == "en"

    # Формат совместим с парсером бота
    from app.services.asana_service import parse_timezone_offset

    assert parse_timezone_offset(data["timezone"]) == timedelta(hours=3)


async def test_profile_patch_invalid_values_400(client):
    token = await _tg_login(client, telegram_id=910008)
    for payload in (
        {"daily_asana_time": "25:00"},
        {"daily_asana_time": "9am"},
        {"timezone": "Europe/Moscow"},
        {"language": "fr"},
    ):
        r = await client.patch("/api/v1/profile", json=payload, headers=_auth(token))
        assert r.status_code == 400, payload

    # Валидные варианты формата бота
    for tz in ("UTC", "UTC+3", "UTC-7", "UTC+5:30", "gmt+2"):
        r = await client.patch("/api/v1/profile", json={"timezone": tz}, headers=_auth(token))
        assert r.status_code == 200, tz
    for t in ("0:00", "9:00", "23:59"):
        r = await client.patch("/api/v1/profile", json={"daily_asana_time": t}, headers=_auth(token))
        assert r.status_code == 200, t


async def test_parse_timezone_offset():
    from app.services.asana_service import parse_timezone_offset

    assert parse_timezone_offset(None) == timedelta(0)
    assert parse_timezone_offset("UTC") == timedelta(0)
    assert parse_timezone_offset("UTC+3") == timedelta(hours=3)
    assert parse_timezone_offset("utc-5") == timedelta(hours=-5)
    assert parse_timezone_offset("UTC+5:30") == timedelta(hours=5, minutes=30)
    assert parse_timezone_offset("GMT+2") == timedelta(hours=2)
    assert parse_timezone_offset("мусор") == timedelta(0)
