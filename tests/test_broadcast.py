"""Tests for broadcast flow: audience x channel, bot queue, in-app."""

import os
import uuid

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, UserSubscription, BroadcastMessage, BroadcastDelivery
from app.services.auth_service import hash_password, create_access_token
from sqlalchemy import select as sa_select

BOT_KEY = os.environ["BOT_ADMIN_KEY"]
_admin_id = None


def _rnd():
    return uuid.uuid4().hex[:10]


async def _mk_user(telegram_id=None, premium=False):
    user = User(
        email=f"{_rnd()}@test.ru",
        name="User",
        username=_rnd(),
        hashed_password=hash_password("secret"),
        telegram_id=telegram_id,
    )
    async with async_session() as db:
        db.add(user)
        await db.flush()
        if premium:
            db.add(UserSubscription(
                user_id=user.id,
                is_premium=True,
                subscription_status="active",
                subscription_type="manual",
            ))
        await db.commit()
        await db.refresh(user)
        return user.id


async def _mk_admin():
    global _admin_id
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
        await db.refresh(user)
        _admin_id = user.id
        return user.id


async def _count_users(tg_only, premium, free):
    async with async_session() as db:
        if tg_only:
            rows = await db.execute(sa_select(User).where(
                User.telegram_id != None))
        else:
            rows = await db.execute(sa_select(User))
        users = rows.scalars().all()
        count = 0
        for u in users:
            sub = (await db.execute(sa_select(UserSubscription).where(
                UserSubscription.user_id == u.id))).scalar_one_or_none()
            is_prem = bool(sub and sub.is_premium)
            if premium and is_prem:
                count += 1
            elif free and not is_prem:
                count += 1
            elif (premium and free) or (not premium and not free):
                count += 1
        return count


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


async def test_audience_cross_channel_correct(client):
    await _mk_user(telegram_id=1001, premium=True)
    await _mk_user(premium=True)
    await _mk_user(telegram_id=3003, premium=False)
    await _mk_user(premium=False)

    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "Hi premium",
        "audience": {"free": False, "premium": True},
        "channels": {"telegram": True, "app": False},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count_telegram"] == await _count_users(True, True, False)
    assert data["count_app"] == 0

    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "Hi free",
        "audience": {"free": True, "premium": False},
        "channels": {"telegram": True, "app": True},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count_telegram"] == await _count_users(True, False, True)
    assert data["count_app"] == await _count_users(False, False, True)


async def test_all_audiences_all_channels_all_users(client):
    await _mk_user(telegram_id=5001, premium=True)
    await _mk_user(premium=False)
    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "Everyone both",
        "audience": {"free": True, "premium": True},
        "channels": {"telegram": True, "app": True},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count_telegram"] == await _count_users(True, True, True)
    assert data["count_app"] == await _count_users(False, True, True)


async def test_validation_errors(client):
    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "x",
        "audience": {"free": False, "premium": False},
        "channels": {"telegram": True, "app": False},
    })
    assert r.status_code == 400
    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "x",
        "audience": {"free": True, "premium": False},
        "channels": {"telegram": False, "app": False},
    })
    assert r.status_code == 400
    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "   ",
        "audience": {"free": True, "premium": False},
        "channels": {"telegram": True, "app": False},
    })
    assert r.status_code == 400


async def test_bot_broadcast_and_queue(client):
    await _mk_user(telegram_id=7777, premium=True)
    await _mk_user(premium=False)
    r = await client.post("/api/v1/admin-bot/broadcast",
                          headers={"X-Bot-Key": BOT_KEY},
                          json={
                              "message": "From bot",
                              "audience": {"free": False, "premium": True},
                              "channels": {"telegram": True, "app": False},
                          })
    assert r.status_code == 200, r.text
    assert r.json()["count_telegram"] == await _count_users(True, True, False)

    pending = await client.get("/api/v1/admin-bot/broadcast/pending",
                               headers={"X-Bot-Key": BOT_KEY})
    assert pending.status_code == 200, pending.text
    items = pending.json()
    assert len(items) == 1
    assert items[0]["telegram_id"] == 7777
    assert items[0]["message"] == "From bot"

    again = await client.get("/api/v1/admin-bot/broadcast/pending",
                             headers={"X-Bot-Key": BOT_KEY})
    assert again.json() == []

    bad = await client.get("/api/v1/admin-bot/broadcast/pending",
                           headers={"X-Bot-Key": "wrong"})
    assert bad.status_code == 403


async def test_in_app_notifications(client):
    uid = await _mk_user(telegram_id=9999, premium=True)
    token = create_access_token(uid)
    r = await client.post("/api/v1/admin/broadcast", json={
        "message": "In-app hi",
        "audience": {"free": False, "premium": True},
        "channels": {"telegram": False, "app": True},
    })
    assert r.status_code == 200, r.text
    assert r.json()["count_app"] == await _count_users(False, True, False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        got = await c.get("/api/v1/notifications/broadcast")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["unread"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["message"] == "In-app hi"
        assert body["items"][0]["is_read"] is False

        read = await c.post("/api/v1/notifications/broadcast/read")
        assert read.status_code == 200, read.text
        assert read.json()["marked"] == 1

        got2 = await c.get("/api/v1/notifications/broadcast")
        body2 = got2.json()
        assert body2["unread"] == 0
        assert len(body2["items"]) == 1
        assert body2["items"][0]["is_read"] is True

async def test_admin_broadcast_test_endpoint(client):
    await _mk_user(telegram_id=1234, premium=True)

    r = await client.post("/api/v1/admin/broadcast/test", json={
        "message": "Test for admin",
        "audience": {"free": True, "premium": False},
        "channels": {"telegram": True, "app": True},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["app"] == "queued"
    assert data["telegram"] in ("sent", "failed", "skipped")

    async with async_session() as db:
        delivs = (await db.execute(sa_select(BroadcastDelivery))).scalars().all()
        assert len(delivs) == 1
        d = delivs[0]
        assert d.user_id == _admin_id
        assert d.channel == "app"
        assert d.status == "pending"
        msgs = (await db.execute(sa_select(BroadcastMessage))).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].message == "Test for admin"

    r = await client.post("/api/v1/admin/broadcast/test", json={
        "message": "no channel",
        "audience": {"free": True, "premium": False},
        "channels": {"telegram": False, "app": False},
    })
    assert r.status_code == 400

async def db_count_deliveries():
    async with async_session() as db:
        return len((await db.execute(sa_select(BroadcastDelivery))).scalars().all())

async def test_admin_bot_broadcast_test_endpoint(client):
    r = await client.post("/api/v1/admin-bot/broadcast/test",
                          headers={"X-Bot-Key": BOT_KEY},
                          json={
                              "message": "Bot test",
                              "audience": {"free": True, "premium": False},
                              "channels": {"telegram": True, "app": False},
                          })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["app"] == "skipped"
    assert data["telegram"] in ("sent", "failed", "skipped")
    # no user deliveries should exist
    deliv_count = (await db_count_deliveries())
    assert deliv_count == 0

    r = await client.post("/api/v1/admin-bot/broadcast/test",
                          headers={"X-Bot-Key": BOT_KEY},
                          json={
                              "message": "no channel",
                              "audience": {"free": True, "premium": False},
                              "channels": {"telegram": False, "app": False},
                          })
    assert r.status_code == 400
