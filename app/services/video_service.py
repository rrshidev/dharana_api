import os
import re
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.models import Video

logger = logging.getLogger(__name__)

MEDIA_DIR = settings.MEDIA_DIR or "/opt/dharana/media"
CATALOG_DIR = os.path.join(MEDIA_DIR, "catalog")
SEQUENCES_FREE_DIR = os.path.join(MEDIA_DIR, "sequences", "free")
SEQUENCES_PREMIUM_DIR = os.path.join(MEDIA_DIR, "sequences", "premium")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class DuplicateSequenceError(Exception):
    """Raised when a sequence video with the same name already exists."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


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

    @staticmethod
    def _safe_filename(name: str, ext: str) -> str:
        """Build a filesystem-safe filename from the sequence name."""
        base = re.sub(r"[^\w\-\u0400-\u04FF]+", "_", name, flags=re.UNICODE).strip("_")
        if not base:
            base = "video"
        return base + ext

    async def add_sequence_video(
        self,
        db: AsyncSession,
        *,
        filename: str,
        content: bytes,
        name: str,
        section: str,
    ) -> Video:
        """Save an uploaded sequence video to the shared catalog and DB.

        Note: callers should wrap a duplicate-name exception with a 409.
        Raises ValueError on duplicate name and OSError on write failure.
        """
        if section not in ("free", "premium"):
            raise ValueError("section must be 'free' or 'premium'")

        ext = os.path.splitext(filename)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            raise ValueError(
                f"Unsupported video format '{ext}'. Allowed: {', '.join(sorted(VIDEO_EXTENSIONS))}"
            )

        is_premium = section == "premium"
        target_dir = self.sequences_premium_dir if is_premium else self.sequences_free_dir

        # Store the file under a name derived from the sequence name, so the
        # on-disk filename (and thus the ready-sequences menu / file service)
        # matches the human-facing sequence name.
        safe_file = self._safe_filename(name, ext)
        filepath_rel = f"sequences/{section}/{safe_file}"

        # Uniqueness: same name (case-insensitive) in the same section is not allowed.
        existing = await db.execute(
            select(Video).where(
                Video.video_type == "sequence",
                Video.is_premium == is_premium,
                Video.sequence_name == name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateSequenceError(name)

        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, safe_file)
        with open(target_path, "wb") as f:
            f.write(content)

        video = Video(
            filename=safe_file,
            filepath=filepath_rel,
            video_type="sequence",
            is_premium=is_premium,
            sequence_name=name,
        )
        db.add(video)
        await db.commit()
        await db.refresh(video)
        logger.info(f"Added sequence video '{name}' -> {filepath_rel}")
        return video

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

    def _absolute_path(self, filepath: str) -> str:
        """Map a stored filepath (relative to MEDIA_DIR) to the filesystem."""
        return os.path.join(self.media_dir, filepath)

    async def add_asana_video(
        self,
        db: AsyncSession,
        *,
        name: str,
        filename: str,
        content: bytes,
    ) -> Video:
        """Save an asana catalog video. Replaces an existing video for the asana."""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            raise ValueError(
                f"Unsupported video format '{ext}'. Allowed: {', '.join(sorted(VIDEO_EXTENSIONS))}"
            )

        safe_file = self._safe_filename(name, ext)
        filepath_rel = f"catalog/{safe_file}"
        target_path = os.path.join(self.catalog_dir, safe_file)

        # Replace any previous video for this asana (file + row).
        existing = await db.execute(
            select(Video).where(
                Video.video_type == "asana",
                Video.asana_name == name,
            )
        )
        old = existing.scalar_one_or_none()
        if old is not None:
            old_path = self._absolute_path(old.filepath)
            if os.path.exists(old_path):
                os.remove(old_path)
            await db.delete(old)

        os.makedirs(self.catalog_dir, exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(content)

        video = Video(
            filename=safe_file,
            filepath=filepath_rel,
            video_type="asana",
            is_premium=False,
            asana_name=name,
        )
        db.add(video)
        await db.commit()
        await db.refresh(video)
        logger.info(f"Added asana video for '{name}' -> {filepath_rel}")
        return video

    async def update_sequence_video(
        self,
        db: AsyncSession,
        *,
        video_id: int,
        name: str,
        section: str,
    ) -> Video:
        """Rename and/or move an existing sequence video between free/premium."""
        if section not in ("free", "premium"):
            raise ValueError("section must be 'free' or 'premium'")

        result = await db.execute(
            select(Video).where(Video.id == video_id, Video.video_type == "sequence")
        )
        video = result.scalar_one_or_none()
        if video is None:
            raise ValueError("Sequence video not found")

        clean_name = (name or "").strip().title()
        if not clean_name:
            raise ValueError("name is required")

        ext = os.path.splitext(video.filename)[1].lower() or ".mp4"
        is_premium = section == "premium"
        target_dir = self.sequences_premium_dir if is_premium else self.sequences_free_dir
        safe_file = self._safe_filename(clean_name, ext)
        filepath_rel = f"sequences/{section}/{safe_file}"

        # Uniqueness: same name (case-insensitive) in the same section is not allowed.
        existing = await db.execute(
            select(Video).where(
                Video.video_type == "sequence",
                Video.is_premium == is_premium,
                Video.sequence_name == clean_name,
                Video.id != video_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateSequenceError(clean_name)

        # Move the physical file if path changed.
        old_path = self._absolute_path(video.filepath)
        new_path = os.path.join(target_dir, safe_file)
        if old_path != new_path and os.path.exists(old_path):
            os.makedirs(target_dir, exist_ok=True)
            os.replace(old_path, new_path)

        video.filename = safe_file
        video.filepath = filepath_rel
        video.sequence_name = clean_name
        video.is_premium = is_premium
        await db.commit()
        await db.refresh(video)
        logger.info(f"Updated sequence video #{video.id} -> '{clean_name}' ({section})")
        return video

    async def delete_video(self, db: AsyncSession, video_id: int) -> bool:
        """Delete a video row and its file from disk. Returns True if deleted."""
        result = await db.execute(select(Video).where(Video.id == video_id))
        video = result.scalar_one_or_none()
        if video is None:
            return False
        abs_path = self._absolute_path(video.filepath)
        if os.path.exists(abs_path):
            os.remove(abs_path)
        await db.delete(video)
        await db.commit()
        logger.info(f"Deleted video #{video.id} ({video.filepath})")
        return True


video_service = VideoService()
