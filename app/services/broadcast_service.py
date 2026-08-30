import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User, UserSubscription, BroadcastMessage, BroadcastDelivery

logger = logging.getLogger(__name__)


@dataclass
class BroadcastInput:
    message: str
    audience_free: bool
    audience_premium: bool
    channel_telegram: bool
    channel_app: bool
    author_id: int | None = None


@dataclass
class BroadcastResult:
    message_id: int | None = None
    count_telegram: int = 0
    count_app: int = 0

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "count_telegram": self.count_telegram,
            "count_app": self.count_app,
            "total": self.count_telegram + self.count_app,
        }


def validate_broadcast(inp: BroadcastInput) -> None:
    """Возвращает None, если всё ок, иначе строку ошибки."""
    if not inp.message or not inp.message.strip():
        return "message is required"
    if not inp.audience_free and not inp.audience_premium:
        return "choose at least one audience (free / premium)"
    if not inp.channel_app and not inp.channel_telegram:
        return "choose at least one channel (telegram / app)"
    return None


async def _eligible_user_ids(db: AsyncSession, inp: BroadcastInput) -> list[int]:
    """Юзеры, попадающие в выбранные категории (free/premium)."""
    want_premium = inp.audience_premium
    want_free = inp.audience_free

    # Все, если выбраны обе категории
    if want_free and want_premium:
        res = await db.execute(select(User.id))
        return [r for (r,) in res.all()]

    # Только одна категория -> join с подпиской (нет подписки == бесплатный)
    sub = (
        select(User.id, UserSubscription.is_premium)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .subquery()
    )
    if want_premium:
        query = select(sub.c.id).where(
            sub.c.is_premium == True,  # noqa: E712
        )
    else:  # want_free
        query = select(sub.c.id).where(
            (sub.c.is_premium == False) | (sub.c.is_premium == None),  # noqa: E712
        )
    res = await db.execute(query)
    return [r for (r,) in res.all()]


async def create_broadcast(db: AsyncSession, inp: BroadcastInput) -> BroadcastResult:
    err = validate_broadcast(inp)
    if err:
        raise ValueError(err)

    user_ids = await _eligible_user_ids(db, inp)
    if not user_ids:
        return BroadcastResult(count_telegram=0, count_app=0)

    # Получаем telegram_id для нужных юзеров одним запросом
    telegram_by_user: dict[int, int | None] = {}
    if inp.channel_telegram:
        res = await db.execute(
            select(User.id, User.telegram_id).where(User.id.in_(user_ids))
        )
        telegram_by_user = {uid: tg for uid, tg in res.all()}

    msg = BroadcastMessage(
        message=inp.message.strip(),
        audience_free=inp.audience_free,
        audience_premium=inp.audience_premium,
        channel_telegram=inp.channel_telegram,
        channel_app=inp.channel_app,
        author_id=inp.author_id,
    )
    db.add(msg)
    await db.flush()

    count_tg = 0
    count_app = 0
    for uid in user_ids:
        if inp.channel_telegram and telegram_by_user.get(uid):
            db.add(BroadcastDelivery(
                message_id=msg.id,
                user_id=uid,
                telegram_id=telegram_by_user[uid],
                channel="telegram",
                status="pending",
            ))
            count_tg += 1
        if inp.channel_app:
            db.add(BroadcastDelivery(
                message_id=msg.id,
                user_id=uid,
                channel="app",
                status="pending",
            ))
            count_app += 1

    msg.total_recipients = count_tg + count_app
    msg.telegram_pending = count_tg
    msg.app_pending = count_app
    await db.commit()

    return BroadcastResult(
        message_id=msg.id,
        count_telegram=count_tg,
        count_app=count_app,
    )
