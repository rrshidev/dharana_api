"""Google OAuth 2.0 id_token validation.

Fронтенд получает id_token через Google Identity Services (Web) или
google_sign_in (Flutter). Бэкенд валидирует его лёгким запросом к
https://oauth2.googleapis.com/tokeninfo — Google проверяет подпись и срок,
мы проверяем audience (наш Web Client ID).
"""
from typing import Optional
import logging

import httpx

logger = logging.getLogger(__name__)

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def decode_google_id_token(id_token: str, expected_audiences: list[str]) -> Optional[dict]:
    """Валидирует Google id_token через tokeninfo endpoint.

    expected_audiences — список допустимых Client ID (web + android/flutter).
    Возвращает claims (sub, email, name, picture) или None.
    """
    try:
        resp = httpx.get(
            TOKENINFO_URL,
            params={"id_token": id_token},
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning(f"Google tokeninfo HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        claims = resp.json()
    except Exception as e:
        logger.warning(f"Google tokeninfo request failed: {type(e).__name__}: {e}")
        return None

    # tokeninfo возвращает audience клиента, для которого выдан токен.
    actual_audience = claims.get("aud") or claims.get("azp")
    if actual_audience not in expected_audiences:
        logger.warning(f"Google token audience mismatch: {actual_audience}")
        return None

    if not claims.get("sub"):
        return None
    return claims