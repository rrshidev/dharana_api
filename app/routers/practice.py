from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, PracticeSession
from app.services.auth_service import require_user
from app.services.subscription_service import get_subscription_status

router = APIRouter(prefix="/practice", tags=["practice"])

FREE_REPEATABLE_LIMIT = 3


class StartSessionRequest(BaseModel):
    sequence_id: int | None = None


class CompleteSessionRequest(BaseModel):
    asanas_practiced: list[str] = []
    asana_durations: dict[str, int] = {}
    rest_seconds: int = 15


@router.post("/start")
async def start_session(
    body: StartSessionRequest = StartSessionRequest(),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PracticeSession).where(
            PracticeSession.user_id == user.id,
            PracticeSession.status == "active",
        )
    )
    active = result.scalar_one_or_none()
    if active:
        raise HTTPException(status_code=400, detail="Active session already exists")

    session = PracticeSession(user_id=user.id, sequence_id=body.sequence_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"id": session.id, "status": session.status, "started_at": session.started_at.isoformat()}


@router.put("/{session_id}/complete")
async def complete_session(
    session_id: int,
    body: CompleteSessionRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PracticeSession).where(
            PracticeSession.id == session_id,
            PracticeSession.user_id == user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    total_seconds = sum(body.asana_durations.values())

    session.status = "completed"
    session.asanas_practiced = body.asanas_practiced
    session.asana_durations = body.asana_durations
    session.rest_seconds = body.rest_seconds
    session.total_duration_seconds = total_seconds
    session.completed_at = datetime.utcnow()

    user.total_practice_minutes += total_seconds // 60
    today = date.today().isoformat()
    user.last_practice_at = datetime.utcnow()

    result = await db.execute(
        select(PracticeSession).where(
            PracticeSession.user_id == user.id,
            PracticeSession.status == "completed",
        )
    )
    unique_days = set()
    for s in result.scalars().all():
        if s.started_at:
            unique_days.add(s.started_at.date().isoformat())
    user.total_practice_days = len(unique_days)

    await db.commit()
    return {
        "ok": True,
        "total_duration_seconds": total_seconds,
        "asanas_count": len(body.asanas_practiced),
    }


@router.get("/history")
async def practice_history(
    limit: int = 20,
    offset: int = 0,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Need all completed sessions (ordered) to compute repeatable window for free users
    all_result = await db.execute(
        select(PracticeSession)
        .where(PracticeSession.user_id == user.id, PracticeSession.status == "completed")
        .order_by(PracticeSession.started_at.desc())
    )
    all_sessions = all_result.scalars().all()

    status = await get_subscription_status(db, user.id)
    is_premium = status["is_premium"]

    sessions = all_sessions[offset:offset + limit]

    return {
        "is_premium": is_premium,
        "free_repeatable_limit": None if is_premium else FREE_REPEATABLE_LIMIT,
        "sessions": [
            {
                "id": s.id,
                "asanas_practiced": s.asanas_practiced,
                "asana_durations": s.asana_durations or {},
                "rest_seconds": s.rest_seconds,
                "total_duration_seconds": s.total_duration_seconds,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                # Only the 3 most recent are repeatable for free users
                "can_repeat": is_premium or (offset + idx) < FREE_REPEATABLE_LIMIT,
            }
            for idx, s in enumerate(sessions)
        ],
    }


@router.get("/stats")
async def practice_stats(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PracticeSession).where(
            PracticeSession.user_id == user.id,
            PracticeSession.status == "completed",
        )
    )
    sessions = result.scalars().all()

    total_minutes = sum(s.total_duration_seconds for s in sessions) // 60
    total_days = len(set(s.started_at.date() for s in sessions if s.started_at))
    total_asanas = sum(len(s.asanas_practiced) for s in sessions)

    today = date.today()
    current_streak = 0
    check_date = today
    practiced_dates = set(s.started_at.date() for s in sessions if s.started_at)
    while check_date in practiced_dates:
        current_streak += 1
        from datetime import timedelta
        check_date -= timedelta(days=1)

    favorite_asanas = {}
    for s in sessions:
        for name in s.asanas_practiced:
            favorite_asanas[name] = favorite_asanas.get(name, 0) + 1
    top_asanas = sorted(favorite_asanas.items(), key=lambda x: -x[1])[:5]

    return {
        "total_minutes": total_minutes,
        "total_days": total_days,
        "total_sessions": len(sessions),
        "total_asanas_practiced": total_asanas,
        "current_streak": current_streak,
        "favorite_asanas": [{"name": n, "count": c} for n, c in top_asanas],
    }
