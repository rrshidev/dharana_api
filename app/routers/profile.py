from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import os
import uuid

from app.database import get_db
from app.models.models import User, UserAvatar
from app.services.auth_service import require_user

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    username: str | None = None
    bio: str | None = None


class AvatarResponse(BaseModel):
    id: int
    url: str
    is_primary: bool

    class Config:
        from_attributes = True


@router.get("")
async def get_profile(user: User = Depends(require_user)):
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
        "longest_streak": user.longest_streak,
        "last_practice_at": user.last_practice_at.isoformat() if user.last_practice_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.patch("")
async def update_profile(
    body: ProfileUpdateRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if body.name is not None:
        user.name = body.name
    if body.username is not None:
        if body.username != user.username:
            result = await db.execute(
                select(User).where(User.username == body.username)
            )
            if result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Username already taken")
        user.username = body.username
    if body.bio is not None:
        user.bio = body.bio

    await db.commit()
    await db.refresh(user)
    return {"ok": True, "message": "Profile updated"}


@router.get("/avatars")
async def get_avatars(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UserAvatar).where(UserAvatar.user_id == user.id)
    )
    avatars = result.scalars().all()
    return [
        {"id": a.id, "url": a.url, "is_primary": a.is_primary}
        for a in avatars
    ]


@router.post("/avatars")
async def add_avatar(
    url: str,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAvatar).where(UserAvatar.user_id == user.id)
    )
    avatars = result.scalars().all()

    if len(avatars) >= 5:
        raise HTTPException(status_code=400, detail="Maximum 5 avatars allowed")

    avatar = UserAvatar(user_id=user.id, url=url, is_primary=len(avatars) == 0)
    db.add(avatar)
    await db.commit()
    await db.refresh(avatar)
    return {"id": avatar.id, "url": avatar.url, "is_primary": avatar.is_primary}


@router.delete("/avatars/{avatar_id}")
async def delete_avatar(
    avatar_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAvatar).where(UserAvatar.id == avatar_id, UserAvatar.user_id == user.id)
    )
    avatar = result.scalar_one_or_none()
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found")

    was_primary = avatar.is_primary
    await db.delete(avatar)

    if was_primary:
        result = await db.execute(
            select(UserAvatar).where(UserAvatar.user_id == user.id)
        )
        first = result.scalars().first()
        if first:
            first.is_primary = True
            db.add(first)

    await db.commit()
    return {"ok": True}


@router.put("/avatars/{avatar_id}/primary")
async def set_primary_avatar(
    avatar_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAvatar).where(UserAvatar.user_id == user.id)
    )
    avatars = result.scalars().all()

    for a in avatars:
        a.is_primary = (a.id == avatar_id)
        db.add(a)

    await db.commit()
    return {"ok": True}


UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "avatars")


@router.post("/avatars/upload")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAvatar).where(UserAvatar.user_id == user.id)
    )
    avatars = result.scalars().all()
    if len(avatars) >= 5:
        raise HTTPException(status_code=400, detail="Maximum 5 avatars allowed")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "avatar.jpg")[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    url = f"/uploads/avatars/{filename}"
    avatar = UserAvatar(user_id=user.id, url=url, is_primary=len(avatars) == 0)
    db.add(avatar)
    await db.commit()
    await db.refresh(avatar)

    user.avatar_url = url
    await db.commit()

    return {"id": avatar.id, "url": url, "is_primary": avatar.is_primary}
