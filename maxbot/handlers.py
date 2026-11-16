# -*- coding: utf-8 -*-
"""Обработчики команд MAX-бота Dharana.

MVP: /start, /help, /about_us, /language. Код входа в чат не отправляется:
вход идёт через мини-приложение бота (п. 1.5 Требований MAX).
"""

from __future__ import annotations

import logging

from maxapi import Bot, Dispatcher, F
from maxapi.enums.parse_mode import ParseMode
from maxapi.filters.command import Command, CommandStart
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.users import User
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from .api import DharanaApi
from .i18n import (
    LANGUAGES,
    SITE_URL,
    lang_from_locale,
    normalize_lang,
    t,
)

logger = logging.getLogger(__name__)

LANG_MENU_CALLBACK = "lang:menu"


class MaxBotHandlers:
    def __init__(self, bot: Bot, api: DharanaApi) -> None:
        self.bot = bot
        self.api = api
        # Язык кешируем в памяти: MINI-APP/сайт могут его поменять, но на
        # время жизни процесса кеша достаточно, а после рестарта он
        # подтянется из API при первом сообщении.
        self._languages: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Регистрация
    # ------------------------------------------------------------------

    def register(self, dp: Dispatcher) -> None:
        dp.bot_started()(self.on_bot_started)
        dp.message_created(CommandStart())(self.on_start)
        dp.message_created(Command("help"))(self.on_help)
        dp.message_created(Command("about_us"))(self.on_about_us)
        dp.message_created(Command("language"))(self.on_language)
        dp.message_callback(F.callback.payload)(self.on_callback)
        # Последним — ответ на любой текст, чтобы неизвестная команда не
        # оставалась без ответа. Диспетчер выполняет только первый
        # подошедший обработчик, поэтому команды выше имеют приоритет.
        dp.message_created(F.message.body.text)(self.on_text)

    # ------------------------------------------------------------------
    # Команды
    # ------------------------------------------------------------------

    async def on_bot_started(self, event: BotStarted) -> None:
        lang = await self._register(event.user, event.user_locale)
        await self.bot.send_message(
            chat_id=event.chat_id,
            text=self._welcome(lang, event.user),
            attachments=[self._start_keyboard(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_start(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            text=self._welcome(lang, event.message.sender),
            attachments=[self._start_keyboard(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_help(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            t(lang, "help_text"),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_about_us(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            t(lang, "about_us_text"),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_language(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            t(lang, "lang_menu_title"),
            attachments=[self._language_keyboard()],
        )

    async def on_text(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(t(lang, "unknown_text"))

    # ------------------------------------------------------------------
    # Callback-кнопки
    # ------------------------------------------------------------------

    async def on_callback(self, event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        user = event.callback.user
        lang = await self._register(user, event.user_locale)

        if payload == LANG_MENU_CALLBACK:
            await event.answer(
                new_text=t(lang, "lang_menu_title"),
                attachments=[self._language_keyboard()],
            )
            return

        if payload.startswith("lang:"):
            new_lang = normalize_lang(payload.split(":", 1)[1])
            updated = await self.api.set_language(user.user_id, new_lang)
            if updated is None:
                await event.answer(new_text=t(lang, "error_generic"))
                return
            new_lang = normalize_lang(updated)
            self._languages[user.user_id] = new_lang
            await event.answer(
                new_text=t(
                    new_lang,
                    "lang_changed",
                    name=t(new_lang, f"lang_{new_lang}"),
                ),
                attachments=[],
            )
            return

        await event.answer()

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------

    async def _lang(self, event: MessageCreated) -> str:
        return await self._register(event.message.sender, event.user_locale)

    async def _register(self, user: User | None, locale: str | None) -> str:
        """Синхронизирует пользователя с API и возвращает его язык."""
        fallback = lang_from_locale(locale)
        if user is None:
            return fallback

        cached = self._languages.get(user.user_id)
        if cached is not None:
            return cached

        profile = await self.api.register_user(
            max_id=user.user_id,
            name=user.full_name,
            username=user.username,
            language=fallback,
        )
        if profile is None:
            return fallback

        lang = normalize_lang(profile.get("language") or fallback)
        if lang not in LANGUAGES:
            lang = fallback
        self._languages[user.user_id] = lang
        return lang

    @staticmethod
    def _welcome(lang: str, user: User | None) -> str:
        greeting = t(lang, "start_greeting")
        if user is not None and user.full_name:
            greeting = t(lang, "start_greeting_named", name=user.full_name)
        return f"{greeting}\n\n{t(lang, 'start_welcome')}"

    @staticmethod
    def _start_keyboard(lang: str):
        builder = InlineKeyboardBuilder()
        builder.row(LinkButton(text=t(lang, "btn_open_site"), url=SITE_URL))
        builder.row(
            CallbackButton(
                text=t(lang, "btn_language"),
                payload=LANG_MENU_CALLBACK,
            )
        )
        return builder.as_markup()

    @staticmethod
    def _language_keyboard():
        builder = InlineKeyboardBuilder()
        builder.row(
            CallbackButton(text=t("ru", "lang_ru"), payload="lang:ru"),
            CallbackButton(text=t("en", "lang_en"), payload="lang:en"),
        )
        return builder.as_markup()
