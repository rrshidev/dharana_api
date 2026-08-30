from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, Sequence, UserSubscription
from app.services.auth_service import require_user
from app.services.subscription_service import get_subscription_status

router = APIRouter(prefix="/sequences", tags=["sequences"])

FREE_SEQUENCE_LIMIT = 3


class SequenceItem(BaseModel):
    name: str
    duration_seconds: int = 60
    rest_seconds: int = 15


class CreateSequenceRequest(BaseModel):
    name: str
    description: str | None = None
    asanas: list[SequenceItem]
    is_public: bool = False


@router.get("")
async def list_sequences(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    status = await get_subscription_status(db, user.id)
    result = await db.execute(
        select(Sequence)
        .where(Sequence.user_id == user.id)
        .order_by(Sequence.created_at.desc())
    )
    sequences = result.scalars().all()
    is_premium = status["is_premium"]
    return {
        "is_premium": is_premium,
        "free_limit": None if is_premium else FREE_SEQUENCE_LIMIT,
        "limit_reached": (not is_premium) and len(sequences) >= FREE_SEQUENCE_LIMIT,
        "sequences": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "asanas": s.asanas,
                "is_public": s.is_public,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sequences
        ],
    }


@router.post("")
async def create_sequence(
    body: CreateSequenceRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    status = await get_subscription_status(db, user.id)
    if not status["is_premium"]:
        result = await db.execute(
            select(Sequence).where(Sequence.user_id == user.id)
        )
        count = len(result.scalars().all())
        if count >= FREE_SEQUENCE_LIMIT:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Бесплатный тариф позволяет хранить до {FREE_SEQUENCE_LIMIT} "
                    "последовательностей. Оформите Premium для безлимита."
                ),
            )

    seq = Sequence(
        user_id=user.id,
        name=body.name,
        description=body.description,
        asanas=[a.model_dump() for a in body.asanas],
        is_public=body.is_public,
    )
    db.add(seq)
    await db.commit()
    await db.refresh(seq)
    return {
        "id": seq.id,
        "name": seq.name,
        "description": seq.description,
        "asanas": seq.asanas,
        "is_public": seq.is_public,
        "created_at": seq.created_at.isoformat() if seq.created_at else None,
    }


@router.get("/{sequence_id}")
async def get_sequence(
    sequence_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user.id)
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return {
        "id": seq.id,
        "name": seq.name,
        "description": seq.description,
        "asanas": seq.asanas,
        "is_public": seq.is_public,
        "created_at": seq.created_at.isoformat() if seq.created_at else None,
    }


@router.patch("/{sequence_id}")
async def update_sequence(
    sequence_id: int,
    body: CreateSequenceRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user.id)
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")

    seq.name = body.name
    seq.description = body.description
    seq.asanas = [a.model_dump() for a in body.asanas]
    seq.is_public = body.is_public
    await db.commit()
    return {"ok": True}


@router.delete("/{sequence_id}")
async def delete_sequence(
    sequence_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user.id)
    )
    seq = result.scalar_one_or_none()
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")

    await db.delete(seq)
    await db.commit()
    return {"ok": True}
