"""Проверка подписи стартовых данных мини-приложения MAX (WebAppData).

Алгоритм из официальной документации:
https://dev.max.ru/docs/webapps/validation/

1. Разбить строку по ``&`` на пары key=value.
2. Запомнить ``hash`` и исключить его из дальнейшей обработки.
3. URL-декодировать значения.
4. Отсортировать по ключам (a→z).
5. Собрать ``key1=value1\\nkey2=value2``.
6. secret_key = HMAC-SHA256(key="WebAppData", msg=BOT_TOKEN).
7. Подпись = hex(HMAC-SHA256(key=secret_key, msg=launch_params)).
8. Сравнить с ``hash`` (constant-time).

Подпись подтверждает, что данные выдал MAX именно нашему боту: подделать её
без токена бота нельзя.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import unquote

# Свежесть initData: мини-приложение получает его при запуске, а подтвердить
# вход должно успеть за время жизни сессии (по умолчанию 10 минут).
DEFAULT_MAX_AGE_SECONDS = 900


class MaxWebAppError(ValueError):
    """Подпись неверна, данные повреждены или устарели."""


def _parse_pairs(init_data: str) -> tuple[list[tuple[str, str]], str]:
    pairs: list[tuple[str, str]] = []
    signature: str | None = None

    for chunk in init_data.split("&"):
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        if not sep or not key:
            raise MaxWebAppError("malformed pair")
        if key == "hash":
            if signature is not None:
                raise MaxWebAppError("duplicate hash")
            signature = unquote(value)
            continue
        pairs.append((key, unquote(value)))

    if signature is None:
        raise MaxWebAppError("missing hash")
    return pairs, signature


def validate_webapp_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int | None = DEFAULT_MAX_AGE_SECONDS,
) -> dict:
    """Проверить подпись и вернуть разобранные поля initData.

    ``user`` остаётся строкой — вызывающий код сам решает, парсить ли JSON.
    Бросает :class:`MaxWebAppError` при любой проблеме.
    """
    if not init_data or not bot_token:
        raise MaxWebAppError("empty data or token")

    # Страховка на случай, если клиент прислал initData целиком URL-кодированным
    # (значение WebAppData из фрагмента URL): тогда внутри нет ни одного «&».
    if "&" not in init_data and "%" in init_data:
        init_data = unquote(init_data)

    pairs, signature = _parse_pairs(init_data)
    if not pairs:
        raise MaxWebAppError("no parameters")

    launch_params = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs, key=lambda item: item[0])
    )

    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected = hmac.new(
        secret_key, launch_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise MaxWebAppError("signature mismatch")

    data = dict(pairs)

    auth_date = data.get("auth_date")
    if max_age_seconds is not None and auth_date:
        try:
            issued_at = int(auth_date)
        except (TypeError, ValueError):
            raise MaxWebAppError("bad auth_date")
        if abs(int(time.time()) - issued_at) > max_age_seconds:
            raise MaxWebAppError("stale initData")

    return data


def parse_user(data: dict) -> dict | None:
    """Вернуть объект пользователя из initData (или None, если его нет)."""
    raw = data.get("user")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
