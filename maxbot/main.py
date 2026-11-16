# -*- coding: utf-8 -*-
"""Точка входа MAX-бота (uvicorn)."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("maxbot")

    missing = [
        name
        for name, value in (
            ("MAX_BOT_TOKEN", settings.bot_token),
            ("MAX_BOT_NAME", settings.bot_name),
            ("MAX_BOT_KEY", settings.bot_key),
        )
        if not value
    ]
    if missing:
        logger.error("Не заданы обязательные переменные: %s", ", ".join(missing))
        raise SystemExit(1)

    logger.info("Запуск MAX-бота @%s на %s:%s", settings.bot_name, settings.host, settings.port)
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
