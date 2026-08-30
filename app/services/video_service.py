import os
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import Video

logger = logging.getLogger(__name__)

MEDIA_DIR = "/opt/dharana/media"
CATALOG_DIR = os.path.join(MEDIA_DIR, "catalog")
SEQUENCES_FREE_DIR = os.path.join(MEDIA_DIR, "sequences", "free")
SEQUENCES_PREMIUM_DIR = os.path.join(MEDIA_DIR, "sequences", "premium")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class VideoService:
    def __init__(self):
        self.media_dir = MEDIA_DIR
        self.catalog_dir = CATALOG_DIR
        self.sequences_free_dir = SEQUENCES_FREE_DIR
        self.sequences_premium_dir = SEQUENCES_PREMIUM_DIR

    async def scan_and_sync(self, db: AsyncSession) -> dict:
        """Scan media folders and sync with database. Returns stats."""
        stats = {"catalog": 0, "sequences_free": 0, "sequences_premium": 0, "removed": 0}

        existing = await db.execute(select(Video))
        known_files = {v.filepath: v for v in existing.scalars().all()}
        seen_files = set()

        # Scan catalog videos (asana videos)
        if os.path.exists(self.catalog_dir):
            for f in os.listdir(self.catalog_dir):
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    filepath = f"catalog/{f}"
                    seen_files.add(filepath)
                    if filepath not in known_files:
                        asana_name = os.path.splitext(f)[0]
                        video = Video(
                            filename=f,
                            filepath=filepath,
                            video_type="asana",
                            is_premium=False,
                            asana_name=asana_name,
                        )
                        db.add(video)
                        stats["catalog"] += 1
                        logger.info(f"New catalog video: {f} -> asana '{asana_name}'")

        # Scan free sequences
        if os.path.exists(self.sequences_free_dir):
            for f in os.listdir(self.sequences_free_dir):
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    filepath = f"sequences/free/{f}"
                    seen_files.add(filepath)
                    if filepath not in known_files:
                        sequence_name = os.path.splitext(f)[0].replace("_", " ").title()
                        video = Video(
                            filename=f,
                            filepath=filepath,
                            video_type="sequence",
                            is_premium=False,
                            sequence_name=sequence_name,
                        )
                        db.add(video)
                        stats["sequences_free"] += 1
                        logger.info(f"New free sequence: {f} -> '{sequence_name}'")

        # Scan premium sequences
        if os.path.exists(self.sequences_premium_dir):
            for f in os.listdir(self.sequences_premium_dir):
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    filepath = f"sequences/premium/{f}"
                    seen_files.add(filepath)
                    if filepath not in known_files:
                        sequence_name = os.path.splitext(f)[0].replace("_", " ").title()
                        video = Video(
                            filename=f,
                            filepath=filepath,
                            video_type="sequence",
                            is_premium=True,
                            sequence_name=sequence_name,
                        )
                        db.add(video)
                        stats["sequences_premium"] += 1
                        logger.info(f"New premium sequence: {f} -> '{sequence_name}'")

        # Remove deleted files from DB
        for filepath, video in known_files.items():
            if filepath not in seen_files:
                await db.delete(video)
                stats["removed"] += 1
                logger.info(f"Removed stale video: {filepath}")

        await db.commit()
        return stats

    def get_video_path(self, filepath: str) -> Optional[str]:
        """Return full filesystem path for a video filepath."""
        full_path = os.path.join(self.media_dir, filepath)
        if os.path.exists(full_path):
            return full_path
        return None

    async def get_asana_video(self, asana_name: str, db: AsyncSession) -> Optional[Video]:
        """Get video for a specific asana."""
        result = await db.execute(
            select(Video).where(
                Video.video_type == "asana",
                Video.asana_name == asana_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_sequences(
        self, is_premium: Optional[bool] = None, db: AsyncSession = None
    ) -> List[Video]:
        """Get all sequence videos, optionally filtered by premium status."""
        query = select(Video).where(Video.video_type == "sequence")
        if is_premium is not None:
            query = query.where(Video.is_premium == is_premium)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_all_videos(self, db: AsyncSession) -> List[Video]:
        """Get all videos."""
        result = await db.execute(select(Video))
        return list(result.scalars().all())


video_service = VideoService()
