from datetime import datetime, date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import UserSubscription


async def get_or_create_subscription(db: AsyncSession, user_id: int) -> UserSubscription:
    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        sub = UserSubscription(user_id=user_id)
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    return sub


async def get_subscription_status(db: AsyncSession, user_id: int) -> dict:
    sub = await get_or_create_subscription(db, user_id)

    now = datetime.utcnow()
    is_active = False
    if sub.is_premium and sub.subscription_end and now < sub.subscription_end:
        is_active = True
    if sub.trial_used and sub.trial_end and now < sub.trial_end:
        is_active = True

    can_generate = True
    if not is_active:
        today = date.today()
        if sub.last_generation_date == today:
            can_generate = sub.daily_generations_used < 1
        else:
            can_generate = True

    return {
        "is_premium": is_active,
        "subscription_type": sub.subscription_type,
        "subscription_status": sub.subscription_status if is_active else "inactive",
        "subscription_end": sub.subscription_end.isoformat() if sub.subscription_end else None,
        "trial_used": sub.trial_used,
        "can_generate": can_generate,
        "daily_generations_used": sub.daily_generations_used,
    }


async def activate_trial(db: AsyncSession, user_id: int, days: int = 7) -> UserSubscription:
    from datetime import timedelta

    sub = await get_or_create_subscription(db, user_id)
    now = datetime.utcnow()
    sub.trial_used = True
    sub.trial_start = now
    sub.trial_end = now + timedelta(days=days)
    sub.is_premium = True
    sub.subscription_type = "trial"
    sub.subscription_status = "active"
    sub.subscription_start = now
    sub.subscription_end = now + timedelta(days=days)
    await db.commit()
    await db.refresh(sub)
    return sub
