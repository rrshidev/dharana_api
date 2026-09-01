from datetime import datetime, timedelta, date
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, UserSubscription, PracticeSession, Payment, BroadcastMessage, BroadcastDelivery, Video
from app.services.auth_service import require_user
from app.services.video_service import video_service, DuplicateSequenceError
from app.services.asana_service import asana_service
from app.services.notify_service import notify_admin, notify_error
import os
import httpx
router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/users")
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    admin: User = Depends(require_admin),
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
        query.order_by(desc(User.created_at)).limit(limit).offset(offset)
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
            "is_premium": sub.is_premium if sub else False,
            "is_banned": bool(u.is_banned),
            "is_deleted": bool(u.is_deleted),
            "total_practice_minutes": u.total_practice_minutes,
            "total_practice_days": u.total_practice_days,
            "last_practice_at": u.last_practice_at.isoformat() if u.last_practice_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    sub_result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )
    sub = sub_result.scalar_one_or_none()

    sessions_result = await db.execute(
        select(PracticeSession)
        .where(PracticeSession.user_id == user.id, PracticeSession.status == "completed")
        .order_by(desc(PracticeSession.started_at))
        .limit(10)
    )
    sessions = sessions_result.scalars().all()

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "username": user.username,
            "telegram_id": user.telegram_id,
            "avatar_url": user.avatar_url,
            "bio": user.bio,
            "is_admin": user.is_admin,
            "is_banned": bool(user.is_banned),
            "is_deleted": bool(user.is_deleted),
            "total_practice_minutes": user.total_practice_minutes,
            "total_practice_days": user.total_practice_days,
            "current_streak": user.current_streak,
            "longest_streak": user.longest_streak,
            "last_practice_at": user.last_practice_at.isoformat() if user.last_practice_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "subscription": {
            "is_premium": sub.is_premium if sub else False,
            "subscription_type": sub.subscription_type if sub else None,
            "subscription_status": sub.subscription_status if sub else None,
            "subscription_end": sub.subscription_end.isoformat() if sub and sub.subscription_end else None,
        },
        "recent_sessions": [
            {
                "id": s.id,
                "asanas_practiced": s.asanas_practiced,
                "total_duration_seconds": s.total_duration_seconds,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in sessions
        ],
    }


