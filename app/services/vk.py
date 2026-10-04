"""VK ID (https://id.vk.ru) — вход по access_token официального виджета OneTap.

Схема (проверена пробами с VPS 2026-10-04, см. plan.md сессия 31):
  * страница входа — GET /authorize (путь /oauth2/authorize больше не существует, 404);
  * обмен authorization code → access_token делает **браузер** через
    `VKID.Auth.exchangeCode(code, device_id)` — POST на /oauth2/auth, и
    `device_id` выдаёт только их SDK-виджет (наш редирект-флоу без него
    невозможен: /oauth2/auth отвечает "device id is missing");
  * classical `oauth.vk.ru/access_token` коды VK ID не понимает —
    `invalid_grant: Code is invalid or expired`.

Поэтому клиент (веб-виджет) отдаёт нам access_token, а мы проверяем его
сервером через /oauth2/user_info — это и есть доказательство аутентификации.
client_secret в этой схеме не участвует.

Отдельного OAuth у MAX не существует: вход MAX авторизует через тот же VK ID,
однако официальный виджет — VK-брендовый, отдельную кнопку MAX без
собственного приложения делать не на чем.
"""
from typing import Optional
import logging

import httpx

logger = logging.getLogger(__name__)

USER_INFO_URL = "https://id.vk.ru/oauth2/user_info"


def fetch_profile(access_token: str, client_id: str) -> Optional[dict]:
    """Профиль по access_token VK ID: {id, email, name, avatar_url}. None при ошибке.

    Ответ VK ID — OIDC-подобный (`sub`, `name`/`given_name`, `picture`, `email`),
    поэтому разбираем с запасными именами полей.
    """
    try:
        resp = httpx.post(
            USER_INFO_URL,
            params={"client_id": client_id},
            data={"access_token": access_token},
            timeout=10.0,
        )
    except Exception as e:
        logger.warning(f"VK user_info request failed: {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"VK user_info HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("VK user_info response is not JSON")
        return None

    if not isinstance(data, dict) or data.get("error"):
        logger.warning(f"VK user_info error response: {str(data)[:200]}")
        return None

    provider_id = str(data.get("sub") or data.get("id") or data.get("user_id") or "").strip()
    if not provider_id:
        logger.warning(f"VK user_info without subject: {str(data)[:200]}")
        return None

    name = (data.get("name") or "").strip()
    if not name:
        parts = [
            (data.get("given_name") or data.get("first_name") or "").strip(),
            (data.get("family_name") or data.get("last_name") or "").strip(),
        ]
        name = " ".join(p for p in parts if p).strip()

    email = str(data.get("email") or "").strip().casefold() or None
    avatar_url = (
        data.get("picture")
        or data.get("avatar")
        or data.get("photo_200")
        or data.get("default_avatar")
        or None
    )

    return {
        "id": provider_id,
        "email": email,
        "name": name or None,
        "avatar_url": avatar_url,
    }


def login_with_access_token(access_token: str, client_id: str) -> Optional[dict]:
    """access_token (из виджета) → профиль. None на любой неудаче."""
    if not access_token:
        return None
    return fetch_profile(access_token, client_id)