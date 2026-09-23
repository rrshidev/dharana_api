from datetime import datetime, date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import User, PracticeSession
from app.services.auth_service import require_user
from app.services.asana_service import resolve_lang
from app.services.subscription_service import get_subscription_status, consume_generation
from app.services.sequence_generator import sequence_generator

router = APIRouter(prefix="/practice", tags=["practice"])

# Типы практик: asana (исторический дефолт), meditation, pranayama.
PRACTICE_TYPES = ("asana", "meditation", "pranayama")


def require_timer_key(x_timer_key: Optional[str] = Header(default=None)):
    if not settings.TIMER_BOT_KEY:
        raise HTTPException(status_code=500, detail="TIMER_BOT_KEY not configured")
    if x_timer_key != settings.TIMER_BOT_KEY:
        raise HTTPException(status_code=403, detail="Invalid timer bot key")
    return True

FREE_REPEATABLE_LIMIT = 3

# Лимит бесплатных генераций практики в сутки (общий с ботом).
DAILY_GENERATION_LIMIT = 1


class GenerateSequenceRequest(BaseModel):
    difficulty: str = "beginner"
    duration_minutes: int = 30
    focus: str = "back"
    lang: str = "ru"

# Автоматически завершаем активную сессию, если она «висит» дольше этого срока
# (вкладка закрылась/приложение убито/таймер упал и т.п.) и пользователь хочет
# начать новую практику, не дожидаясь ручного сброса.
SESSION_TTL = timedelta(hours=4)


class StartSessionRequest(BaseModel):
    sequence_id: int | None = None


class CompleteSessionRequest(BaseModel):
    asanas_practiced: list[str] = []
    asana_durations: dict[str, int] = {}
    rest_seconds: int = 15


class TimerPracticeRecord(BaseModel):
    """Запись завершённой практики из таймер-бота (@timerasana_bot).

    Таймер-бот завершает практику по факту и сообщает итог целиком.
    """
    telegram_id: int
    practice_type: str = "asana"  # asana, meditation, pranayama
    total_duration_seconds: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


@router.delete("/{session_id}")
async def cancel_session(
    session_id: int,
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

    session.status = "cancelled"
    session.completed_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "id": session_id, "status": "cancelled"}


