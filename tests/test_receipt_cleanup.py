"""Тест фоновой очистки физических файлов чеков.

Проверяет, что `run_receipt_cleanup` удаляет файл чека только тогда, когда
статус/срок позволяют (см. схему хранения), а остальные файлы сохраняет.
Записи в БД при этом не удаляются.

Запуск: .venv/Scripts/python.exe -m pytest tests/test_receipt_cleanup.py -v
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from app.database import init_db, async_session
from app.models.models import User, Payment, UserSubscription
from app.services.auth_service import hash_password
from app.services import receipt_cleanup
import app.routers.payments as payments_module


@pytest.fixture(autouse=True)
async def _isolate_receipt_dir(tmp_path, monkeypatch):
    """Направляем RECEIPT_DIR во временный каталог для каждого теста."""
    monkeypatch.setattr(payments_module, "RECEIPT_DIR", str(tmp_path))
    monkeypatch.setattr(receipt_cleanup, "RECEIPT_DIR", str(tmp_path))
    await init_db()
    yield


@pytest.fixture
def receipt_dir(tmp_path) -> str:
    return str(tmp_path)


def _touch(receipt_dir: str, filename: str):
    path = os.path.join(receipt_dir, filename)
    with open(path, "wb") as f:
        f.write(b"fake-receipt")
    return path


async def _make_payment(
    status, review_date=None, created_date=None, user_id=None, filename="receipt.jpg"
):
    payment = Payment(
        user_id=user_id,
        user_name="Клиент Тест",
        contact="c@t.ru",
        source="app",
        payment_method="Сбербанк",
        amount=499.0,
        receipt_url=f"/uploads/receipts/{filename}",
        status=status,
    )
    if review_date:
        payment.reviewed_at = review_date
    if created_date:
        payment.created_at = created_date
    async with async_session() as db:
        db.add(payment)
        await db.commit()
        await db.refresh(payment)
        return payment.id


async def _make_user_with_sub(days_from_now=30):
    """Создаёт пользователя с подпиской, кончающейся через days_from_now дней."""
    async with async_session() as db:
        user = User(
            email=f"u_{os.urandom(4).hex()}@t.ru",
            name="Клиент",
            username=f"u_{os.urandom(4).hex()}",
            hashed_password=hash_password("secret"),
        )
        db.add(user)
        await db.flush()
        sub = UserSubscription(
            user_id=user.id,
            is_premium=True,
            subscription_status="active",
            subscription_end=datetime.utcnow() + timedelta(days=days_from_now),
        )
        db.add(sub)
        await db.commit()
        return user.id


async def test_rejected_old_file_removed(receipt_dir):
    """Отклонённый чек старше 7 дней — файл удаляется, запись остаётся."""
    _touch(receipt_dir, "old_rejected.jpg")
    pid = await _make_payment(
        status="rejected",
        review_date=datetime.utcnow() - timedelta(days=10),
        filename="old_rejected.jpg",
    )
    await receipt_cleanup.run_receipt_cleanup()
    assert not os.path.exists(os.path.join(receipt_dir, "old_rejected.jpg"))
    async with async_session() as db:
        from sqlalchemy import select
        p = (await db.execute(select(Payment).where(Payment.id == pid))).scalar_one()
        assert p.status == "rejected"  # запись в БД не удалена


async def test_rejected_recent_file_kept(receipt_dir):
    """Отклонённый чек свежий (меньше 7 дней) — файл сохраняется."""
    _touch(receipt_dir, "recent_rejected.jpg")
    await _make_payment(
        status="rejected",
        review_date=datetime.utcnow() - timedelta(days=1),
        filename="recent_rejected.jpg",
    )
    await receipt_cleanup.run_receipt_cleanup()
    assert os.path.exists(os.path.join(receipt_dir, "recent_rejected.jpg"))


async def test_confirmed_expired_subscription_file_removed(receipt_dir):
    """Подтверждённый чек, подписка уже истекла (+запас) — файл удаляется."""
    user_id = await _make_user_with_sub(days_from_now=-10)  # подписка закончилась
    _touch(receipt_dir, "confirmed_expired.jpg")
    await _make_payment(
        status="confirmed",
        review_date=datetime.utcnow() - timedelta(days=30),
        user_id=user_id,
        filename="confirmed_expired.jpg",
    )
    await receipt_cleanup.run_receipt_cleanup()
    assert not os.path.exists(os.path.join(receipt_dir, "confirmed_expired.jpg"))


async def test_confirmed_active_subscription_file_kept(receipt_dir):
    """Подтверждённый чек, подписка всё ещё активна — файл сохраняется."""
    user_id = await _make_user_with_sub(days_from_now=30)  # подписка активна
    _touch(receipt_dir, "confirmed_active.jpg")
    await _make_payment(
        status="confirmed",
        review_date=datetime.utcnow() - timedelta(days=5),
        user_id=user_id,
        filename="confirmed_active.jpg",
    )
    await receipt_cleanup.run_receipt_cleanup()
    assert os.path.exists(os.path.join(receipt_dir, "confirmed_active.jpg"))


async def test_pending_old_file_removed(receipt_dir):
    """Зависший (pending) чек старше 30 дней — файл удаляется."""
    _touch(receipt_dir, "old_pending.jpg")
    await _make_payment(
        status="pending",
        created_date=datetime.utcnow() - timedelta(days=35),
        filename="old_pending.jpg",
    )
    await receipt_cleanup.run_receipt_cleanup()
    assert not os.path.exists(os.path.join(receipt_dir, "old_pending.jpg"))
