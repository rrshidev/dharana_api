"""Сводная статистика для ботов: GET /practice/timer/stats (X-Timer-Key).

Бот не имеет JWT — он знает только telegram_id пользователя. Эндпоинт отдаёт
те же агрегаты, что профильный /practice/stats, поэтому бот и приложение
показывают одинаковые числа.
"""

import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, PracticeSession
from app.services.auth_service import hash_password
from app.config import settings


def _rnd():
    return uuid.uuid4().hex[:8]


async def _mk_user(telegram_id):
    user = User(
        email=f"{_rnd()}@t.ru",
        name="StatsUser",
        username=_rnd(),
        hashed_password=hash_password("secret"),
        telegram_id=telegram_id,
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
        uid = user.id
    return uid


async def _mk_session(user_id, practice_type, duration_seconds, started_at, asanas=None):
    async with async_session() as db:
        db.add(PracticeSession(
            user_id=user_id,
            practice_type=practice_type,
            status="completed",
            asanas_practiced=asanas or [],
            total_duration_seconds=duration_seconds,
            started_at=started_at,
            completed_at=started_at,
        ))
        await db.commit()


@pytest.fixture
async def client():
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_timer_stats_requires_key(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    r = await client.get("/api/v1/practice/timer/stats?telegram_id=777010")
    assert r.status_code == 403

    r = await client.get(
        "/api/v1/practice/timer/stats?telegram_id=777010",
        headers={"X-Timer-Key": "wrong-key"},
    )
    assert r.status_code == 403


async def test_timer_stats_unknown_user(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    r = await client.get(
        "/api/v1/practice/timer/stats?telegram_id=777011",
        headers={"X-Timer-Key": "timer-test-key"},
    )
    assert r.status_code == 404


async def test_timer_stats_empty_for_registered_user(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    await _mk_user(telegram_id=777012)
    r = await client.get(
        "/api/v1/practice/timer/stats?telegram_id=777012",
        headers={"X-Timer-Key": "timer-test-key"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_sessions"] == 0
    assert data["total_minutes"] == 0
    assert data["total_days"] == 0
    assert data["current_streak"] == 0
    assert data["favorite_asanas"] == []
    assert data["last_practice_at"] is None


async def test_timer_stats_matches_profile_endpoint(client, monkeypatch):
    """Цифры бота и профиля не должны расходиться."""
    from sqlalchemy import select
    from app.services.auth_service import create_access_token

    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    uid = await _mk_user(telegram_id=777013)
    token = create_access_token(uid)

    today = datetime.utcnow()
    yesterday = today - timedelta(days=1)
    await _mk_session(uid, "asana", 600, today, asanas=["Бакасана", "Триконасана"])
    await _mk_session(uid, "asana", 300, today, asanas=["Бакасана"])
    await _mk_session(uid, "meditation", 300, yesterday)

    r_bot = await client.get(
        "/api/v1/practice/timer/stats?telegram_id=777013",
        headers={"X-Timer-Key": "timer-test-key"},
    )
    r_app = await client.get(
        "/api/v1/practice/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_bot.status_code == 200, r_bot.text
    assert r_app.status_code == 200

    bot, app_stats = r_bot.json(), r_app.json()
    for key in (
        "total_minutes",
        "total_days",
        "total_sessions",
        "total_asanas_practiced",
        "current_streak",
        "favorite_asanas",
        "by_type",
    ):
        assert bot[key] == app_stats[key], key

    assert bot["total_sessions"] == 3
    assert bot["total_days"] == 2
    assert bot["total_minutes"] == 20
    assert bot["current_streak"] == 2
    assert bot["total_asanas_practiced"] == 3
    assert bot["favorite_asanas"][0] == {"name": "Бакасана", "count": 2}
    assert bot["by_type"]["asana"]["sessions"] == 2
    assert bot["by_type"]["meditation"]["sessions"] == 1


async def test_timer_stats_includes_last_practice_at(client, monkeypatch):
    from sqlalchemy import select

    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    uid = await _mk_user(telegram_id=777014)
    stamp = datetime.utcnow() - timedelta(hours=3)
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        user.last_practice_at = stamp
        await db.commit()

    r = await client.get(
        "/api/v1/practice/timer/stats?telegram_id=777014",
        headers={"X-Timer-Key": "timer-test-key"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["last_practice_at"] == stamp.isoformat()