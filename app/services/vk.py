"""VK ID (https://id.vk.ru) — обмен authorization code на access_token.

Отдельного OAuth у MAX не существует: личный кабинет MAX авторизует через тот же
VK ID, поэтому обе кнопки (VK и MAX) ходят в один и тот же флоу, а пользователь
для нас один и тот же.

Бэкенд сам меняет код на токен (client_secret не покидает сервер), затем берёт
профиль через users.get: сам access_token — доказательство аутентификации, а
user_id из ответа сверяем с тем, что вернул токен-эндпоинт.
"""
from typing import Optional
import logging

import httpx

logger = logging.getLogger(__name__)

# VK ID (https://id.vk.ru): страница входа - /authorize, обмен кода на токен -
# oauth.vk.ru/access_token. Путь /oauth2/authorize и /oauth2/token у VK-ID
# больше не существует (404), проверено 2026-10-04.
TOKEN_URL = "https://oauth.vk.ru/access_token"
USERS_GET_URL = "https://api.vk.ru/method/users.get"
API_VERSION = "5.199"
USER_FIELDS = "screen_name,photo_200"


def exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Optional[dict]:
    """Меняет authorization code на {access_token, user_id, email?}. None при ошибке."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10.0,
        )
    except Exception as e:
        logger.warning(f"VK token request failed: {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"VK token HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("VK token response is not JSON")
        return None

    if not data.get("access_token"):
        logger.warning(f"VK token response without access_token: {str(data)[:200]}")
        return None
    return data


def fetch_profile(access_token: str, user_id: Optional[str] = None) -> Optional[dict]:
    """Профиль пользователя по access_token: {id, first_name, last_name, screen_name, photo_200}."""
    params = {
        "access_token": access_token,
        "v": API_VERSION,
        "fields": USER_FIELDS,
    }
    if user_id:
        params["user_ids"] = user_id

    try:
        resp = httpx.get(USERS_GET_URL, params=params, timeout=10.0)
        if resp.status_code != 200:
            logger.warning(f"VK users.get HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        items = (resp.json() or {}).get("response") or []
    except Exception as e:
        logger.warning(f"VK users.get failed: {type(e).__name__}: {e}")
        return None

    if not items:
        logger.warning("VK users.get returned empty response")
        return None
    return items[0]


def login_with_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Optional[dict]:
    """Полный флоу: code → access_token → профиль.

    Возвращает {id, email, name, avatar_url} или None на любой неудаче.
    """
    token_data = exchange_code(code, redirect_uri, client_id, client_secret)
    if token_data is None:
        return None

    access_token = token_data["access_token"]
    profile = fetch_profile(access_token, str(token_data.get("user_id") or "") or None)
    if profile is None or not profile.get("id"):
        return None

    # user_id из токена и из профиля должны совпадать — иначе токен не наш.
    token_user_id = str(token_data.get("user_id") or "")
    if token_user_id and token_user_id != str(profile.get("id")):
        logger.warning("VK user_id mismatch between token and profile")
        return None

    # email приходит из токен-ответа (scope email), в users.get его нет.
    email = (token_data.get("email") or "").strip().casefold() or None

    name = " ".join(
        part for part in [profile.get("first_name"), profile.get("last_name")] if part
    ).strip()

    return {
        "id": str(profile["id"]),
        "email": email,
        "name": name or profile.get("screen_name") or None,
        "avatar_url": profile.get("photo_200") or None,
    }