@router.get("/active")
async def active_session(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PracticeSession).where(
            PracticeSession.user_id == user.id,
            PracticeSession.status == "active",
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        return {"active": False}
    return {
        "active": True,
        "id": session.id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "asanas_practiced": session.asanas_practiced or [],
    }


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
        now = datetime.utcnow()
        started = active.started_at or now
        if now - started < SESSION_TTL:
            raise HTTPException(status_code=400, detail="Active session already exists")
        # Сессия «висит» дольше TTL — авто-завершаем и даём начать новую.
        active.status = "cancelled"
        active.completed_at = now

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


@router.post("/timer")
async def record_timer_practice(
    body: TimerPracticeRecord,
    _: bool = Depends(require_timer_key),
    db: AsyncSession = Depends(get_db),
):
    """Запись завершённой практики от таймер-бота.

    Бот сам знает telegram_id пользователя; API не требует JWT —
    аутентификация по X-Timer-Key.
    """
    if body.practice_type not in PRACTICE_TYPES:
        raise HTTPException(status_code=422, detail="INVALID_PRACTICE_TYPE")

    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    started = body.started_at or datetime.utcnow()
    completed = body.completed_at or datetime.utcnow()
    duration = max(body.total_duration_seconds, 0)

    session = PracticeSession(
        user_id=user.id,
        practice_type=body.practice_type,
        status="completed",
        total_duration_seconds=duration,
        started_at=started,
        completed_at=completed,
    )
    db.add(session)

    user.total_practice_minutes += duration // 60
    user.last_practice_at = datetime.utcnow()

    res = await db.execute(
        select(PracticeSession).where(
            PracticeSession.user_id == user.id,
            PracticeSession.status == "completed",
        )
    )
    unique_days = set()
    for s in res.scalars().all():
        if s.started_at:
            unique_days.add(s.started_at.date().isoformat())
    user.total_practice_days = len(unique_days)

    await db.commit()
    return {
        "ok": True,
        "practice_type": body.practice_type,
        "total_duration_seconds": duration,
    }


@router.post("/generate")
async def generate_sequence(
    body: GenerateSequenceRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if body.difficulty not in ("beginner", "intermediate", "advanced"):
        raise HTTPException(status_code=422, detail="INVALID_DIFFICULTY")
    if body.duration_minutes not in (15, 30, 60):
        raise HTTPException(status_code=422, detail="INVALID_DURATION")
    if body.focus not in ("back", "legs", "balance", "flexibility", "energy"):
        raise HTTPException(status_code=422, detail="INVALID_FOCUS")

    allowed = await consume_generation(db, user.id)
    if not allowed:
        status = await get_subscription_status(db, user.id)
        raise HTTPException(
            status_code=403,
            detail=status,
        )

    sequence = sequence_generator.generate_sequence(
        difficulty=body.difficulty,
        duration_minutes=body.duration_minutes,
        focus=body.focus,
        lang=resolve_lang(body.lang),
    )
    status = await get_subscription_status(db, user.id)
    return {
        **sequence,
        "is_premium": status["is_premium"],
        "can_generate": status["can_generate"],
        "daily_generations_used": status["daily_generations_used"],
        "daily_generation_limit": None if status["is_premium"] else DAILY_GENERATION_LIMIT,
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
                "practice_type": s.practice_type,
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
    total_asanas = sum(len(s.asanas_practiced) for s in sessions if s.practice_type == "asana")

    by_type = {t: {"minutes": 0, "sessions": 0} for t in PRACTICE_TYPES}
    for s in sessions:
        if s.practice_type in by_type:
            by_type[s.practice_type]["minutes"] += s.total_duration_seconds // 60
            by_type[s.practice_type]["sessions"] += 1

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
        if s.practice_type != "asana":
            continue
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
        "by_type": by_type,
    }


@router.get("/stats/series")
async def practice_stats_series(
    days: int = 30,
    practice_type: str = "all",
    tz_offset_minutes: int = 0,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Дневные ряды активности для профильного графика.

    Единый источник агрегации по дням (минуты/сессии/асаны) — клиенты
    (Flutter и Web) больше не дублируют это и не расходятся по таймзонам.
    `tz_offset_minutes` — смещение клиента от UTC, чтобы «день» считался
    в локальной таймзоне пользователя (Web: -getTimezoneOffset(), Flutter:
    DateTime.now().timeZoneOffset.inMinutes).
    `practice_type` — all|asana|meditation|pranayama (вкладки графика).
    """
    days = max(min(days, 90), 1)
    if practice_type not in ("all", *PRACTICE_TYPES):
        raise HTTPException(status_code=422, detail="INVALID_PRACTICE_TYPE")

    start_date = date.today() - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())

    stmt = select(PracticeSession).where(
        PracticeSession.user_id == user.id,
        PracticeSession.status == "completed",
        PracticeSession.started_at >= start_dt,
        PracticeSession.started_at < end_dt,
    )
    if practice_type != "all":
        stmt = stmt.where(PracticeSession.practice_type == practice_type)
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    shift = timedelta(minutes=tz_offset_minutes)
    counts = {}
    for s in sessions:
        shifted = s.started_at + shift
        if isinstance(shifted, datetime):
            shifted = shifted.date()
        key = shifted.isoformat()
        row = counts.setdefault(key, {"minutes": 0, "sessions": 0, "asanas": 0})
        row["minutes"] += s.total_duration_seconds // 60
        row["sessions"] += 1
        if s.practice_type == "asana":
            row["asanas"] += len(s.asanas_practiced or [])

    days_out = []
    minutes = []
    sessions_out = []
    asanas = []
    for i in range(days):
        day_dt = datetime.combine(start_date + timedelta(days=i), datetime.min.time()) + shift
        if isinstance(day_dt, datetime):
            day_dt = day_dt.date()
        day = day_dt.isoformat()
        row = counts.get(day, {"minutes": 0, "sessions": 0, "asanas": 0})
        days_out.append(day)
        minutes.append(row["minutes"])
        sessions_out.append(row["sessions"])
        asanas.append(row["asanas"])

    return {
        "days": days_out,
        "minutes": minutes,
        "sessions": sessions_out,
        "asanas": asanas,
    }
