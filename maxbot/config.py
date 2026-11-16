"""Настройки MAX-бота из переменных окружения.

Пустые MAX_BOT_TOKEN/MAX_BOT_NAME означают, что бот не сконфигурирован:
процесс завершится с понятной ошибкой на старте, а не будет молча висеть.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_name: str
    bot_key: str
    api_url: str
    api_prefix: str
    webhook_url: str
    webhook_secret: str
    webhook_path: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.getenv("MAX_BOT_TOKEN", "").strip(),
            bot_name=os.getenv("MAX_BOT_NAME", "").strip(),
            bot_key=os.getenv("MAX_BOT_KEY", "").strip(),
            api_url=os.getenv("API_URL", "http://dharana-api:8000").rstrip("/"),
            api_prefix=os.getenv("API_PREFIX", "/api/v1"),
            webhook_url=os.getenv("MAX_WEBHOOK_URL", "").strip(),
            webhook_secret=os.getenv("MAX_WEBHOOK_SECRET", "").strip(),
            webhook_path=os.getenv("MAX_WEBHOOK_PATH", "/max/webhook"),
            host=os.getenv("MAX_WEBHOOK_HOST", "0.0.0.0"),
            port=_int_env("MAX_WEBHOOK_PORT", 8081),
        )


settings = Settings.from_env()
