"""Tests for extended admin series: date-range stats, payments, user activity, broadcast."""

import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import (
    User,
    UserSubscription,
    PracticeSession,
    Payment,
    BroadcastMessage,
)
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


async def _mk_practice(user_id, completed_at=None, duration=600):
    p = PracticeSession(
        user_id=user_id,
        status="completed",
        total_duration_seconds=duration,
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


async def _mk_payment(status="pending", created_at=None, reviewed_at=None):
    p = Payment(
        user_name="Payer",
        contact="t@t.ru",
        source="app",
        payment_method="bank",
        amount=500,
        receipt_url="/uploads/receipts/x.png",
        status=status,
        premium_days=30,
        created_at=created_at or datetime.utcnow(),
        reviewed_at=reviewed_at,
    )
    async with async_session() as db:
        db.add(p)
        await db.commit()


async def _mk_broadcast(total=10, created_at=None):
    b = BroadcastMessage(
        message="Hi",
        audience_free=True,
        audience_premium=False,
        channel_telegram=True,
        channel_app=False,
        author_id=1,
        total_recipients=total,
        telegram_pending=10,
        created_at=created_at or datetime.utcnow(),
    )
    async with async_session() as db:
        db.add(b)
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


async def test_stats_series_date_range(client):
    u = await _mk_user()
    await _mk_practice(u)
    await _mk_premium(u)
    today = datetime.utcnow().date().isoformat()
    r = await client.get(f"/api/v1/admin/stats/series?start={today}&end={today}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["days"]) == 1
    assert data["days"][0] == today
    assert data["new_users"][-1] >= 1  # admin + user
    assert data["practices"][-1] >= 1
    assert data["new_premium"][-1] >= 1


async def test_payments_series(client):
    today = datetime.utcnow()
    await _mk_payment(status="pending", created_at=today)
    await _mk_payment(status="confirmed", created_at=today, reviewed_at=today)
    await _mk_payment(status="rejected", created_at=today, reviewed_at=today)

    r = await client.get("/api/v1/admin/payments/series?days=7")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["days"]) == 7
    assert sum(data["pending"]) >= 1
    assert sum(data["confirmed"]) >= 1
    assert sum(data["rejected"]) >= 1


async def test_user_activity_series(client):
    u = await _mk_user()
    await _mk_practice(u, duration=900)  # 15 min
    today = datetime.utcnow().date().isoformat()
    r = await client.get(f"/api/v1/admin/users/{u}/activity?start={today}&end={today}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["minutes"][-1] >= 15
    assert data["sessions"][-1] >= 1


async def test_user_activity_404(client):
    r = await client.get("/api/v1/admin/users/99999/activity?days=7")
    assert r.status_code == 404


async def test_broadcast_series(client):
    today = datetime.utcnow()
    await _mk_broadcast(total=25, created_at=today)
    r = await client.get("/api/v1/admin/broadcast/series?days=7")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["days"]) == 7
    assert sum(data["campaigns"]) >= 1
    assert sum(data["recipients"]) >= 25
    assert data["total_recipients"] >= 25
