from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User, Favorite
from app.services.auth_service import require_user

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.get("")
async def list_favorites(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Favorite)
        .where(Favorite.user_id == user.id)
        .order_by(Favorite.created_at.desc())
    )
    favorites = result.scalars().all()
    return [{"id": f.id, "asana_name": f.asana_name, "created_at": f.created_at.isoformat()} for f in favorites]


@router.get("/check/{asana_name}")
async def check_favorite(
    asana_name: str,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.asana_name == asana_name)
    )
    fav = result.scalar_one_or_none()
    return {"is_favorite": fav is not None, "id": fav.id if fav else None}


@router.post("/{asana_name}")
async def add_favorite(
    asana_name: str,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.asana_name == asana_name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already in favorites")

    fav = Favorite(user_id=user.id, asana_name=asana_name)
    db.add(fav)
    await db.commit()
    await db.refresh(fav)
    return {"id": fav.id, "asana_name": fav.asana_name, "created_at": fav.created_at.isoformat()}


@router.delete("/{asana_name}")
async def remove_favorite(
    asana_name: str,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.asana_name == asana_name)
    )
    fav = result.scalar_one_or_none()
    if not fav:
        raise HTTPException(status_code=404, detail="Not in favorites")

    await db.delete(fav)
    await db.commit()
    return {"ok": True}
