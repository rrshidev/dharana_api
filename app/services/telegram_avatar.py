import os
import uuid
import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "avatars")


async def fetch_telegram_avatar(telegram_id: int) -> str | None:
    """Fetch the largest profile photo from Telegram and save it locally. Returns the URL path or None."""
    if not settings.BOT_TOKEN:
        return None

    try:
        async with httpx.AsyncClient() as client:
            # Get profile photos
            resp = await client.get(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/getUserProfilePhotos",
                params={"user_id": telegram_id, "limit": 1},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            photos = data.get("result", {}).get("photos", [])
            if not photos:
                return None

            # Get the largest photo (last in the array)
            largest = photos[0][-1]
            file_id = largest["file_id"]

            # Get file path
            file_resp = await client.get(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/getFile",
                params={"file_id": file_id},
                timeout=10,
            )
            if file_resp.status_code != 200:
                return None

            file_path = file_resp.json().get("result", {}).get("file_path")
            if not file_path:
                return None

            # Download the file
            download_resp = await client.get(
                f"https://api.telegram.org/file/bot{settings.BOT_TOKEN}/{file_path}",
                timeout=10,
            )
            if download_resp.status_code != 200:
                return None

            # Save locally
            os.makedirs(AVATAR_DIR, exist_ok=True)
            ext = os.path.splitext(file_path)[1] or ".jpg"
            filename = f"tg_{telegram_id}_{uuid.uuid4().hex[:8]}{ext}"
            filepath = os.path.join(AVATAR_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(download_resp.content)

            return f"/uploads/avatars/{filename}"

    except Exception as e:
        logger.error(f"Failed to fetch Telegram avatar for {telegram_id}: {e}")
        return None
