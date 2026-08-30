import logging
import os
import httpx

logger = logging.getLogger(__name__)

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


async def notify_admin(message: str):
    """Send notification to admin via Telegram."""
    if not ADMIN_TELEGRAM_ID or not BOT_TOKEN:
        logger.debug(f"Admin notification (not sent - no config): {message}")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_TELEGRAM_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error(f"Failed to send admin notification: {resp.text}")
    except Exception as e:
        logger.error(f"Admin notification error: {e}")


async def notify_new_user(name: str, source: str = "app"):
    await notify_admin(f"🆕 <b>Новый пользователь</b>\nИмя: {name}\nИсточник: {source}")


async def notify_subscription(user_name: str, sub_type: str):
    await notify_admin(
        f"💎 <b>Новая подписка</b>\nПользователь: {user_name}\nТип: {sub_type}"
    )


async def notify_error(service: str, error: str):
    await notify_admin(f"🚨 <b>Ошибка {service}</b>\n{error[:500]}")
