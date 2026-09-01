"""Integration tests for admin content management (asanas + sequence videos).

Covers:
  * POST/GET/DELETE /admin/asanas (filesystem catalog)
  * PUT /admin/asanas/{name}/info (description)
  * POST /admin/asanas/{name}/photo
  * POST /admin/asanas/{name}/video
  * POST /admin/sequences/video, PUT/DELETE /admin/sequences/{id}
  * DELETE /admin/videos/{id}

Uses the isolated test DB + temp dirs from tests/conftest.py.
The API is exercised via httpx.ASGITransport (no live server).
"""

import os
import uuid
import tempfile

# Isolate MEDIA_DIR before any app import (BOT_DATA_DIR already set by conftest).
_MEDIA = tempfile.mkdtemp(prefix="dharana_media_")
os.environ["MEDIA_DIR"] = _MEDIA

import httpx
import pytest

from app.main import app
from app.database import Base, engine, async_session
from app.models.models import User
from app.services.auth_service import hash_password, create_access_token


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


def _img():
    return {"file": ("photo.jpg", b"\xff\xd8\xff\xe0fakejpgdata", "image/jpeg")}


def _video():
    return {"file": ("video.mp4", b"\x00\x00\x00\x18ftypisomfake", "video/mp4")}


async def test_asanas_requires_admin(client):
    user = User(
        email="plain@test.ru",
        name="Plain",
        username=_rnd(),
        hashed_password=hash_password("secret"),
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
        uid = user.id
    token = create_access_token(uid)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        r = await c.get("/api/v1/admin/asanas")
        assert r.status_code == 403


async def test_create_list_update_photo_delete_asana(client):
    # Create
    r = await client.post(
        "/api/v1/admin/asanas",
        data={"name": "Тест Асана", "category_id": "stay+", "description": "Описание теста"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["asana"]["name"] == "Тест Асана"

    # List contains it
    r = await client.get("/api/v1/admin/asanas")
    assert r.status_code == 200
    names = [a["name"] for a in r.json()["items"]]
    assert "Тест Асана" in names

    # Browser API returns it too
    r = await client.get("/api/v1/asanas/Тест%20Асана")
    assert r.status_code == 200
    assert r.json()["description"] == "Описание теста"

    # Update description
    r = await client.put(
        "/api/v1/admin/asanas/Тест%20Асана/info",
        data={"description": "Новое описание"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["asana"]["description"] == "Новое описание"

    # Upload photo
    r = await client.post(
        "/api/v1/admin/asanas/Тест%20Асана/photo", files=_img()
    )
    assert r.status_code == 200, r.text
    assert "image_url" in r.json()

    # Photo reachable
    img_url = r.json()["image_url"]
    r = await client.get(img_url)
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff\xe0fakejpgdata"

    # Delete (no video present yet)
    r = await client.delete("/api/v1/admin/asanas/Тест%20Асана")
    assert r.status_code == 200, r.text

    # Gone from catalog
    r = await client.get("/api/v1/admin/asanas")
    names = [a["name"] for a in r.json()["items"]]
    assert "Тест Асана" not in names


async def test_delete_unknown_asana_404(client):
    r = await client.delete("/api/v1/admin/asanas/Несуществующая")
    assert r.status_code == 404


async def test_asana_video_upload_and_delete(client):
    r = await client.post(
        "/api/v1/admin/asanas",
        data={"name": "Асана Видео", "category_id": "sag+", "description": "x"},
    )
    assert r.status_code == 200, r.text

    # Upload video
    r = await client.post(
        "/api/v1/admin/asanas/Асана%20Видео/video", files=_video()
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["asana_name"] == "Асана Видео"
    assert payload["video_url"].startswith("/api/v1/media/videos/catalog/")

    # Asana list shows has_video
    r = await client.get("/api/v1/admin/asanas")
    item = next(a for a in r.json()["items"] if a["name"] == "Асана Видео")
    assert item["has_video"] is True

    # Video reachable
    vid_url = payload["video_url"]
    r = await client.get(vid_url)
    assert r.status_code == 200
    assert r.content == b"\x00\x00\x00\x18ftypisomfake"

    # Delete the video via generic endpoint
    r = await client.delete(f"/api/v1/admin/videos/{payload['id']}")
    assert r.status_code == 200, r.text

    # has_video gone
    r = await client.get("/api/v1/admin/asanas")
    item = next(a for a in r.json()["items"] if a["name"] == "Асана Видео")
    assert item["has_video"] is False

    # Cleanup asana
    r = await client.delete("/api/v1/admin/asanas/Асана%20Видео")
    assert r.status_code == 200


async def test_sequence_crud(client):
    # Add sequence video
    r = await client.post(
        "/api/v1/admin/sequences/video",
        data={"name": "Утренний Комплекс", "section": "free"},
        files=_video(),
    )
    assert r.status_code == 200, r.text
    seq_id = r.json()["id"]

    # List
    r = await client.get("/api/v1/admin/sequences")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["items"]]
    assert "Утренний Комплекс" in names

    # Rename + move to premium
    r = await client.put(
        f"/api/v1/admin/sequences/{seq_id}",
        data={"name": "Вечерний Релакс", "section": "premium"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Вечерний Релакс"
    assert r.json()["is_premium"] is True
    assert r.json()["video_url"].startswith("/api/v1/media/videos/sequences/premium/")

    # Video file moved & reachable
    r = await client.get(r.json()["video_url"])
    assert r.status_code == 200

    # Delete sequence
    r = await client.delete(f"/api/v1/admin/sequences/{seq_id}")
    assert r.status_code == 200, r.text

    # Gone from list
    r = await client.get("/api/v1/admin/sequences")
    names = [s["name"] for s in r.json()["items"]]
    assert "Вечерний Релакс" not in names


async def test_sequence_duplicate_on_rename(client):
    await client.post(
        "/api/v1/admin/sequences/video",
        data={"name": "Комплекс А", "section": "free"},
        files=_video(),
    )
    r = await client.post(
        "/api/v1/admin/sequences/video",
        data={"name": "Комплекс Б", "section": "free"},
        files=_video(),
    )
    seq_b_id = r.json()["id"]

    r = await client.put(
        f"/api/v1/admin/sequences/{seq_b_id}",
        data={"name": "Комплекс А", "section": "free"},
    )
    assert r.status_code == 409