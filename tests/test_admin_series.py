"""Tests for /admin/stats/series (admin chart data)."""

import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, UserSubscription, PracticeSession
from app.services.auth_service import hash_password, create_access_token


def _rnd():
    return uuid.uuid4().hex[:10]


async def _mk_admin():
    user = User(
        email="admin@test.ru",
        name="Admin",
        username=_rnd(),
        hashed_password=hash_password("secret"),
    )
    async with async_session() as db:
        db.add(user)
        await db.flush()
        user.is_admin = True
        await db.commit()
        return user.id


async def _mk_user():
    user = User(
        email=f"{_rnd()}@test.ru",
        name="User",
        username=_rnd(),
        hashed_password=hash_password("secret"),
    )
    async with async_session() as db:
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


async def _mk_practice(user_id, completed_at=None):
    p = PracticeSession(
        user_id=user_id,
        status="completed",
        total_duration_seconds=600,
        asanas_practiced=[],
        completed_at=completed_at or datetime.utcnow(),
    )
    async with async_session() as db:
        db.add(p)
        await db.commit()


async def _mk_premium(user_id, started_at=None):
    sub = UserSubscription(
        user_id=user_id,
        is_premium=True,
        subscription_status="active",
        subscription_type="manual",
        subscription_start=started_at or datetime.utcnow(),
    )
    async with async_session() as db:
        db.add(sub)
        await db.commit()


@pytest.fixture
async def client():
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    admin_id = await _mk_admin()
    token = create_access_token(admin_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


async def test_series_requires_admin(client):
    # Create a non-admin user and use their token
    uid = await _mk_user()
    token = create_access_token(uid)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        r = await c.get("/api/v1/admin/stats/series")
        assert r.status_code == 403


async def test_series_empty(client):
    r = await client.get("/api/v1/admin/stats/series?days=30")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["days"]) == 30
    assert len(data["new_users"]) == 30
    assert len(data["practices"]) == 30
    assert len(data["new_premium"]) == 30
    assert sum(data["new_users"]) == 1  # only the admin user
    assert sum(data["practices"]) == 0
    assert sum(data["new_premium"]) == 0


async def test_series_counts_aggregate(client):
    u1 = await _mk_user()
    u2 = await _mk_user()
    await _mk_user()  # u3, no premium
    await _mk_practice(u1)
    await _mk_practice(u2)
    await _mk_premium(u1)
    await _mk_premium(u2)

    r = await client.get("/api/v1/admin/stats/series?days=30")
    assert r.status_code == 200, r.text
    data = r.json()
    assert sum(data["new_users"]) == 4  # admin + 3 created
    assert sum(data["practices"]) == 2
    assert sum(data["new_premium"]) == 2
    # last bucket (today) should hold at least these
    assert data["new_users"][-1] >= 4
    assert data["practices"][-1] >= 2
    assert data["new_premium"][-1] >= 2


async def test_series_respects_days_and_old_data_outside_window(client):
    # practice far in the past (beyond window) must not count
    u = await _mk_user()
    old = datetime.utcnow() - timedelta(days=120)
    await _mk_practice(u, completed_at=old)

    r = await client.get("/api/v1/admin/stats/series?days=7")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["days"]) == 7
    assert sum(data["practices"]) == 0
