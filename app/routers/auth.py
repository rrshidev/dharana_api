import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, PendingTelegramAuth
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    require_user,
    get_current_user,
)
from app.services.notify_service import notify_new_user
from app.services.telegram_avatar import fetch_telegram_avatar

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TelegramLoginRequest(BaseModel):
    telegram_id: int
    name: str | None = None
    username: str | None = None


class TelegramCodeRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    email: str | None
    name: str | None
    avatar_url: str | None


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await notify_new_user(user.name or user.email or f"User #{user.id}", "email")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name},
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name},
    )


@router.post("/telegram/create-code")
async def create_telegram_code(body: TelegramLoginRequest, db: AsyncSession = Depends(get_db)):
    """Bot calls this to create a pending auth code. Also upserts the user in DB."""
    code = str(secrets.randbelow(900000) + 100000)

    # Upsert user by telegram_id
    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()
    is_new = False
    if not user:
        user = User(
            telegram_id=body.telegram_id,
            name=body.name,
            username=body.username,
        )
        db.add(user)
        is_new = True
    else:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            user.username = body.username

    # Fetch Telegram avatar if user has no avatar
    if not user.avatar_url:
        avatar_url = await fetch_telegram_avatar(body.telegram_id)
        if avatar_url:
            user.avatar_url = avatar_url

    pending = PendingTelegramAuth(
        code=code,
        telegram_id=body.telegram_id,
        telegram_name=body.name,
        telegram_username=body.username,
    )
    db.add(pending)
    await db.commit()

    if is_new:
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    return {"code": code}


@router.post("/telegram/verify", response_model=TokenResponse)
async def verify_telegram_code(
    body: TelegramCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """App calls this with the code from the bot to complete login."""
    result = await db.execute(
        select(PendingTelegramAuth).where(
            PendingTelegramAuth.code == body.code,
            PendingTelegramAuth.confirmed == False,
        )
    )
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    if datetime.utcnow() - pending.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="Code expired")

    pending.confirmed = True

    if current_user:
        # Logged-in user linking Telegram — merge if bot user exists
        existing_result = await db.execute(
            select(User).where(User.telegram_id == pending.telegram_id, User.id != current_user.id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            # Merge: transfer favorites/practice data from bot user, then delete
            from app.models.models import Favorite, PracticeSession
            await db.execute(
                Favorite.__table__.update().where(Favorite.user_id == existing.id).values(user_id=current_user.id)
            )
            await db.execute(
                PracticeSession.__table__.update().where(PracticeSession.user_id == existing.id).values(user_id=current_user.id)
            )
            # Free up unique fields before current_user takes them (avoid UNIQUE conflicts)
            existing.telegram_id = None
            if pending.telegram_username and pending.telegram_username == existing.username:
                existing.username = None
            await db.flush()
            await db.delete(existing)

        current_user.telegram_id = pending.telegram_id
        if pending.telegram_name and not current_user.name:
            current_user.name = pending.telegram_name
        if pending.telegram_username and not current_user.username:
            current_user.username = pending.telegram_username
        # Fetch Telegram avatar if user has no avatar
        if not current_user.avatar_url:
            avatar_url = await fetch_telegram_avatar(pending.telegram_id)
            if avatar_url:
                current_user.avatar_url = avatar_url
        user = current_user
    else:
        # New login via Telegram — find or create
        user_result = await db.execute(select(User).where(User.telegram_id == pending.telegram_id))
        user = user_result.scalar_one_or_none()

        if user:
            if pending.telegram_name and not user.name:
                user.name = pending.telegram_name
            if pending.telegram_username and not user.username:
                user.username = pending.telegram_username
        else:
            user = User(
                telegram_id=pending.telegram_id,
                name=pending.telegram_name,
                username=pending.telegram_username,
            )
            db.add(user)
            await db.flush()
            await notify_new_user(user.name or f"TG #{pending.telegram_id}", "telegram")

        # Fetch Telegram avatar if user has no avatar
        if not user.avatar_url:
            avatar_url = await fetch_telegram_avatar(pending.telegram_id)
            if avatar_url:
                user.avatar_url = avatar_url

    pending.user_id = user.id
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name, "telegram_id": user.telegram_id},
    )


@router.post("/telegram", response_model=TokenResponse)
async def telegram_login(body: TelegramLoginRequest, db: AsyncSession = Depends(get_db)):
    """Direct telegram login (legacy)."""
    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()

    if user:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            user.username = body.username
        await db.commit()
        await db.refresh(user)
    else:
        user = User(
            telegram_id=body.telegram_id,
            name=body.name,
            username=body.username,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name, "telegram_id": user.telegram_id},
    )


@router.get("/me")
async def get_me(user: User = Depends(require_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "username": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "telegram_id": user.telegram_id,
        "is_admin": user.is_admin,
        "total_practice_minutes": user.total_practice_minutes,
        "total_practice_days": user.total_practice_days,
        "current_streak": user.current_streak,
        "last_practice_at": user.last_practice_at.isoformat() if user.last_practice_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
