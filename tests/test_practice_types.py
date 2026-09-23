"""Мультипрактичность: practice_type (asana|meditation|pranayama).

- Запись завершённой практики из таймер-бота: POST /practice/timer (X-Timer-Key).
- practice_type в /practice/history.
- /practice/stats: общий streak по всем типам, breakdown by_type.
- /practice/stats/series: дневные ряды с фильтром по типу и tz_offset.
"""

import os
import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, PracticeSession
from app.services.auth_service import hash_password, create_access_token
from app.config import settings


def _rnd():
    return uuid.uuid4().hex[:8]


async def _mk_user(telegram_id=None):
    user = User(
        email=f"{_rnd()}@t.ru",
        name="PracticeUser",
        username=_rnd(),
        hashed_password=hash_password("secret"),
        telegram_id=telegram_id,
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
        uid = user.id
    token = create_access_token(uid)
    return uid, token


async def _mk_session(user_id, practice_type, duration_seconds, started_at, asanas=None):
    session = PracticeSession(
        user_id=user_id,
        practice_type=practice_type,
        status="completed",
        asanas_practiced=asanas or [],
        total_duration_seconds=duration_seconds,
        started_at=started_at,
        completed_at=started_at,
    )
    async with async_session() as db:
        db.add(session)
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


# ---------- Запись из таймер-бота ----------

async def test_timer_record_requires_key(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    r = await client.post(
        "/api/v1/practice/timer",
        headers={"X-Timer-Key": "wrong-key"},
        json={"telegram_id": 1, "practice_type": "meditation", "total_duration_seconds": 300},
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/practice/timer",
        json={"telegram_id": 1, "practice_type": "meditation", "total_duration_seconds": 300},
    )
    assert r.status_code == 403


async def test_timer_record_meditation(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    uid, _ = await _mk_user(telegram_id=777001)
    now = datetime.utcnow() - timedelta(minutes=1)

    r = await client.post(
        "/api/v1/practice/timer",
        headers={"X-Timer-Key": "timer-test-key"},
        json={
            "telegram_id": 777001,
            "practice_type": "meditation",
            "total_duration_seconds": 600,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["practice_type"] == "meditation"

    async with async_session() as db:
        from sqlalchemy import select
        s = (await db.execute(
            select(PracticeSession).where(PracticeSession.user_id == uid)
        )).scalars().all()
        assert len(s) == 1
        assert s[0].practice_type == "meditation"
        assert s[0].total_duration_seconds == 600


async def test_timer_record_invalid_type(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    await _mk_user(telegram_id=777002)
    r = await client.post(
        "/api/v1/practice/timer",
        headers={"X-Timer-Key": "timer-test-key"},
        json={"telegram_id": 777002, "practice_type": "running", "total_duration_seconds": 60},
    )
    assert r.status_code == 422


async def test_timer_record_missing_user(client, monkeypatch):
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    r = await client.post(
        "/api/v1/practice/timer",
        headers={"X-Timer-Key": "timer-test-key"},
        json={"telegram_id": 999990, "practice_type": "asana", "total_duration_seconds": 60},
    )
    assert r.status_code == 404


async def test_timer_record_accepts_offset_aware_iso(client, monkeypatch):
    """Бот шлёт ISO 8601 с Z — должен ложиться в naive UTC-колонку Postgres."""
    monkeypatch.setattr(settings, "TIMER_BOT_KEY", "timer-test-key")
    uid, _ = await _mk_user(telegram_id=777004)

    r = await client.post(
        "/api/v1/practice/timer",
        headers={"X-Timer-Key": "timer-test-key"},
        json={
            "telegram_id": 777004,
            "practice_type": "meditation",
            "total_duration_seconds": 300,
            "started_at": "2026-09-23T08:00:00Z",
            "completed_at": "2026-09-23T08:05:00Z",
        },
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        from sqlalchemy import select
        s = (await db.execute(
            select(PracticeSession).where(PracticeSession.user_id == uid)
        )).scalars().one()
        assert s.started_at.tzinfo is None
        assert s.started_at.hour == 8
        assert s.completed_at.hour == 8


# ---------- practice_type в history ----------

async def test_history_includes_practice_type(client):
    uid, token = await _mk_user()
    await _mk_session(uid, "meditation", 300, datetime.utcnow())
    await _mk_session(uid, "asana", 420, datetime.utcnow(), asanas=["Собака мордой вниз"])

    r = await client.get(
        "/api/v1/practice/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    types = {(s["id"], s["practice_type"]) for s in r.json()["sessions"]}
    assert ("meditation",) and any("meditation" == t for _, t in types)
    assert any("asana" == t for _, t in types)


# ---------- /practice/stats: общий streak + by_type ----------

async def test_stats_common_streak_and_by_type(client):
    uid, token = await _mk_user()
    today = datetime.utcnow()
    yesterday = today - timedelta(days=1)
    await _mk_session(uid, "asana", 600, today, asanas=["А1", "А2"])
    await _mk_session(uid, "meditation", 300, yesterday)  # вчера — медитация
    await _mk_session(uid, "meditation", 600, today)      # сегодня — ещё медитация
    # streak должен быть 2 дня подряд (вчера+сегодня), объединяя типы

    r = await client.get(
        "/api/v1/practice/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["current_streak"] == 2
    assert data["total_days"] == 2
    assert data["total_minutes"] == (600 + 300 + 600) // 60  # 25
    assert data["total_asanas_practiced"] == 2  # только asana-сессия
    assert data["by_type"]["meditation"]["sessions"] == 2
    assert data["by_type"]["meditation"]["minutes"] == 15
    assert data["by_type"]["asana"]["sessions"] == 1
    assert data["by_type"]["asana"]["minutes"] == 10
    assert data["by_type"]["pranayama"]["sessions"] == 0


# ---------- /practice/stats/series ----------

async def test_stats_series_daily_rows(client):
    uid, token = await _mk_user()
    today = datetime.utcnow()
    await _mk_session(uid, "meditation", 300, today)
    await _mk_session(uid, "asana", 60, today, asanas=["А1"])

    r = await client.get(
        "/api/v1/practice/stats/series?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["days"]) == 7
    assert len(data["minutes"]) == 7
    assert len(data["sessions"]) == 7
    assert len(data["asanas"]) == 7
    assert data["minutes"][-1] == 6   # (300+60)//60
    assert data["sessions"][-1] == 2
    assert data["asanas"][-1] == 1


async def test_stats_series_filter_by_type(client):
    uid, token = await _mk_user()
    today = datetime.utcnow()
    await _mk_session(uid, "meditation", 600, today)
    await _mk_session(uid, "asana", 120, today, asanas=["А1", "А2"])

    r = await client.get(
        "/api/v1/practice/stats/series?days=7&practice_type=meditation",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = r.json()
    assert data["minutes"][-1] == 10
    assert data["sessions"][-1] == 1
    assert data["asanas"][-1] == 0

    r = await client.get(
        "/api/v1/practice/stats/series?days=7&practice_type=asana",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = r.json()
    assert data["minutes"][-1] == 2
    assert data["sessions"][-1] == 1
    assert data["asanas"][-1] == 2


async def test_stats_series_invalid_type(client):
    uid, token = await _mk_user()
    r = await client.get(
        "/api/v1/practice/stats/series?days=7&practice_type=zzz",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_stats_series_tz_shift(client):
    uid, token = await _mk_user()
    # сегодня в 01:00 UTC; для tz=+3 это уже сегодня, без сдвига — тоже сегодня,
    # но граничный случай: практика 23:00 UTC вчера = 02:00 сегодня при tz=+3
    yesterday = datetime.utcnow() - timedelta(days=1)
    late_yesterday = yesterday.replace(hour=23, minute=0, second=0, microsecond=0)
    await _mk_session(uid, "meditation", 300, late_yesterday)

    r_all = await client.get(
        "/api/v1/practice/stats/series?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    r_tz = await client.get(
        "/api/v1/practice/stats/series?days=7&tz_offset_minutes=180",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Без сдвига практика вчера (индекс days-2), со сдвигом +3 — приехала на сегодня (последний день)
    assert r_all.json()["sessions"][-2] == 1
    assert r_all.json()["sessions"][-1] == 0
    assert r_tz.json()["sessions"][-1] == 1
    assert r_tz.json()["sessions"][-2] == 0