@router.get("/stats")
async def admin_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    total_users = (await db.execute(
        select(func.count(User.id)).where(User.is_deleted == False)
    )).scalar()
    premium_users = (await db.execute(
        select(func.count(UserSubscription.id))
        .join(User, User.id == UserSubscription.user_id)
        .where(UserSubscription.is_premium == True, User.is_deleted == False)
    )).scalar()

    total_sessions = (await db.execute(
        select(func.count(PracticeSession.id)).where(PracticeSession.status == "completed")
    )).scalar()

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    new_users_week = (await db.execute(
        select(func.count(User.id)).where(
            User.is_deleted == False,
            User.created_at >= datetime.combine(week_ago, datetime.min.time()),
        )
    )).scalar()

    new_users_month = (await db.execute(
        select(func.count(User.id)).where(
            User.is_deleted == False,
            User.created_at >= datetime.combine(month_ago, datetime.min.time()),
        )
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


@router.get("/metrics")
async def admin_metrics(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """DAU/WAU/MAU, latency, error rate, uptime и сводка по эндпоинтам.

    DAU/WAU/MAU считаются по практике (уникальные пользователи с
    PracticeSession.started_at за скользящие окна 1/7/30 дней).
    """
    now = datetime.utcnow()

    def _dau_window(days_int):
        return now - timedelta(days=days_int)

    # скользящие окна активности (уникальные юзеры по практике)
    dau = (await db.execute(
        select(func.count(func.distinct(PracticeSession.user_id))).where(
            PracticeSession.started_at >= _dau_window(1)
        )
    )).scalar()
    wau = (await db.execute(
        select(func.count(func.distinct(PracticeSession.user_id))).where(
            PracticeSession.started_at >= _dau_window(7)
        )
    )).scalar()
    mau = (await db.execute(
        select(func.count(func.distinct(PracticeSession.user_id))).where(
            PracticeSession.started_at >= _dau_window(30)
        )
    )).scalar()

    # практики сегодня / вчера (для сравнения)
    today_start = datetime.combine(now.date(), datetime.min.time())
    sessions_today = (await db.execute(
        select(func.count(PracticeSession.id)).where(
            PracticeSession.started_at >= today_start
        )
    )).scalar()

    from app.services.metrics_service import metrics_collector
    snapshot = await metrics_collector.snapshot()

    return {
        "active_users": {
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "sessions_today": sessions_today,
        },
        "api": snapshot,
    }



@router.get("/stats/series")
async def admin_stats_series(
    start: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[date] = Query(None, description="End date YYYY-MM-DD (inclusive)"),
    days: int = Query(30, ge=7, le=90),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Daily series for admin charts: new users, completed practices, new premium subs.

    Accepts either explicit start/end dates (inclusive, calendar filter) or a
    relative `days` window (backwards compatible).
    """
    if start and end:
        if end < start:
            start, end = end, start
        num_days = (end - start).days + 1
        start_date = start
    else:
        start_date = date.today() - timedelta(days=days - 1)
        num_days = days
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(start_date + timedelta(days=num_days), datetime.min.time())

    def _day_key(dt):
        return dt.date().isoformat()

    def _bucket(rows):
        counts = {}
        for r in rows:
            counts[_day_key(r)] = counts.get(_day_key(r), 0) + 1
        return counts

    users_rows = (await db.execute(
        select(User.created_at).where(
            User.is_deleted == False,
            User.created_at >= start_dt,
            User.created_at < end_dt,
        )
    )).scalars().all()
    users_by_day = _bucket(users_rows)

    prac_rows = (await db.execute(
        select(PracticeSession.completed_at).where(
            PracticeSession.status == "completed",
            PracticeSession.completed_at >= start_dt,
            PracticeSession.completed_at < end_dt,
        )
    )).scalars().all()
    prac_by_day = _bucket(prac_rows)

    prem_rows = (await db.execute(
        select(UserSubscription.subscription_start).where(
            UserSubscription.is_premium == True,
            UserSubscription.subscription_start >= start_dt,
            UserSubscription.subscription_start < end_dt,
        )
    )).scalars().all()
    prem_by_day = _bucket(prem_rows)

    labels = []
    new_users = []
    practices = []
    new_premium = []
    for i in range(num_days):
        day = (start_date + timedelta(days=i)).isoformat()
        labels.append(day)
        new_users.append(users_by_day.get(day, 0))
        practices.append(prac_by_day.get(day, 0))
        new_premium.append(prem_by_day.get(day, 0))

    return {
        "days": labels,
        "new_users": new_users,
        "practices": practices,
        "new_premium": new_premium,
    }


def _resolve_range(start, end, days):
    """Normalise start/end/days into (start_date, end_date) inclusive."""
    if start and end:
        if end < start:
            start, end = end, start
        return start, end
    default_days = days or 30
    end = date.today()
    start = end - timedelta(days=default_days - 1)
    return start, end


@router.get("/payments/series")
async def payments_series(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    days: int = Query(30, ge=7, le=90),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Payments per day: pending (created) and confirmed/rejected (reviewed)."""
    s, e = _resolve_range(start, end, days)
    s_dt = datetime.combine(s, datetime.min.time())
    e_dt = datetime.combine(e + timedelta(days=1), datetime.min.time())
    num_days = (e - s).days + 1

    def _bucket_by(rows, key_fn):
        counts = {}
        for r in rows:
            k = key_fn(r)
            if k:
                counts[k] = counts.get(k, 0) + 1
        return counts

    created_rows = (await db.execute(
        select(Payment.created_at).where(Payment.created_at >= s_dt, Payment.created_at < e_dt)
    )).scalars().all()
    created = _bucket_by(created_rows, lambda dt: dt.date().isoformat())

    reviewed_rows = (await db.execute(
        select(Payment.status, Payment.reviewed_at).where(
            Payment.reviewed_at >= s_dt, Payment.reviewed_at < e_dt
        )
    )).all()
    confirmed = {}
    rejected = {}
    for status, rv in reviewed_rows:
        if not rv:
            continue
        k = rv.date().isoformat()
        if status == "confirmed":
            confirmed[k] = confirmed.get(k, 0) + 1
        elif status == "rejected":
            rejected[k] = rejected.get(k, 0) + 1

    labels, pending, appr, rej = [], [], [], []
    for i in range(num_days):
        day = (s + timedelta(days=i)).isoformat()
        labels.append(day)
        pending.append(created.get(day, 0))
        appr.append(confirmed.get(day, 0))
        rej.append(rejected.get(day, 0))

    return {"days": labels, "pending": pending, "confirmed": appr, "rejected": rej}


@router.get("/users/{user_id}/activity")
async def user_activity_series(
    user_id: int,
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    days: int = Query(30, ge=7, le=90),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Practice volume per day for a single user (minutes)."""
    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    s, e = _resolve_range(start, end, days)
    s_dt = datetime.combine(s, datetime.min.time())
    e_dt = datetime.combine(e + timedelta(days=1), datetime.min.time())
    num_days = (e - s).days + 1

    rows = (await db.execute(
        select(PracticeSession.completed_at, PracticeSession.total_duration_seconds).where(
            PracticeSession.user_id == user_id,
            PracticeSession.status == "completed",
            PracticeSession.completed_at >= s_dt,
            PracticeSession.completed_at < e_dt,
        )
    )).all()

    minutes = {}
    sessions = {}
    for dt, dur in rows:
        if not dt:
            continue
        k = dt.date().isoformat()
        minutes[k] = minutes.get(k, 0) + ((dur or 0) / 60)
        sessions[k] = sessions.get(k, 0) + 1

    labels = []
    minutes_series = []
    sessions_series = []
    for i in range(num_days):
        day = (s + timedelta(days=i)).isoformat()
        labels.append(day)
        minutes_series.append(round(minutes.get(day, 0)))
        sessions_series.append(sessions.get(day, 0))

    return {"days": labels, "minutes": minutes_series, "sessions": sessions_series}


@router.get("/broadcast/series")
async def broadcast_series(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    days: int = Query(30, ge=7, le=90),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Broadcasts per day + recipient volume trend."""
    s, e = _resolve_range(start, end, days)
    s_dt = datetime.combine(s, datetime.min.time())
    e_dt = datetime.combine(e + timedelta(days=1), datetime.min.time())
    num_days = (e - s).days + 1

    rows = (await db.execute(
        select(BroadcastMessage.created_at, BroadcastMessage.total_recipients).where(
            BroadcastMessage.created_at >= s_dt, BroadcastMessage.created_at < e_dt
        )
    )).all()

    sent_count = {}
    recipients = {}
    total = 0
    for dt, rcpt in rows:
        if not dt:
            continue
        k = dt.date().isoformat()
        sent_count[k] = sent_count.get(k, 0) + 1
        n = rcpt or 0
        recipients[k] = recipients.get(k, 0) + n
        total += n

    labels = []
    campaigns = []
    recipients_series = []
    for i in range(num_days):
        day = (s + timedelta(days=i)).isoformat()
        labels.append(day)
        campaigns.append(sent_count.get(day, 0))
        recipients_series.append(recipients.get(day, 0))

    return {
        "days": labels,
        "campaigns": campaigns,
        "recipients": recipients_series,
        "total_recipients": total,
    }


@router.get("/activity")
async def admin_activity(
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    users_result = await db.execute(
        select(User).order_by(desc(User.created_at)).limit(limit)
    )
    recent_users = users_result.scalars().all()

    sessions_result = await db.execute(
        select(PracticeSession)
        .where(PracticeSession.status == "completed")
        .order_by(desc(PracticeSession.completed_at))
        .limit(limit)
    )
    recent_sessions = sessions_result.scalars().all()

    events = []
    for u in recent_users:
        events.append({
            "type": "new_user",
            "user_name": u.name or u.email or f"User #{u.id}",
            "timestamp": u.created_at.isoformat() if u.created_at else None,
        })
    for s in recent_sessions:
        events.append({
            "type": "practice",
            "user_id": s.user_id,
            "asanas_count": len(s.asanas_practiced) if s.asanas_practiced else 0,
            "duration_seconds": s.total_duration_seconds,
            "timestamp": s.completed_at.isoformat() if s.completed_at else None,
        })

    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return events[:limit]


class SetPremiumBody(BaseModel):
    is_premium: bool = True
    days: int = 30


@router.post("/users/{user_id}/premium")
async def set_user_premium(
    user_id: int,
    body: SetPremiumBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timedelta

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
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
        sub.is_premium = False
        sub.subscription_status = "inactive"
        sub.subscription_end = None
        await db.commit()
        return {"user_id": user.id, "is_premium": False, "changed": True}


class UserBanBody(BaseModel):
    banned: bool = True


class UserDeleteBody(BaseModel):
    deleted: bool = True


class UserMessageBody(BaseModel):
    channel: str = "both"  # telegram | app | both
    message: str


async def _get_admin_target_user(db, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_banned or user.is_deleted:
        raise HTTPException(status_code=400, detail="User is banned or deleted")
    return user


@router.post("/users/{user_id}/message")
async def send_user_message(
    user_id: int,
    body: UserMessageBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Отправить прямoe e-mail/TG/app-сообщение конкретному пользователю."""
    from app.config import settings

    channel = (body.channel or "both").strip().lower()
    if channel not in ("telegram", "app", "both"):
        raise HTTPException(status_code=400, detail="channel must be 'telegram', 'app' or 'both'")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    user = await _get_admin_target_user(db, user_id)
    result = {"telegram": "skipped", "app": "skipped"}

    if channel in ("telegram", "both"):
        if not user.telegram_id:
            result["telegram"] = "no_telegram"
        elif not settings.BOT_TOKEN:
            result["telegram"] = "no_bot"
        else:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                        json={"chat_id": user.telegram_id, "text": message},
                        timeout=10,
                    )
                result["telegram"] = "sent" if resp.status_code == 200 else "failed"
            except Exception:
                result["telegram"] = "failed"

    if channel in ("app", "both"):
        msg = BroadcastMessage(
            message=message,
            audience_free=True,
            audience_premium=True,
            channel_telegram=False,
            channel_app=True,
            author_id=admin.id,
            total_recipients=1,
            app_pending=1,
        )
        db.add(msg)
        await db.flush()
        db.add(BroadcastDelivery(
            message_id=msg.id,
            user_id=user.id,
            channel="app",
            status="pending",
        ))
        await db.commit()
        result["app"] = "queued"

    return {"user_id": user.id, **result}


@router.post("/users/{user_id}/ban")
async def set_user_ban(
    user_id: int,
    body: UserBanBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot ban an admin user")
    if user.is_deleted:
        raise HTTPException(status_code=400, detail="User is deleted")
    user.is_banned = bool(body.banned)
    await db.commit()
    return {"user_id": user.id, "is_banned": bool(user.is_banned), "changed": True}


@router.post("/users/{user_id}/delete")
async def set_user_deleted(
    user_id: int,
    body: UserDeleteBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete an admin user")
    user.is_deleted = bool(body.deleted)
    await db.commit()
    return {"user_id": user.id, "is_deleted": bool(user.is_deleted), "changed": True}


@router.get("/payments")
async def list_payments(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Payment)
    if status:
        query = query.where(Payment.status == status)
    query = query.order_by(Payment.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    payments = result.scalars().all()
    return {
        "payments": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "telegram_id": p.telegram_id,
                "user_name": p.user_name,
                "contact": p.contact,
                "source": p.source,
                "payment_method": p.payment_method,
                "amount": p.amount,
                "receipt_url": p.receipt_url,
                "status": p.status,
                "premium_days": p.premium_days,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ]
    }


@router.get("/payments/{payment_id}")
async def get_payment(
    payment_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {
        "id": p.id,
        "user_id": p.user_id,
        "telegram_id": p.telegram_id,
        "user_name": p.user_name,
        "contact": p.contact,
        "source": p.source,
        "payment_method": p.payment_method,
        "amount": p.amount,
        "receipt_url": p.receipt_url,
        "status": p.status,
        "premium_days": p.premium_days,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


class ReviewPaymentBody(BaseModel):
    status: str  # confirmed | rejected
    premium_days: int = 30


@router.post("/payments/{payment_id}/review")
async def review_payment(
    payment_id: int,
    body: ReviewPaymentBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
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
    p.reviewed_by = admin.id
    p.reviewed_at = datetime.utcnow()

    granted = False
    if body.status == "confirmed":
        granted = await _grant_premium_to_user(db, p, body.premium_days)

    await db.commit()

    notified = False
    if granted and p.telegram_id:
        # Bot polls /admin-bot/payments/confirmations to notify client with receipt
        notified = True

    try:
        await notify_admin(
            f"{'✅' if body.status == 'confirmed' else '❌'} Оплата #{p.id} "
            f"{'подтверждена' if body.status == 'confirmed' else 'отклонена'}"
        )
    except Exception:
        pass

    return {
        "id": p.id,
        "status": p.status,
        "premium_granted": granted,
        "premium_days": body.premium_days,
        "will_notify_client": notified,
    }


async def _grant_premium_to_user(db, payment: Payment, days: int) -> bool:
    user = None
    if payment.user_id:
        result = await db.execute(select(User).where(User.id == payment.user_id))
        user = result.scalar_one_or_none()
    if user is None and payment.telegram_id:
        result = await db.execute(select(User).where(User.telegram_id == payment.telegram_id))
        user = result.scalar_one_or_none()
    if user is None:
        return False

    from app.services.subscription_service import get_or_create_subscription
    sub = await get_or_create_subscription(db, user.id)
    now = datetime.utcnow()
    nd = days if days and days > 0 else 30
    sub.is_premium = True
    sub.subscription_type = "manual"
    sub.subscription_status = "active"
    sub.subscription_start = now
    sub.subscription_end = now + timedelta(days=nd)
    await db.flush()
    return True


class AudienceBody(BaseModel):
    free: bool = False
    premium: bool = False


class ChannelsBody(BaseModel):
    telegram: bool = False
    app: bool = False


class BroadcastBody(BaseModel):
    message: str
    audience: AudienceBody
    channels: ChannelsBody


@router.post("/broadcast")
async def create_broadcast(
    body: BroadcastBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Создать рассылку (скрещивание категорий × каналов)."""
    from app.services.broadcast_service import create_broadcast, BroadcastInput

    inp = BroadcastInput(
        message=body.message,
        audience_free=body.audience.free,
        audience_premium=body.audience.premium,
        channel_telegram=body.channels.telegram,
        channel_app=body.channels.app,
        author_id=admin.id,
    )
    try:
        result = await create_broadcast(db, inp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result.message_id:
        try:
            await notify_admin(
                f"📣 Создана рассылка #{result.message_id}\n"
                f"Telegram: {result.count_telegram}, Приложение: {result.count_app}"
            )
        except Exception:
            pass
    return result.to_dict()

@router.post("/broadcast/test")
async def broadcast_test(
    body: BroadcastBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Send a test broadcast to the admin only (no queue for users)."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    if not body.channels.telegram and not body.channels.app:
        raise HTTPException(status_code=400, detail="choose at least one channel")

    result = {"telegram": "skipped", "app": "skipped"}

    if body.channels.telegram:
        admin_tg = os.getenv("ADMIN_TELEGRAM_ID", "")
        token = os.getenv("BOT_TOKEN", "")
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


@router.get("/sequences")
async def admin_list_sequences(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List ready-sequence videos (for the admin panel)."""
    result = await db.execute(
        select(Video)
        .where(Video.video_type == "sequence")
        .order_by(Video.sequence_name)
    )
    videos = result.scalars().all()
    return {
        "items": [
            {
                "id": v.id,
                "name": v.sequence_name,
                "is_premium": v.is_premium,
                "filename": v.filename,
                "filepath": v.filepath,
                "video_url": f"/api/v1/media/videos/{v.filepath}",
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ]
    }


@router.post("/sequences/video")
async def admin_add_sequence_video(
    file: UploadFile = File(...),
    name: str = Form(...),
    section: str = Form(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a ready-sequence video (free|premium) from the admin panel."""
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


@router.get("/asanas")
async def admin_list_asanas(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List catalog asanas (filesystem) with their video status."""
    result = await db.execute(
        select(Video.video_type, Video.asana_name)
        .where(Video.video_type == "asana", Video.asana_name.is_not(None))
    )
    video_rows = result.all()
    video_names: set = {row.asana_name for row in video_rows if row.asana_name}

    data = asana_service.get_all_asanas(limit=10000, offset=0)
    items = []
    for a in data["items"]:
        items.append({
            "name": a["name"],
            "category_id": a["category_id"],
            "image_url": a["image_url"],
            "difficulty": a["difficulty"],
            "effects": a["effects"],
            "has_video": a["name"] in video_names,
        })
    items.sort(key=lambda x: x["name"].lower())
    return {"items": items}


@router.post("/asanas")
async def admin_create_asana(
    name: str = Form(...),
    category_id: str = Form(...),
    description: str = Form(""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new catalog asana on disk."""
    try:
        clean_name = asana_service.create_asana(
            name=name, category_id=category_id, description=description
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить асану: {e}")

    detail = asana_service.get_asana_detail(clean_name) or {}
    return {"ok": True, "asana": detail}


@router.put("/asanas/{name}/info")
async def admin_update_asana_info(
    name: str,
    description: str = Form(""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an asana's description on disk."""
    try:
        asana_service.update_asana_description(name=name, description=description)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    detail = asana_service.get_asana_detail(name) or {}
    return {"ok": True, "asana": detail}


@router.post("/asanas/{name}/photo")
async def admin_upload_asana_photo(
    name: str,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload/replace a photo for an existing asana."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    try:
        sub_path = asana_service.upload_asana_photo(
            name=name, content=content, ext=ext
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"ok": True, "image_url": f"/api/v1/media/photos/{sub_path}"}


@router.post("/asanas/{name}/video")
async def admin_upload_asana_video(
    name: str,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload/replace a video for an existing asana (catalog)."""
    # Ensure the asana exists in the filesystem catalog.
    if asana_service.get_asana_detail(name) is None:
        raise HTTPException(status_code=404, detail=f"Асана '{name}' не найдена")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        video = await video_service.add_asana_video(
            db,
            name=name,
            filename=file.filename or f"{name}.mp4",
            content=content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить видео: {e}")

    return {
        "ok": True,
        "id": video.id,
        "asana_name": video.asana_name,
        "video_url": f"/api/v1/media/videos/{video.filepath}",
    }


@router.delete("/asanas/{name}")
async def admin_delete_asana(
    name: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an asana: description + photo files, and its catalog video if any."""
    try:
        category_id = asana_service.delete_asana_files(name=name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if category_id is None:
        raise HTTPException(status_code=404, detail=f"Асана '{name}' не найдена")

    # Remove video (row + file) if present.
    result = await db.execute(
        select(Video).where(
            Video.video_type == "asana",
            Video.asana_name == name,
        )
    )
    for video in result.scalars().all():
        if video.filepath:
            abs_path = os.path.join(
                video_service.media_dir, video.filepath
            )
            if os.path.exists(abs_path):
                os.remove(abs_path)
        await db.delete(video)
    await db.commit()

    return {"ok": True, "deleted": name}


@router.put("/sequences/{video_id}")
async def admin_update_sequence(
    video_id: int,
    name: str = Form(...),
    section: str = Form(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Rename and/or move a sequence video between free/premium."""
    try:
        video = await video_service.update_sequence_video(
            db, video_id=video_id, name=name, section=section
        )
    except DuplicateSequenceError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Комплекс '{e.name}' уже существует в разделе "
                   f"{'Premium' if section == 'premium' else 'бесплатные'}.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось переместить файл: {e}")

    return {
        "ok": True,
        "id": video.id,
        "name": video.sequence_name,
        "is_premium": video.is_premium,
        "video_url": f"/api/v1/media/videos/{video.filepath}",
    }


@router.delete("/sequences/{video_id}")
async def admin_delete_sequence(
    video_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a ready-sequence video (file + row)."""
    deleted = await video_service.delete_video(db, video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Видео не найдено")
    return {"ok": True, "deleted": video_id}


@router.delete("/videos/{video_id}")
async def admin_delete_video(
    video_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete any video (asana or sequence) — file + row."""
    deleted = await video_service.delete_video(db, video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Видео не найдено")
    return {"ok": True, "deleted": video_id}
