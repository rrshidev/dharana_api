"""Генератор практики: структура последовательности и лимиты генераций."""

import os
import pytest

from app.config import settings

CATALOG = {
    "sit_lie+": [
        "ТестПадмасана", "ТестСидасана", "ТестСукхасана", "ТестХаласана",
        "ТестПашчимоттанасана", "ТестДжану", "ТестБаддха", "ТестБаласана",
    ],
    "coup+": [
        "ТестСаламба", "ТестНираламба", "ТестШиршасана", "ТестАдхо",
        "ТестВипарита", "ТестКарнапидасана", "ТестСету", "ТестДханурасана",
    ],
    "stay+": [
        "ТестВирасана", "ТестУттана", "ТестПашватанасана", "ТестТрикона",
        "ТестВрикшасана", "ТестГарудасана", "ТестВирабхадрасана", "ТестТадасана",
    ],
    "sag+": [
        "ТестБхуджангасана", "ТестУштрасана", "ТестДханурасана", "ТестКапота",
        "ТестМатясана", "ТестБхекасана", "ТестШалабхасана", "ТестРаджакапота",
    ],
    "power+": [
        "ТестЧатуранга", "ТестВасиштха", "ТестПланка", "ТестКактус",
    ],
    "hand+": [
        "ТестВришчика", "ТестМайюрасана", "ТестБакасана", "ТестЭкапада",
    ],
}


def _setup_catalog():
    catalog_dir = os.path.join(settings.BOT_DATA_DIR, "catalog")
    for category, names in CATALOG.items():
        cat_dir = os.path.join(catalog_dir, category)
        os.makedirs(cat_dir, exist_ok=True)
        for name in names:
            path = os.path.join(cat_dir, f"{name}.txt")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Описание {name}")

    from app.services.asana_service import asana_service
    asana_service.refresh_catalog_cache()


async def _register(client, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secret123", "name": "Gen", "website": ""},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def test_generator_structure():
    """Генератор отдаёт разминку/основную/заминку без повторов имён."""
    _setup_catalog()
    from app.services.sequence_generator import sequence_generator

    seq = sequence_generator.generate_sequence(
        difficulty="beginner", duration_minutes=15, focus="back"
    )
    items = seq["items"]
    assert len(items) >= 9
    # Параметры в ответе.
    assert seq["params"]["duration_minutes"] == 15
    # Калории и длительность посчитаны.
    assert seq["total_duration_seconds"] > 0
    assert seq["estimated_calories"] > 0
    # Все шаги — настоящие асаны (без "Отдых").
    assert all(it["name"] != "Отдых" for it in items)
    # Дедупликация: имена не повторяются.
    names = [it["name"] for it in items]
    assert len(names) == len(set(names))
    # Rest в основной части, отсутствует в разминке/заминке.
    rest_items = [it for it in items if it["rest_seconds"]]
    assert rest_items
    non_rest = [it for it in items if not it["rest_seconds"]]
    assert all(it["rest_seconds"] == 0 for it in non_rest)
    # Каждый шаг содержит поля, нужные клиентам.
    for it in items:
        assert "name" in it and "image_url" in it and "duration_seconds" in it


@pytest.fixture
async def client():
    _setup_catalog()
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


async def test_generate_requires_auth(client):
    r = await client.post("/api/v1/practice/generate", json={})
    assert r.status_code in (401, 403)


async def test_freemium_generation_limit(client):
    token = await _register(client, "gen_free@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    body = {"difficulty": "beginner", "duration_minutes": 15, "focus": "energy"}
    first = await client.post("/api/v1/practice/generate", json=body, headers=headers)
    assert first.status_code == 200
    data = first.json()
    assert data["items"]
    assert data["daily_generations_used"] == 1
    assert data["daily_generation_limit"] == 1

    second = await client.post("/api/v1/practice/generate", json=body, headers=headers)
    assert second.status_code == 403
    detail = second.json()["detail"]
    assert detail["is_premium"] is False
    assert detail["can_generate"] is False
    assert detail["daily_generations_used"] == 1


async def test_premium_generation_unlimited(client):
    token = await _register(client, "gen_prem@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    act = await client.post("/api/v1/subscription/activate", headers=headers,
                            json={"subscription_type": "monthly"})
    assert act.status_code == 200

    body = {"difficulty": "advanced", "duration_minutes": 60, "focus": "balance"}
    for _ in range(3):
        r = await client.post("/api/v1/practice/generate", json=body, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["is_premium"] is True
        assert data["daily_generation_limit"] is None


async def test_generate_invalid_params(client):
    token = await _register(client, "gen_bad@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    bad_dur = await client.post("/api/v1/practice/generate", headers=headers,
                                json={"difficulty": "beginner", "duration_minutes": 999, "focus": "back"})
    assert bad_dur.status_code == 422

    bad_focus = await client.post("/api/v1/practice/generate", headers=headers,
                                  json={"difficulty": "beginner", "duration_minutes": 15, "focus": "nope"})
    assert bad_focus.status_code == 422

    bad_diff = await client.post("/api/v1/practice/generate", headers=headers,
                                 json={"difficulty": "hardcore", "duration_minutes": 15, "focus": "back"})
    assert bad_diff.status_code == 422