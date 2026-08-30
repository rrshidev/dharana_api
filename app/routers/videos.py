from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Video, User, UserSubscription
from app.services.auth_service import get_current_user
from app.services.video_service import video_service

router = APIRouter(prefix="/videos", tags=["videos"])


async def _check_premium(user: User, db: AsyncSession) -> bool:
    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    return sub.is_premium if sub else False


@router.get("/asana/{asana_name}")
async def get_asana_video(
    asana_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    video = await video_service.get_asana_video(asana_name, db)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    is_premium = await _check_premium(user, db) if user else False

    # Catalog (asana) videos are premium-only
    accessible = is_premium

    return {
        "id": video.id,
        "asana_name": video.asana_name,
        "is_premium": video.is_premium,
        "accessible": accessible,
        "video_url": f"/api/v1/media/videos/{video.filepath}" if accessible else None,
        "message": "Видео из каталога доступно только по подписке Premium" if not accessible else None,
    }


@router.get("/sequences")
async def list_sequences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    is_premium = await _check_premium(user, db) if user else False
    videos = await video_service.get_sequences(db=db)

    result = []
    for v in videos:
        accessible = not v.is_premium or is_premium
        result.append({
            "id": v.id,
            "name": v.sequence_name,
            "is_premium": v.is_premium,
            "accessible": accessible,
            "video_url": f"/api/v1/media/videos/{v.filepath}" if accessible else None,
        })

    return result


@router.get("/sequences/{video_id}")
async def get_sequence_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video or video.video_type != "sequence":
        raise HTTPException(status_code=404, detail="Sequence not found")

    is_premium = await _check_premium(user, db) if user else False

    if video.is_premium and not is_premium:
        return {
            "id": video.id,
            "name": video.sequence_name,
            "is_premium": True,
            "accessible": False,
            "video_url": None,
            "message": "Требуется подписка Premium",
        }

    return {
        "id": video.id,
        "name": video.sequence_name,
        "is_premium": video.is_premium,
        "accessible": True,
        "video_url": f"/api/v1/media/videos/{video.filepath}",
    }


@router.post("/scan")
async def scan_videos(db: AsyncSession = Depends(get_db)):
    stats = await video_service.scan_and_sync(db)
    return {"ok": True, "stats": stats}
