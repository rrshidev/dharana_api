from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, UserSubscription
from app.services.auth_service import require_user
from app.services.subscription_service import get_subscription_status
from app.services.notify_service import notify_subscription

router = APIRouter(prefix="/subscription", tags=["subscription"])


class ActivateSubscriptionRequest(BaseModel):
    subscription_type: str = "monthly"
    payment_id: str | None = None


@router.get("/status")
async def subscription_status(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_subscription_status(db, user.id)


@router.post("/activate")
async def activate_subscription(
    body: ActivateSubscriptionRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timedelta

    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        sub = UserSubscription(user_id=user.id)
        db.add(sub)

    now = datetime.utcnow()
    days = 30 if body.subscription_type == "monthly" else 365
    sub.is_premium = True
    sub.subscription_type = body.subscription_type
    sub.subscription_status = "active"
    sub.subscription_start = now
    sub.subscription_end = now + timedelta(days=days)
    sub.payment_id = body.payment_id

    await db.commit()
    await db.refresh(sub)

    await notify_subscription(user.name or user.email or f"User #{user.id}", body.subscription_type)

    return {"ok": True, "subscription_end": sub.subscription_end.isoformat()}
