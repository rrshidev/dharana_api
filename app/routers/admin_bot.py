import os
import httpx
import uuid
from datetime import datetime, timedelta, date
from fastapi import APIRouter, Depends, HTTPException, Query, Header, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import (
    User, UserSubscription, PracticeSession, Payment, BroadcastMessage, BroadcastDelivery,
    Video,
)
from app.services.video_service import video_service, DuplicateSequenceError
from app.services.notify_service import notify_admin

router = APIRouter(prefix="/admin-bot", tags=["admin-bot"])


def require_bot_key(x_bot_key: Optional[str] = Header(default=None)):
    if not settings.BOT_ADMIN_KEY:
        raise HTTPException(status_code=500, detail="BOT_ADMIN_KEY not configured")
    if x_bot_key != settings.BOT_ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid bot key")
    return True


class IsAdminRequest(BaseModel):
    telegram_id: int | None = None
    user_id: int | None = None


class SetAdminRequest(BaseModel):
    telegram_id: int | None = None
    user_id: int | None = None
    is_admin: bool = True


class SetPremiumRequest(BaseModel):
    telegram_id: int | None = None
    user_id: int | None = None
    is_premium: bool = True
    days: int | None = 30


async def _find_user(db, telegram_id: int | None = None, user_id: int | None = None):
    if user_id is not None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    if telegram_id is not None:
        result = await db.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()
    return None


@router.post("/is-admin")
async def is_admin(
    body: IsAdminRequest,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    user = await _find_user(db, body.telegram_id, body.user_id)
    return {"is_admin": bool(user and user.is_admin), "found": user is not None}


@router.get("/stats")
async def admin_bot_stats(
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    total_users = (await db.execute(select(func.count(User.id)))).scalar()
    premium_users = (await db.execute(
        select(func.count(UserSubscription.id)).where(UserSubscription.is_premium == True)
    )).scalar()

    total_sessions = (await db.execute(
        select(func.count(PracticeSession.id)).where(PracticeSession.status == "completed")
    )).scalar()

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    new_users_week = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= datetime.combine(week_ago, datetime.min.time()))
    )).scalar()

    new_users_month = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= datetime.combine(month_ago, datetime.min.time()))
    )).scalar()

    sessions_week = (await db.execute(
        select(func.count(PracticeSession.id)).where(
            PracticeSession.status == "completed",
            PracticeSession.completed_at >= datetime.combine(week_ago, datetime.min.time()),
        )
    )).scalar()

    sessions_month = (await db.execute(
        select(func.count(PracticeSession.id)).where(
            PracticeSession.status == "completed",
            PracticeSession.completed_at >= datetime.combine(month_ago, datetime.min.time()),
        )
    )).scalar()

    total_minutes = (await db.execute(
        select(func.sum(PracticeSession.total_duration_seconds)).where(PracticeSession.status == "completed")
    )).scalar() or 0

    return {
        "total_users": total_users,
        "premium_users": premium_users,
        "conversion_rate": round(premium_users / total_users * 100, 1) if total_users > 0 else 0,
        "total_sessions": total_sessions,
        "total_practice_minutes": total_minutes // 60,
        "new_users_week": new_users_week,
        "new_users_month": new_users_month,
        "sessions_week": sessions_week,
        "sessions_month": sessions_month,
    }


