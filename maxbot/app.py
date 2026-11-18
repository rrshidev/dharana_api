# -*- coding: utf-8 -*-
"""FastAPI-приложение MAX-бота: приём webhook от платформы MAX."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from maxapi import Bot, Dispatcher
from maxapi.enums.update import UpdateType
from maxapi.types import BotCommand
from maxapi.webhook.fastapi import FastAPIMaxWebhook

from .api import DharanaApi
from .catalog import CatalogClient
from .config import settings
from .handlers import MaxBotHandlers

logger = logging.getLogger(__name__)

UPDATE_TYPES = [
    UpdateType.MESSAGE_CREATED,
    UpdateType.BOT_STARTED,
    UpdateType.MESSAGE_CALLBACK,
]

# Меню команд в карточке бота MAX. Описания — по-русски: это основной язык
# аудитории, а список команд у бота один на все языки.
BOT_COMMANDS = (
    BotCommand(name="start", description="Меню и приветствие"),
    BotCommand(name="catalog", description="Каталог асан"),
    BotCommand(name="daily", description="Асана дня"),
    BotCommand(name="random", description="Случайная асана"),
    BotCommand(name="basics", description="Основы йоги"),
    BotCommand(name="steps", description="Ступени йоги"),
    BotCommand(name="help", description="Помощь"),
    BotCommand(name="about_us", description="О проекте и авторах"),
    BotCommand(name="language", description="Сменить язык"),
)


def create_app() -> FastAPI:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    api = DharanaApi(
        base_url=settings.api_url,
        api_prefix=settings.api_prefix,
        bot_key=settings.bot_key,
    )
    catalog = CatalogClient(
        base_url=settings.api_url,
        api_prefix=settings.api_prefix,
    )
    MaxBotHandlers(bot, api, catalog).register(dp)

    webhook = FastAPIMaxWebhook(
        dp,
        bot,
        secret=settings.webhook_secret or None,
    )

    async def _subscribe() -> None:
        """Подписывает MAX на наш webhook (идемпотентно для того же URL)."""
        if not settings.webhook_url:
            logger.warning(
                "MAX_WEBHOOK_URL не задан — webhook не зарегистрирован. "
                "Задайте публичный HTTPS-адрес, например "
                "https://dharana.ru/max/webhook"
            )
            return
        try:
            await bot.subscribe_webhook(
                settings.webhook_url,
                update_types=UPDATE_TYPES,
                secret=settings.webhook_secret or None,
            )
            logger.info("MAX webhook подписан: %s", settings.webhook_url)
        except Exception:
            # Не валим процесс: webhook мог быть уже подписан, а платформа
            # может быть временно недоступна. Ошибка видна в логах.
            logger.exception("Не удалось подписаться на MAX webhook")

    async def _publish_commands() -> None:
        """Публикует список команд — он появляется в карточке бота в MAX."""
        try:
            await bot.set_commands(*BOT_COMMANDS)
            logger.info("Меню команд MAX опубликовано (%s)", len(BOT_COMMANDS))
        except Exception:
            logger.exception("Не удалось опубликовать меню команд MAX")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with webhook.lifespan(app):
            await _subscribe()
            await _publish_commands()
            try:
                yield
            finally:
                await catalog.close()
                await api.close()

    app = FastAPI(title="Dharana MAX bot", lifespan=lifespan)
    webhook.setup(app, path=settings.webhook_path)

    @app.get("/health")
    async def health() -> dict[str, bool | str]:
        return {"ok": True, "bot": settings.bot_name}

    return app
