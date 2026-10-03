"""Яндекс OAuth (https://oauth.yandex.ru) — вход по Яндекс ID.

Классический authorization code flow: код меняем на access_token, профиль берём
с https://login.yandex.ru/info. Проверять подпись JWT не нужно — access_token
выдан Яндексом именно нашему client_id, а ответ /info приходит только по нему.
"""
from typing import Optional
import logging

import httpx

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info"
SCOPE = "login:email login:info"


def exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Optional[str]:
    """Меняет authorization code на access_token. None при ошибке."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=10.0,
        )
    except Exception as e:
        logger.warning(f"Yandex token request failed: {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"Yandex token HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("Yandex token response is not JSON")
        return None

    access_token = data.get("access_token")
    if not access_token:
        logger.warning(f"Yandex token response without access_token: {str(data)[:200]}")
        return None
    return access_token


def fetch_profile(access_token: str) -> Optional[dict]:
    """Профиль по access_token: {id, login, display_name, real_name, default_email}."""
    try:
        resp = httpx.get(
            INFO_URL,
            params={"format": "json"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    except Exception as e:
        logger.warning(f"Yandex info request failed: {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"Yandex info HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("Yandex info response is not JSON")
        return None

    if not data.get("id"):
        logger.warning("Yandex info without id")
        return None
    return data


def login_with_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Optional[dict]:
    """Полный флоу: code → access_token → профиль.

    Возвращает {id, email, name, avatar_url} или None на любой неудаче.
    """
    access_token = exchange_code(code, redirect_uri, client_id, client_secret)
    if access_token is None:
        return None

    info = fetch_profile(access_token)
    if info is None:
        return None

    email = (info.get("default_email") or "").strip().casefold() or None
    name = (info.get("real_name") or info.get("display_name") or info.get("login") or "").strip()

    return {
        "id": str(info["id"]),
        "email": email,
        "name": name or None,
        # Аватар Яндекса собирается из внутреннего идентификатора; url не строим,
        # лучше initials из имени, чем битая ссылка.
        "avatar_url": None,
    }