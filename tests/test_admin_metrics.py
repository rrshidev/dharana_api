"""Integration tests for admin monitoring: /admin/metrics (DAU/WAU/MAU, latency, error rate)."""

import os
import uuid
import tempfile
from datetime import datetime, timedelta

_MEDIA = tempfile.mkdtemp(prefix="dharana_media_")
os.environ["MEDIA_DIR"] = _MEDIA

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User, PracticeSession
from app.services.auth_service import hash_password, create_access_token
from app.services.metrics_service import metrics_collector


def _rnd():
    return uuid.uuid4().hex[:8]


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
    await engine.dispose()


async def _mk_user(name, username):
    user = User(
        email=f"{username}@t.ru",
        name=name,
        username=username,
        hashed_password=hash_password("secret"),
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
        return user.id


async def test_metrics_requires_admin(client):
    uid = await _mk_user("Plain", _rnd())
    token = create_access_token(uid)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        r = await c.get("/api/v1/admin/metrics")
        assert r.status_code == 403


async def test_metrics_dau_and_api_snapshot(client):
    # seed two users with practice sessions in different recency ranges
    u1 = await _mk_user("One", _rnd())
    u2 = await _mk_user("Two", _rnd())

    now = datetime.utcnow()
    async with async_session() as db:
        db.add_all([
            # u1 active today, 3 days ago, and 40 days ago
            PracticeSession(user_id=u1, status="completed", started_at=now, completed_at=now),
            PracticeSession(user_id=u1, status="completed",
                            started_at=now - timedelta(days=3), completed_at=now - timedelta(days=3)),
            PracticeSession(user_id=u1, status="completed",
                            started_at=now - timedelta(days=40), completed_at=now - timedelta(days=40)),
            # u2 active 3 days ago only (within 7d -> WAU, not DAU; outside 1d)
            PracticeSession(user_id=u2, status="completed",
                            started_at=now - timedelta(days=3), completed_at=now - timedelta(days=3)),
        ])
        await db.commit()

    # record some fake metrics so snapshot has data
    await metrics_collector.record("/api/v1/asanas", 200, 12.5)
    await metrics_collector.record("/api/v1/asanas", 500, 300.0)

    r = await client.get("/api/v1/admin/metrics")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["active_users"]["dau"] == 1
    assert data["active_users"]["wau"] == 2
    assert data["active_users"]["mau"] == 2

    assert data["api"]["total_5xx"] == 1
    assert data["api"]["total_requests"] >= 2
    ep = next(e for e in data["api"]["endpoints"] if e["path"] == "/api/v1/asanas")
    assert ep["errors_5xx"] == 1
    assert ep["requests"] == 2
    assert ep["p50_ms"] > 0


async def test_prometheus_metrics_endpoint(client):
    # seed some data (collector is global and may have state from prior tests)
    await metrics_collector.record("/api/v1/asanas", 200, 20.0)
    await metrics_collector.record("/api/v1/asanas", 500, 100.0)
    await metrics_collector.record("/api/v1/health", 200, 5.0)

    r = await client.get("/api/v1/metrics")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text

    # structural checks: metric names present, format valid
    assert "dharana_requests_total" in body
    assert "dharana_5xx_total" in body
    assert "dharana_error_rate " in body
    assert "dharana_uptime_seconds " in body
    # per-endpoint lines exist
    assert "dharana_requests{path=\"/api/v1/asanas\"}" in body
    assert "dharana_latency_p50_ms{path=\"/api/v1/asanas\"}" in body
    assert "dharana_latency_p95_ms{path=\"/api/v1/asanas\"}" in body
    assert "dharana_5xx{path=\"/api/v1/asanas\"}" in body
    # all lines end with a number
    for line in body.strip().split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        assert line.split()[-1].replace(".", "").replace("-", "").isdigit(), f"bad line: {line}"
