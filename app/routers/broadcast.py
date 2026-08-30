from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, BroadcastMessage, BroadcastDelivery
from app.services.auth_service import require_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/broadcast")
async def get_broadcast_notifications(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Все рассылки текущего пользователя (канал app): прочитанные и нет, новые сверху."""
    res = await db.execute(
        select(BroadcastDelivery, BroadcastMessage)
        .join(BroadcastMessage, BroadcastMessage.id == BroadcastDelivery.message_id)
        .where(
            BroadcastDelivery.user_id == current_user.id,
            BroadcastDelivery.channel == "app",
        )
        .order_by(BroadcastDelivery.id.desc())
    )
    rows = res.all()
    unread = 0
    items = []
    for d, m in rows:
        is_read = d.status == "read"
        if not is_read:
            unread += 1
        items.append({
            "delivery_id": d.id,
            "message_id": m.id,
            "message": m.message,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "is_read": is_read,
        })
    return {"items": items, "unread": unread}


@router.post("/broadcast/read")
async def mark_broadcast_read(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Пометить app-рассылки пользователя как прочитанные."""
    res = await db.execute(
        select(BroadcastDelivery)
        .where(
            BroadcastDelivery.user_id == current_user.id,
            BroadcastDelivery.channel == "app",
            BroadcastDelivery.status == "pending",
        )
    )
    deliveries = res.scalars().all()

    from datetime import datetime
    now = datetime.utcnow()
    by_msg: dict[int, list] = {}
    for d in deliveries:
        d.status = "read"
        d.sent_at = now
        by_msg.setdefault(d.message_id, []).append(d)

    for msg_id, items in by_msg.items():
        msg = (await db.execute(
            select(BroadcastMessage).where(BroadcastMessage.id == msg_id)
        )).scalar_one_or_none()
        if msg:
            msg.app_pending -= len(items)
            msg.app_read += len(items)

    await db.commit()
    return {"marked": len(deliveries)}