@router.get("/users")
async def admin_bot_users(
    search: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)
    count_query = select(func.count(User.id))

    if search:
        like = f"%{search}%"
        query = query.where(
            (User.name.ilike(like)) | (User.email.ilike(like)) | (User.username.ilike(like))
        )
        count_query = count_query.where(
            (User.name.ilike(like)) | (User.email.ilike(like)) | (User.username.ilike(like))
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    result = await db.execute(
        query.order_by(desc(User.created_at)).limit(limit)
    )
    users = result.scalars().all()

    items = []
    for u in users:
        sub_result = await db.execute(
            select(UserSubscription).where(UserSubscription.user_id == u.id)
        )
        sub = sub_result.scalar_one_or_none()
        items.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "username": u.username,
            "telegram_id": u.telegram_id,
            "is_admin": u.is_admin,
            "is_premium": sub.is_premium if sub else False,
            "total_practice_minutes": u.total_practice_minutes,
            "total_practice_days": u.total_practice_days,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {"total": total, "items": items}


@router.post("/set-admin")
async def set_admin(
    body: SetAdminRequest,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    if body.telegram_id is None and body.user_id is None:
        raise HTTPException(status_code=400, detail="Provide telegram_id or user_id")

    user = await _find_user(db, body.telegram_id, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_admin == body.is_admin:
        return {"user_id": user.id, "is_admin": user.is_admin, "changed": False}

    user.is_admin = body.is_admin
    await db.commit()
    await db.refresh(user)
    return {"user_id": user.id, "is_admin": user.is_admin, "changed": True}


@router.post("/set-premium")
async def set_premium(
    body: SetPremiumRequest,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timedelta

    if body.telegram_id is None and body.user_id is None:
        raise HTTPException(status_code=400, detail="Provide telegram_id or user_id")

    user = await _find_user(db, body.telegram_id, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    sub_result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )
    sub = sub_result.scalar_one_or_none()

    if body.is_premium:
        if sub is None:
            sub = UserSubscription(user_id=user.id)
            db.add(sub)
        now = datetime.utcnow()
        days = body.days if body.days and body.days > 0 else 30
        # Extend from today (fresh grant on cash/transfer)
        sub.is_premium = True
        sub.subscription_type = "manual"
        sub.subscription_status = "active"
        sub.subscription_start = now
        sub.subscription_end = now + timedelta(days=days)
        await db.commit()
        await db.refresh(sub)
        return {
            "user_id": user.id,
            "is_premium": True,
            "subscription_end": sub.subscription_end.isoformat(),
            "days": days,
        }
    else:
        if sub is None:
            raise HTTPException(status_code=404, detail="User has no subscription")
        changed = sub.is_premium
        sub.is_premium = False
        sub.subscription_status = "inactive"
        sub.subscription_end = None
        await db.commit()
        return {"user_id": user.id, "is_premium": False, "changed": changed}


@router.get("/subscription")
async def bot_subscription(
    telegram_id: int | None = Query(default=None),
    user_id: int | None = Query(default=None),
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Статус подписки пользователя (для бота) — источник правды по премиуму"""
    if telegram_id is None and user_id is None:
        raise HTTPException(status_code=400, detail="Provide telegram_id or user_id")

    user = await _find_user(db, telegram_id, user_id)
    if user is None:
        return {
            "user_id": None,
            "telegram_id": telegram_id,
            "is_active": False,
            "is_trial": False,
            "subscription_type": None,
            "subscription_end": None,
        }

    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()

    now = datetime.utcnow()
    is_active = False
    is_trial = False
    if sub is not None:
        if sub.is_premium and sub.subscription_end and now < sub.subscription_end:
            is_active = True
        if sub.trial_used and sub.trial_end and now < sub.trial_end:
            is_active = True
            is_trial = True

    return {
        "user_id": user.id,
        "telegram_id": user.telegram_id,
        "is_active": is_active,
        "is_trial": is_trial,
        "subscription_type": sub.subscription_type if sub else None,
        "subscription_end": sub.subscription_end.isoformat() if sub and sub.subscription_end else None,
    }


@router.get("/payments/requisites")
async def bot_payment_requisites(_=Depends(require_bot_key)):
    from app.routers.payments import REQUISITES
    return {"requisites": REQUISITES}


@router.post("/payments/receipt")
async def bot_payment_receipt(
    file: UploadFile = File(...),
    telegram_id: int = Form(...),
    user_name: str | None = Form(None),
    payment_method: str | None = Form(None),
    amount: float | None = Form(None),
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    from app.routers.payments import RECEIPT_DIR

    os.makedirs(RECEIPT_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "receipt.jpg")[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(RECEIPT_DIR, filename)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    with open(filepath, "wb") as f:
        f.write(content)

    url = f"/uploads/receipts/{filename}"
    user = await _find_user(db, telegram_id=telegram_id)
    payment = Payment(
        user_id=user.id if user else None,
        telegram_id=telegram_id,
        user_name=user_name or (user.name if user else None),
        contact=f"TG:{telegram_id}",
        source="bot",
        payment_method=payment_method,
        amount=amount,
        receipt_url=url,
        status="pending",
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    # Бот опрашивает /admin-bot/payments/pending и показывает чек админу (фото + кнопки)

    return {"id": payment.id, "status": payment.status, "receipt_url": url}


@router.get("/payments/confirmations")
async def bot_payment_confirmations(_=Depends(require_bot_key), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Payment)
        .where(Payment.status == "confirmed", Payment.notified == False)
        .order_by(Payment.reviewed_at.desc())
    )
    payments = result.scalars().all()
    # Mark as being notified so only one bot claims them
    for p in payments:
        p.notified = True
    await db.commit()

    out = []
    for p in payments:
        end = None
        if p.user_id or p.telegram_id:
            u = None
            if p.user_id:
                u = (await db.execute(select(User).where(User.id == p.user_id))).scalar_one_or_none()
            if u is None and p.telegram_id:
                u = (await db.execute(select(User).where(User.telegram_id == p.telegram_id))).scalar_one_or_none()
            if u:
                sub = (await db.execute(
                    select(UserSubscription).where(UserSubscription.user_id == u.id)
                )).scalar_one_or_none()
                if sub and sub.subscription_end:
                    end = sub.subscription_end.isoformat()
        out.append({
            "id": p.id,
            "telegram_id": p.telegram_id,
            "user_name": p.user_name,
            "payment_method": p.payment_method,
            "amount": p.amount,
            "receipt_url": p.receipt_url,
            "premium_days": p.premium_days,
            "subscription_end": end,
            "user_id": p.user_id,
        })
    return out


@router.get("/payments/rejections")
async def bot_payment_rejections(_=Depends(require_bot_key), db: AsyncSession = Depends(get_db)):
    """Отклонённые чеки (бот шлёт клиенту сообщение об отмене заявки)."""
    result = await db.execute(
        select(Payment)
        .where(Payment.status == "rejected", Payment.notified == False)
        .order_by(Payment.reviewed_at.desc())
    )
    payments = result.scalars().all()
    for p in payments:
        p.notified = True
    await db.commit()
    return [
        {
            "id": p.id,
            "telegram_id": p.telegram_id,
            "user_name": p.user_name,
            "source": p.source,
            "user_id": p.user_id,
            "payment_method": p.payment_method,
            "amount": p.amount,
            "receipt_url": p.receipt_url,
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        }
        for p in payments
    ]


@router.get("/payments/pending")
async def bot_payment_pending(_=Depends(require_bot_key), db: AsyncSession = Depends(get_db)):
    """Новые/недоставленные админу чеки (бот шлёт фото + кнопки подтверждения)."""
    result = await db.execute(
        select(Payment)
        .where(Payment.status == "pending", Payment.admin_sent == False)
        .order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    for p in payments:
        p.admin_sent = True
    await db.commit()
    return [
        {
            "id": p.id,
            "telegram_id": p.telegram_id,
            "user_name": p.user_name,
            "source": p.source,
            "user_id": p.user_id,
            "payment_method": p.payment_method,
            "amount": p.amount,
            "receipt_url": p.receipt_url,
        }
        for p in payments
    ]


class BotReviewPaymentBody(BaseModel):
    status: str = "confirmed"  # confirmed | rejected
    premium_days: int = 30


@router.post("/payments/{payment_id}/review")
async def bot_review_payment(
    payment_id: int,
    body: BotReviewPaymentBody,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Подтверждение/отклонение чека из бота (кнопки под фото)."""
    from datetime import timedelta

    if body.status not in ("confirmed", "rejected"):
        raise HTTPException(status_code=400, detail="status must be confirmed or rejected")

    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    if p.status != "pending":
        raise HTTPException(status_code=400, detail=f"Payment already {p.status}")

    p.status = body.status
    p.premium_days = body.premium_days
    p.reviewed_at = datetime.utcnow()
    p.reviewed_by = None

    granted = False
    end = None
    if body.status == "confirmed":
        user = None
        if p.user_id:
            user = (await db.execute(select(User).where(User.id == p.user_id))).scalar_one_or_none()
        if user is None and p.telegram_id:
            user = (await db.execute(select(User).where(User.telegram_id == p.telegram_id))).scalar_one_or_none()
        if user:
            sub = (await db.execute(
                select(UserSubscription).where(UserSubscription.user_id == user.id)
            )).scalar_one_or_none()
            if sub is None:
                sub = UserSubscription(user_id=user.id)
                db.add(sub)
            now = datetime.utcnow()
            nd = body.premium_days if body.premium_days and body.premium_days > 0 else 30
            sub.is_premium = True
            sub.subscription_type = "manual"
            sub.subscription_status = "active"
            sub.subscription_start = now
            sub.subscription_end = now + timedelta(days=nd)
            await db.flush()
            granted = True
            end = sub.subscription_end.isoformat()

    await db.commit()

    return {
        "id": p.id,
        "status": p.status,
        "premium_granted": granted,
        "premium_days": body.premium_days,
        "subscription_end": end,
    }


class BotAudienceBody(BaseModel):
    free: bool = False
    premium: bool = False


class BotChannelsBody(BaseModel):
    telegram: bool = False
    app: bool = False


class BotBroadcastBody(BaseModel):
    message: str
    audience: BotAudienceBody
    channels: BotChannelsBody


@router.post("/broadcast")
async def bot_create_broadcast(
    body: BotBroadcastBody,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Создать рассылку из бота (/adm_broadcast)."""
    from app.services.broadcast_service import create_broadcast, BroadcastInput

    inp = BroadcastInput(
        message=body.message,
        audience_free=body.audience.free,
        audience_premium=body.audience.premium,
        channel_telegram=body.channels.telegram,
        channel_app=body.channels.app,
        author_id=None,
    )
    try:
        result = await create_broadcast(db, inp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result.to_dict()


@router.get("/broadcast/pending")
async def bot_broadcast_pending(_=Depends(require_bot_key), db: AsyncSession = Depends(get_db)):
    """Telegram-доставки для бота (после отправки бот метит/репортит).
    Claim-on-poll: помечаем status='sent' сразу, как в rejections."""
    res = await db.execute(
        select(BroadcastDelivery, BroadcastMessage)
        .join(BroadcastMessage, BroadcastMessage.id == BroadcastDelivery.message_id)
        .where(BroadcastDelivery.channel == "telegram", BroadcastDelivery.status == "pending")
        .order_by(BroadcastDelivery.id)
    )
    rows = res.all()

    out = []
    now = datetime.utcnow()
    # Считаем по сообщениям для обновления счётчиков
    by_msg: dict[int, list] = {}
    for delivery, msg in rows:
        by_msg.setdefault(msg.id, []).append((delivery, msg))

    for msg_id, items in by_msg.items():
        for delivery, msg in items:
            delivery.status = "sent"
            delivery.sent_at = now
            out.append({
                "delivery_id": delivery.id,
                "message_id": msg.id,
                "telegram_id": delivery.telegram_id,
                "user_id": delivery.user_id,
                "message": msg.message,
            })
        bm = items[0][1]
        bm.telegram_pending -= len(items)
        bm.telegram_sent += len(items)
    await db.commit()

    return out


class BotDeliveryFailedBody(BaseModel):
    error: str | None = None


@router.post("/broadcast/{delivery_id}/failed")
async def bot_broadcast_failed(
    delivery_id: int,
    body: BotDeliveryFailedBody,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Бот сообщает, что доставка в TG не удалась."""
    result = await db.execute(
        select(BroadcastDelivery).where(BroadcastDelivery.id == delivery_id)
    )
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")

    # Пересчитать счётчики на сообщении
    msg = (await db.execute(
        select(BroadcastMessage).where(BroadcastMessage.id == d.message_id)
    )).scalar_one_or_none()

    if d.status == "sent":
        if msg and msg.telegram_sent > 0:
            msg.telegram_sent -= 1
        if msg:
            msg.telegram_failed += 1

    d.status = "failed"
    d.error = (body.error or "Telegram send failed")[:500]
    await db.commit()
    return {"delivery_id": d.id, "status": d.status}

@router.post("/broadcast/test")
async def bot_broadcast_test(
    body: BotBroadcastBody,
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Send a test broadcast to the admin only (no queue for users)."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    if not body.channels.telegram and not body.channels.app:
        raise HTTPException(status_code=400, detail="choose at least one channel")

    result = {"telegram": "skipped", "app": "skipped"}

    if body.channels.telegram:
        admin_tg = settings.ADMIN_TELEGRAM_ID
        token = settings.BOT_TOKEN
        if admin_tg and token:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": admin_tg, "text": body.message},
                        timeout=10,
                    )
                result["telegram"] = "sent" if resp.status_code == 200 else "failed"
            except Exception:
                result["telegram"] = "failed"
        else:
            result["telegram"] = "skipped"

    if body.channels.app:
        admin = None
        if settings.ADMIN_TELEGRAM_ID:
            admin = (await db.execute(
                select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID)
            )).scalar_one_or_none()
        if admin is None:
            return {**result, "app": "no_admin_user"}
        msg = BroadcastMessage(
            message=body.message,
            audience_free=body.audience.free,
            audience_premium=body.audience.premium,
            channel_telegram=False,
            channel_app=True,
            author_id=admin.id,
            total_recipients=1,
            app_pending=1,
        )
        db.add(msg)
        await db.flush()
        deliv = BroadcastDelivery(
            message_id=msg.id,
            user_id=admin.id,
            telegram_id=admin.telegram_id,
            channel="app",
            status="pending",
        )
        db.add(deliv)
        await db.commit()
        result["app"] = "queued"

    return result


@router.post("/sequences/add-video")
async def bot_add_sequence_video(
    file: UploadFile = File(...),
    name: str = Form(...),
    section: str = Form(...),
    _=Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Add a ready-sequence video (free|premium) from the bot."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    clean_name = (name or "").strip().title()
    if not clean_name:
        raise HTTPException(status_code=400, detail="name is required")
    if section not in ("free", "premium"):
        raise HTTPException(status_code=400, detail="section must be 'free' or 'premium'")

    try:
        video = await video_service.add_sequence_video(
            db,
            filename=file.filename or f"{clean_name}.mp4",
            content=content,
            name=clean_name,
            section=section,
        )
    except DuplicateSequenceError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Комплекс '{e.name}' уже существует в разделе "
                   f"{'Premium' if section == 'premium' else 'бесплатные'}. "
                   f"Переименуйте название и попробуйте снова.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить видео: {e}")

    return {
        "id": video.id,
        "name": video.sequence_name,
        "is_premium": video.is_premium,
        "video_url": f"/api/v1/media/videos/{video.filepath}",
    }
