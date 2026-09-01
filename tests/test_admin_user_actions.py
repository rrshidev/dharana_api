"""Tests for admin per-user actions: message (tg/app), ban/unban, soft delete."""

import os
import uuid
from datetime import datetime

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, UserSubscription, BroadcastMessage, BroadcastDelivery
from app.services.auth_service import hash_password, create_access_token
from sqlalchemy import select as sa_select


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
        await db.refresh(user)
        return user.id


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
                subscription_start=datetime.utcnow(),
            ))
        await db.commit()
        await db.refresh(user)
        return user.id


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


async def test_message_in_app_queues_delivery(client):
    uid = await _mk_user()
    r = await client.post(f"/api/v1/admin/users/{uid}/message", json={
        "channel": "app",
        "message": "Hi in app",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["app"] == "queued"
    assert data["telegram"] == "skipped"

    async with async_session() as db:
        msgs = (await db.execute(sa_select(BroadcastMessage))).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].message == "Hi in app"
        assert msgs[0].app_pending == 1
        delivs = (await db.execute(sa_select(BroadcastDelivery))).scalars().all()
        assert len(delivs) == 1
        assert delivs[0].user_id == uid
        assert delivs[0].channel == "app"
        assert delivs[0].status == "pending"


async def test_message_validation(client):
    uid = await _mk_user()
    r = await client.post(f"/api/v1/admin/users/{uid}/message", json={
        "channel": "app", "message": "   ",
    })
    assert r.status_code == 400
    r = await client.post(f"/api/v1/admin/users/{uid}/message", json={
        "channel": "sms", "message": "x",
    })
    assert r.status_code == 400


async def test_message_unknown_user(client):
    r = await client.post("/api/v1/admin/users/999999/message", json={
        "channel": "app", "message": "x",
    })
    assert r.status_code == 404


async def test_ban_blocks_access(client):
    uid = await _mk_user()
    token = create_access_token(uid)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as u:
        u.headers.update({"Authorization": f"Bearer {token}"})
        r0 = await u.get("/api/v1/notifications/broadcast")
        assert r0.status_code == 200

    r = await client.post(f"/api/v1/admin/users/{uid}/ban", json={"banned": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_banned"] is True

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as u:
        u.headers.update({"Authorization": f"Bearer {token}"})
        r1 = await u.get("/api/v1/notifications/broadcast")
        assert r1.status_code == 403
        assert "banned" in r1.json()["detail"].lower()

    r = await client.post(f"/api/v1/admin/users/{uid}/ban", json={"banned": False})
    assert r.status_code == 200
    assert r.json()["is_banned"] is False

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as u:
        u.headers.update({"Authorization": f"Bearer {token}"})
        r2 = await u.get("/api/v1/notifications/broadcast")
        assert r2.status_code == 200


async def test_soft_delete_blocks_access_and_prevents_message(client):
    uid = await _mk_user(telegram_id=7777)
    token = create_access_token(uid)

    r = await client.post(f"/api/v1/admin/users/{uid}/delete", json={"deleted": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_deleted"] is True

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as u:
        u.headers.update({"Authorization": f"Bearer {token}"})
        r1 = await u.get("/api/v1/notifications/broadcast")
        assert r1.status_code == 403

    r = await client.post(f"/api/v1/admin/users/{uid}/message", json={
        "channel": "app", "message": "x",
    })
    assert r.status_code == 400

    r = await client.post(f"/api/v1/admin/users/{uid}/delete", json={"deleted": False})
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False

    r2 = await client.post(f"/api/v1/admin/users/{uid}/message", json={
        "channel": "app", "message": "back",
    })
    assert r2.status_code == 200
    assert r2.json()["app"] == "queued"


async def test_cannot_ban_or_delete_admin(client):
    admin = (await client.get("/api/v1/admin/users")).json()
    target = None
    for item in admin["items"]:
        if item["name"] == "Admin":
            target = item["id"]
            break
    assert target is not None

    r = await client.post(f"/api/v1/admin/users/{target}/ban", json={"banned": True})
    assert r.status_code == 400

    r = await client.post(f"/api/v1/admin/users/{target}/delete", json={"deleted": True})
    assert r.status_code == 400


async def test_list_and_detail_expose_flags(client):
    uid = await _mk_user(telegram_id=12345)
    await client.post(f"/api/v1/admin/users/{uid}/ban", json={"banned": True})

    lst = (await client.get("/api/v1/admin/users")).json()
    item = next(i for i in lst["items"] if i["id"] == uid)
    assert item["is_banned"] is True
    assert item["is_deleted"] is False

    det = (await client.get(f"/api/v1/admin/users/{uid}")).json()
    assert det["user"]["is_banned"] is True


async def test_stats_exclude_soft_deleted(client):
    await _mk_user()
    gone = await _mk_user()
    await client.post(f"/api/v1/admin/users/{gone}/delete", json={"deleted": True})

    stats = (await client.get("/api/v1/admin/stats")).json()
    # admin (from fixture) + alive user; deleted user excluded
    assert stats["total_users"] == 2


async def test_premium_series_excludes_soft_deleted(client):
    alive = await _mk_user(premium=True)
    gone = await _mk_user(premium=True)
    await client.post(f"/api/v1/admin/users/{gone}/delete", json={"deleted": True})

    stats = (await client.get("/api/v1/admin/stats")).json()
    assert stats["premium_users"] == 1  # only the alive one

    series = (await client.get("/api/v1/admin/stats/series", params={"days": 30})).json()
    assert sum(series["new_premium"]) == 1
