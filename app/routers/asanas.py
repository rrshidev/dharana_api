from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import DailyAsanaLog, User
from app.services.asana_service import (
    asana_service,
    parse_timezone_offset,
    resolve_lang,
)
from app.services.auth_service import get_optional_user

router = APIRouter(prefix="/asanas", tags=["asanas"])


@router.get("")
async def list_asanas(
    difficulty: Optional[int] = Query(None, ge=1, le=5),
    effect: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return asana_service.get_all_asanas(
        difficulty=difficulty,
        effect=effect,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/random")
async def random_asana(
    lang: Optional[str] = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    asana = asana_service.get_random_asana(resolve_lang(lang, accept_language))
    if asana is None:
        return {"error": "No asanas found"}
    return asana


@router.get("/day")
async def day_asana(
    lang: Optional[str] = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """«Асана дня» для home-экрана и overview.

    У бота-пользователя со свежей рассылкой — та асана, что пришла в логе
    (локальная дата по user.timezone); иначе — детерминированная асана суток.
    JWT опционален и толерантен: протухший токен = аноним, не ошибка.
    """
    resolved = resolve_lang(lang, accept_language)

    if user is not None and user.telegram_id is not None:
        result = await db.execute(
            select(DailyAsanaLog).where(DailyAsanaLog.telegram_id == user.telegram_id)
        )
        log = result.scalar_one_or_none()
        if log is not None and log.sent_at is not None:
            offset = parse_timezone_offset(user.timezone)
            now_local = datetime.utcnow() + offset
            sent_local = log.sent_at + offset
            if sent_local.date() == now_local.date():
                detail = asana_service.get_asana_detail(log.asana_name, resolved)
                if detail is not None:
                    return detail
                # каталог изменился (имя исчезло) → детерминированная ниже

    detail = asana_service.get_daily_asana(resolved)
    if detail is None:
        return {"error": "No asanas found"}
    return detail


@router.get("/{asana_name}")
async def get_asana(
    asana_name: str,
    lang: Optional[str] = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    asana = asana_service.get_asana_detail(asana_name, resolve_lang(lang, accept_language))
    if asana is None:
        return {"error": "Asana not found"}
    return asana
