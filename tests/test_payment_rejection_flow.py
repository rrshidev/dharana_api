"""Интеграционный тест флоу отклонения заявки на оплату Premium.

Проверяет цепочку «пользователь отправил чек -> админ отклонил -> клиент
получил уведомление» по обоим каналам:
  * приложение  -> GET /api/v1/payments/notifications (status=rejected)
  * бот         -> GET /api/v1/admin-bot/payments/rejections (telegram_id)

Запуск (из каталога dharana-api):
    .venv/Scripts/python.exe -m pytest tests/test_payment_rejection_flow.py -v

Тест использует изолированную SQLite-базу во временном каталоге, не трогает
реальную БД и не требует запущенного сервера (httpx.ASGITransport).
"""

import os

import httpx
import pytest

from app.main import app
from app.database import init_db, async_session
from app.models.models import User
from app.services.auth_service import hash_password, create_access_token
from sqlalchemy import select as sa_select

BOT_KEY = os.environ["BOT_ADMIN_KEY"]


def _files(data: bytes) -> dict:
    return {"file": ("receipt.jpg", data, "image/jpeg")}


async def _seed_user() -> int:
    """Создаёт тестового пользователя, если его ещё нет. Возвращает user.id."""
    async with async_session() as db:
        existing = (
            await db.execute(sa_select(User).where(User.email == "client@test.ru"))
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        user = User(
            email="client@test.ru",
            name="Клиент Тест",
            username="client_test",
            hashed_password=hash_password("secret"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


@pytest.fixture(scope="module")
async def client():
    await init_db()
    user_id = await _seed_user()
    token = create_access_token(user_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


async def _create_payment(source="app", telegram_id=None, user_id=None):
    from app.models.models import Payment

    if user_id is None:
        user_id = await _seed_user()
    payment = Payment(
        user_id=user_id,
        telegram_id=telegram_id,
        user_name="Клиент Тест",
        contact="client@test.ru",
        source=source,
        payment_method="Сбербанк",
        amount=499.0,
        receipt_url="/uploads/receipts/test.jpg",
        status="pending",
    )
    async with async_session() as db:
        db.add(payment)
        await db.commit()
        await db.refresh(payment)
        return payment.id


async def _reject(client, payment_id):
    return await client.post(
        f"/api/v1/admin-bot/payments/{payment_id}/review",
        headers={"X-Bot-Key": BOT_KEY},
        json={"status": "rejected", "premium_days": 30},
    )


async def _user_id() -> int:
    return await _seed_user()


async def test_app_receipt_rejected_notifies_client_in_app(client):
    """Чек из приложения: после отклонения клиент видит уведомление status=rejected."""
    resp = await client.post(
        "/api/v1/payments/receipt",
        data={"payment_method": "Сбербанк", "amount": "499", "contact": "client@test.ru"},
        files=_files(b"fake-jpeg-bytes"),
    )
    assert resp.status_code == 200, resp.text
    payment_id = resp.json()["id"]

    review = await _reject(client, payment_id)
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "rejected"

    rej = await client.get(
        "/api/v1/admin-bot/payments/rejections", headers={"X-Bot-Key": BOT_KEY}
    )
    assert rej.status_code == 200
    assert any(p["id"] == payment_id for p in rej.json())

    notif = await client.get("/api/v1/payments/notifications")
    assert notif.status_code == 200
    assert any(n.get("id") == payment_id and n.get("status") == "rejected"
               for n in notif.json())


async def test_bot_receipt_rejected_returns_telegram_id(client):
    """Чек из бота: отклонение отдаётся боту с telegram_id и не повторяется."""
    payment_id = await _create_payment(source="bot", telegram_id=123456789)

    review = await _reject(client, payment_id)
    assert review.status_code == 200

    rej = await client.get(
        "/api/v1/admin-bot/payments/rejections", headers={"X-Bot-Key": BOT_KEY}
    )
    hit = next((p for p in rej.json() if p["id"] == payment_id), None)
    assert hit is not None
    assert hit["telegram_id"] == 123456789
    assert hit["source"] == "bot"

    rej2 = await client.get(
        "/api/v1/admin-bot/payments/rejections", headers={"X-Bot-Key": BOT_KEY}
    )
    assert not any(p["id"] == payment_id for p in rej2.json())


async def test_client_notifications_read_marks_seen(client):
    """После просмотра уведомления клиентом оно не отдаётся повторно."""
    payment_id = await _create_payment(source="app")

    await _reject(client, payment_id)
    await client.get("/api/v1/admin-bot/payments/rejections", headers={"X-Bot-Key": BOT_KEY})

    read = await client.post("/api/v1/payments/notifications/read")
    assert read.status_code == 200

    notif = await client.get("/api/v1/payments/notifications")
    assert not any(n.get("id") == payment_id for n in notif.json())


async def test_requisites_include_card_and_holder(client):
    """Реквизиты содержат номер карты (card) и получателя (holder); получатель один."""
    resp = await client.get("/api/v1/payments/requisites")
    assert resp.status_code == 200
    reqs = resp.json()["requisites"]
    assert len(reqs) > 0
    assert all(r.get("card") for r in reqs)
    assert all(r.get("holder") for r in reqs)
    holder = reqs[0]["holder"]
    assert all(r.get("holder") == holder for r in reqs)
