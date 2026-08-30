import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, Payment
from app.services.auth_service import require_user, get_current_user
from app.services.subscription_service import get_or_create_subscription
from app.services.notify_service import notify_admin, notify_error

router = APIRouter(prefix="/payments", tags=["payments"])

# Реквизиты для оплаты (мок — заменятся на реальные)
REQUISITES = [
    {"bank": "Сбербанк", "card": "2202 2001 2345 6789", "holder": "Руслан Дмитриевич С."},
    {"bank": "Тинькофф", "card": "4377 7200 1234 5678", "holder": "Руслан Дмитриевич С."},
    {"bank": "Альфа-Банк", "card": "5487 9301 2345 6789", "holder": "Руслан Дмитриевич С."},
    {"bank": "ВТБ", "card": "4890 4700 1234 5678", "holder": "Руслан Дмитриевич С."},
]

RECEIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "receipts")


@router.get("/requisites")
async def get_requisites(user: User = Depends(require_user)):
    return {"requisites": REQUISITES}


class ReceiptBody(BaseModel):
    payment_method: str | None = None
    amount: float | None = None
    contact: str | None = None


@router.post("/receipt")
async def upload_receipt(
    file: UploadFile = File(...),
    payment_method: str | None = Form(None),
    amount: float | None = Form(None),
    contact: str | None = Form(None),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
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
    payment = Payment(
        user_id=user.id,
        user_name=user.name,
        contact=contact or user.email,
        source="app",
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


@router.get("/notifications")
async def get_notifications(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Непрочитанные результаты оплаты для клиента (подтверждение/отклонение) — для приложения, если нет бота."""
    from sqlalchemy import and_, or_

    result = await db.execute(
        select(Payment)
        .where(
            and_(
                Payment.user_id == user.id,
                Payment.status.in_(["confirmed", "rejected"]),
                Payment.client_seen == False,
                Payment.notified == True,
            )
        )
        .order_by(Payment.updated_at.desc())
    )
    payments = result.scalars().all()

    sub = await get_or_create_subscription(db, user.id)
    end = sub.subscription_end.isoformat() if sub.subscription_end else None

    return [
        {
            "id": p.id,
            "status": p.status,
            "premium_days": p.premium_days,
            "subscription_end": end,
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        }
        for p in payments
    ]


@router.post("/notifications/read")
async def mark_notifications_read(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import and_, update

    await db.execute(
        update(Payment)
        .where(
            and_(
                Payment.user_id == user.id,
                Payment.status.in_(["confirmed", "rejected"]),
                Payment.client_seen == False,
            )
        )
        .values(client_seen=True)
    )
    await db.commit()
    return {"ok": True}
