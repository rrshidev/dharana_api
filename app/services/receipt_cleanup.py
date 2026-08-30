import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.models import Payment, User, UserSubscription
from app.routers.payments import RECEIPT_DIR

logger = logging.getLogger(__name__)

# Сроки хранения физических файлов чеков (записи в БД не удаляются).
REJECTED_KEEP_DAYS = 7      # отклонённые храним недолго (на случай спора/ошибки админа)
PENDING_KEEP_DAYS = 30      # зависшие/непроверенные
CONFIRMED_GRACE_DAYS = 7    # запас после окончания подписки

CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60  # раз в 6 часов


def _physical_path(receipt_url: str | None):
    """Превращает /uploads/receipts/xxx.jpg в реальный путь файла на диске."""
    if not receipt_url:
        return None
    filename = os.path.basename(receipt_url.rstrip("/"))
    if not filename:
        return None
    return os.path.join(RECEIPT_DIR, filename)


async def _delete_file(receipt_url: str | None) -> bool:
    path = _physical_path(receipt_url)
    if not path:
        return False
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"receipt file removed: {path}")
            return True
    except Exception as e:
        logger.error(f"failed to remove receipt file {path}: {e}")
    return False


async def _expired_subscription_end(user_id: int, threshold: datetime):
    """Возвращает subscription_end, если у пользователя всё ещё действует подписка за threshold."""
    async with async_session() as db:
        res = await db.execute(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        )
        sub = res.scalar_one_or_none()
        if sub and sub.subscription_end:
            return sub.subscription_end
        return None


async def run_receipt_cleanup():
    """Один проход очистки старых чеков."""
    now = datetime.utcnow()
    rejected_cutoff = now - timedelta(days=REJECTED_KEEP_DAYS)
    pending_cutoff = now - timedelta(days=PENDING_KEEP_DAYS)
    confirmed_threshold = now - timedelta(days=CONFIRMED_GRACE_DAYS)

    removed = 0
    async with async_session() as db:
        # Отклонённые старше N дней
        res = await db.execute(
            select(Payment).where(
                Payment.status == "rejected",
                Payment.reviewed_at != None,
                Payment.reviewed_at <= rejected_cutoff,
            )
        )
        for p in res.scalars().all():
            if await _delete_file(p.receipt_url):
                removed += 1

        # Зависшие (pending) старше N дней
        res = await db.execute(
            select(Payment).where(
                Payment.status == "pending",
                Payment.created_at <= pending_cutoff,
            )
        )
        for p in res.scalars().all():
            if await _delete_file(p.receipt_url):
                removed += 1

        # Подтверждённые: удаляем только если подписка уже истекла (с запасом)
        res = await db.execute(
            select(Payment).where(Payment.status == "confirmed")
        )
        for p in res.scalars().all():
            end = await _expired_subscription_end(p.user_id, confirmed_threshold)
            if end is None:
                continue
            if end <= confirmed_threshold:
                if await _delete_file(p.receipt_url):
                    removed += 1

    if removed:
        logger.info(f"receipt cleanup removed {removed} file(s)")
    return removed


async def receipt_cleanup_loop():
    """Фоновая задача: периодически чистит старые чеки."""
    while True:
        try:
            await run_receipt_cleanup()
        except Exception as e:
            logger.error(f"receipt cleanup loop error: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
