# -*- coding: utf-8 -*-
"""Обработчики команд MAX-бота Dharana.

Бот — точка входа в проект: меню, каталог асан с фото, асана дня, основы и
ступени йоги, поиск по названию, язык. Код входа в чат не отправляется: вход
идёт через мини-приложение бота (п. 1.5 Требований MAX).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from maxapi import Bot, Dispatcher, F
from maxapi.enums.parse_mode import ParseMode
from maxapi.enums.upload_type import UploadType
from maxapi.filters.command import Command, CommandStart
from maxapi.types.input_media import InputMediaBuffer
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.users import User

from . import keyboards as kb
from .api import DharanaApi
from .catalog import CatalogClient
from .i18n import (
    LANGUAGES,
    difficulty_stars,
    lang_from_locale,
    normalize_lang,
    site_url,
    t,
    truncate_text,
)

logger = logging.getLogger(__name__)

LANG_MENU_CALLBACK = kb.MENU_LANG

PHOTO_TTL = 1800.0
DESCRIPTION_LIMIT = 1200
SEARCH_LIMIT = 10
MIN_SEARCH_LEN = 2

# Отправитель сообщения: либо новое сообщение в чат, либо правка сообщения
# с callback-кнопкой — обработчики от этого не зависят.
Send = Callable[[str, list | None], Awaitable[None]]


class MaxBotHandlers:
    def __init__(self, bot: Bot, api: DharanaApi, catalog: CatalogClient) -> None:
        self.bot = bot
        self.api = api
        self.catalog = catalog
        # Язык кешируем в памяти: MINI-APP/сайт могут его поменять, но на
        # время жизни процесса кеша достаточно, а после рестарта он
        # подтянется из API при первом сообщении.
        self._languages: dict[int, str] = {}
        # Загруженные в MAX фото: {имя асаны: (момент протухания, вложение)}.
        self._photos: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # Регистрация
    # ------------------------------------------------------------------

    def register(self, dp: Dispatcher) -> None:
        dp.bot_started()(self.on_bot_started)
        dp.message_created(CommandStart())(self.on_start)
        dp.message_created(Command("help"))(self.on_help)
        dp.message_created(Command("about_us"))(self.on_about_us)
        dp.message_created(Command("language"))(self.on_language)
        dp.message_created(Command("catalog"))(self.on_catalog)
        dp.message_created(Command("daily"))(self.on_daily)
        dp.message_created(Command("random"))(self.on_random)
        dp.message_created(Command("basics"))(self.on_basics)
        dp.message_created(Command("steps"))(self.on_steps)
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
            attachments=[kb.main_menu(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_start(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            text=self._welcome(lang, event.message.sender),
            attachments=[kb.main_menu(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_help(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            text=t(lang, "help_text"),
            attachments=[kb.main_menu(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_about_us(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            text=t(lang, "about_us_text"),
            attachments=[kb.main_menu(lang)],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def on_language(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await event.message.answer(
            text=t(lang, "lang_menu_title"),
            attachments=[kb.language_menu(lang)],
        )

    async def on_catalog(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await self._show_categories(self._message_sender(event.message), lang)

    async def on_daily(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await self._show_daily(self._message_sender(event.message), lang)

    async def on_random(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await self._show_random(self._message_sender(event.message), lang)

    async def on_basics(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await self._show_content(
            "basics", self._message_sender(event.message), lang, page=0
        )

    async def on_steps(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        await self._show_content(
            "steps", self._message_sender(event.message), lang, page=0
        )

    async def on_text(self, event: MessageCreated) -> None:
        lang = await self._lang(event)
        text = self._message_text(event.message)

        if text.startswith("/") or len(text) < MIN_SEARCH_LEN:
            await event.message.answer(
                text=t(lang, "unknown_text"),
                attachments=[kb.main_menu(lang)],
            )
            return

        await self._show_search(self._message_sender(event.message), lang, text)

    # ------------------------------------------------------------------
    # Callback-кнопки
    # ------------------------------------------------------------------

    async def on_callback(self, event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        user = event.callback.user
        lang = await self._register(user, event.user_locale)
        send = self._callback_sender(event)

        if payload in ("", kb.NOOP):
            # Пустой ответ гасит индикатор загрузки на кнопке-счётчике.
            await event.answer()
            return

        if payload == kb.MENU_LANG:
            await event.answer(
                new_text=t(lang, "lang_menu_title"),
                attachments=[kb.language_menu(lang)],
            )
            return

        if payload.startswith("lang:"):
            await self._change_language(event, user, lang, payload)
            return

        if payload == kb.MENU_MAIN:
            await event.answer(
                new_text=t(lang, "menu_title"),
                attachments=[kb.main_menu(lang)],
                format=ParseMode.MARKDOWN,
            )
            return

        if payload == kb.MENU_ABOUT:
            await event.answer(
                new_text=t(lang, "about_us_text"),
                attachments=[kb.main_menu(lang)],
                format=ParseMode.MARKDOWN,
            )
            return

        if payload == kb.MENU_SOCIALS:
            await event.answer(
                new_text=t(lang, "socials_title"),
                attachments=[kb.socials_menu(lang)],
                format=ParseMode.MARKDOWN,
            )
            return

        if payload == kb.MENU_CATALOG:
            await self._show_categories(send, lang)
            return

        if payload == kb.MENU_DAILY:
            await self._show_daily(send, lang)
            return

        if payload == kb.MENU_RANDOM:
            await self._show_random(send, lang)
            return

        if payload in (kb.MENU_BASICS, kb.MENU_STEPS):
            kind = "basics" if payload == kb.MENU_BASICS else "steps"
            await self._show_content(kind, send, lang, page=0)
            return

        if payload.startswith(kb.CATALOG_PREFIX):
            category_id, page = self._parse_category(payload)
            await self._show_category(send, lang, category_id, page)
            return

        if payload.startswith(kb.ASANA_PREFIX):
            category_id, page, name = self._parse_asana(payload)
            await self._show_asana(send, lang, name, category_id, page)
            return

        if payload.startswith(kb.CONTENT_PREFIX):
            await self._handle_content_payload(send, lang, payload)
            return

        await event.answer()

    async def _change_language(
        self,
        event: MessageCallback,
        user: User | None,
        lang: str,
        payload: str,
    ) -> None:
        if user is None:
            await event.answer(new_text=t(lang, "error_generic"))
            return

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
            attachments=[kb.main_menu(new_lang)],
            format=ParseMode.MARKDOWN,
        )

    async def _handle_content_payload(self, send: Send, lang: str, payload: str) -> None:
        rest = payload[len(kb.CONTENT_PREFIX) :]
        if ":i:" in rest:
            kind, _, index_raw = rest.partition(":i:")
            try:
                index = int(index_raw)
            except ValueError:
                await send(t(lang, "not_found"), [kb.main_menu(lang)])
                return
            await self._show_content_item(send, lang, kind, index)
            return

        kind, _, page_raw = rest.partition(":")
        try:
            page = int(page_raw or 0)
        except ValueError:
            page = 0
        await self._show_content(kind, send, lang, page=page)

    # ------------------------------------------------------------------
    # Экраны
    # ------------------------------------------------------------------

    async def _show_categories(self, send: Send, lang: str) -> None:
        categories = await self.catalog.categories(lang)
        if not categories:
            await send(t(lang, "error_generic"), [kb.main_menu(lang)])
            return
        await send(
            t(lang, "catalog_title"),
            [kb.categories_menu(categories, lang)],
        )

    async def _show_category(
        self,
        send: Send,
        lang: str,
        category_id: str,
        page: int,
    ) -> None:
        items = await self.catalog.category_asanas(category_id, lang)
        if not items:
            await send(t(lang, "not_found"), [kb.main_menu(lang)])
            return

        total = len(items)
        pages = kb.pages_count(total)
        page = max(0, min(page, pages - 1))
        start = page * kb.PAGE_SIZE
        chunk = items[start : start + kb.PAGE_SIZE]

        await send(
            t(
                lang,
                "asana_caption",
                name=await self._category_name(category_id, lang),
                count=total,
                page=page + 1,
                pages=pages,
            ),
            [kb.asanas_menu(category_id, chunk, page, total, lang)],
        )

    async def _show_search(self, send: Send, lang: str, query: str) -> None:
        data = await self.catalog.search(query, lang, limit=SEARCH_LIMIT)
        items = (data or {}).get("items") or []
        if not items:
            await send(
                t(lang, "search_empty", query=query),
                [kb.main_menu(lang)],
            )
            return

        total = int((data or {}).get("total") or len(items))
        caption = t(lang, "search_caption", query=query, count=total)
        if total > len(items):
            caption += t(lang, "search_more", shown=len(items))
        await send(caption, [kb.search_menu(items, lang)])

    async def _show_daily(self, send: Send, lang: str) -> None:
        asana = await self.catalog.daily(lang)
        await self._render_asana(send, lang, asana, category_id=None, page=0)

    async def _show_random(self, send: Send, lang: str) -> None:
        asana = await self.catalog.random_asana(lang)
        await self._render_asana(send, lang, asana, category_id=None, page=0)

    async def _show_asana(
        self,
        send: Send,
        lang: str,
        name: str,
        category_id: str | None,
        page: int,
    ) -> None:
        asana = await self.catalog.asana(name, lang)
        await self._render_asana(send, lang, asana, category_id, page)

    async def _render_asana(
        self,
        send: Send,
        lang: str,
        asana: dict | None,
        category_id: str | None,
        page: int,
    ) -> None:
        if not asana:
            await send(t(lang, "asana_not_found"), [kb.main_menu(lang)])
            return

        text = self._card_text(asana, lang)
        keyboard = kb.asana_card_menu(
            lang,
            asana,
            category_id=category_id,
            page=page,
        )

        photo = await self._photo(asana)
        if photo is not None:
            try:
                await send(text, [photo, keyboard])
                return
            except Exception:
                # Токен мог протухнуть или MAX отверг файл — не оставляем
                # пользователя без карточки, просто шлём текст.
                logger.warning(
                    "Не удалось отправить фото асаны %s", asana.get("name"),
                    exc_info=True,
                )
                self._photos.pop(asana.get("name") or "", None)

        await send(text, [keyboard])

    async def _show_content(
        self,
        kind: str,
        send: Send,
        lang: str,
        page: int,
    ) -> None:
        items = await self._content_items(kind, lang)
        if not items:
            await send(t(lang, "not_found"), [kb.main_menu(lang)])
            return

        total = len(items)
        pages = kb.pages_count(total)
        page = max(0, min(page, pages - 1))
        start = page * kb.PAGE_SIZE
        chunk = items[start : start + kb.PAGE_SIZE]

        title_key = "basics_title" if kind == "basics" else "steps_title"
        await send(
            t(lang, title_key),
            [kb.content_menu(kind, chunk, page, total, lang)],
        )

    async def _show_content_item(
        self,
        send: Send,
        lang: str,
        kind: str,
        index: int,
    ) -> None:
        items = await self._content_items(kind, lang)
        if not items or index < 0 or index >= len(items):
            await send(t(lang, "not_found"), [kb.main_menu(lang)])
            return

        item = items[index]
        body, truncated = truncate_text(item.get("content"), DESCRIPTION_LIMIT)
        display_name = (
            item.get("name_en") if lang == "en" and item.get("name_en") else item.get("name")
        ) or ""
        text = f"**{display_name}**\n\n{body}"
        if truncated:
            text += (
                f"\n\n[{t(lang, 'card_more')}]"
                f"({site_url(lang, kb.content_site_path(kind, item))})"
            )

        page = index // kb.PAGE_SIZE
        await send(text, [kb.content_item_menu(kind, page, lang)])

    async def _content_items(self, kind: str, lang: str) -> list[dict] | None:
        if kind not in kb.CONTENT_KINDS:
            return None
        if kind == "basics":
            return await self.catalog.basics(lang)
        return await self.catalog.steps(lang)

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------

    @staticmethod
    def _message_sender(message) -> Send:
        async def send(text: str, attachments: list | None = None) -> None:
            await message.answer(
                text=text,
                attachments=attachments,
                parse_mode=ParseMode.MARKDOWN,
            )

        return send

    @staticmethod
    def _callback_sender(event: MessageCallback) -> Send:
        async def send(text: str, attachments: list | None = None) -> None:
            await event.answer(
                new_text=text,
                attachments=attachments,
                format=ParseMode.MARKDOWN,
            )

        return send

    @staticmethod
    def _message_text(message) -> str:
        body = getattr(message, "body", None)
        return (getattr(body, "text", None) or "").strip()

    @staticmethod
    def _parse_category(payload: str) -> tuple[str, int]:
        rest = payload[len(kb.CATALOG_PREFIX) :]
        category_id, _, page_raw = rest.rpartition(":")
        if not category_id:
            return rest, 0
        try:
            page = int(page_raw)
        except ValueError:
            page = 0
        return category_id, max(0, page)

    @staticmethod
    def _parse_asana(payload: str) -> tuple[str | None, int, str]:
        rest = payload[len(kb.ASANA_PREFIX) :]
        parts = rest.split(":", 2)
        if len(parts) != 3:
            return None, 0, rest
        category_id, page_raw, name = parts
        try:
            page = int(page_raw)
        except ValueError:
            page = 0
        return (category_id or None), max(0, page), name

    async def _category_name(self, category_id: str, lang: str) -> str:
        categories = await self.catalog.categories(lang) or []
        for category in categories:
            if category.get("id") == category_id:
                return (
                    category.get("display_name")
                    or category.get("id")
                    or category_id
                )
        return category_id

    def _card_text(self, asana: dict, lang: str) -> str:
        name = asana.get("name") or ""
        lines = [f"**{name}**"]

        name_en = (asana.get("name_en") or "").strip()
        if name_en and name_en.casefold() != name.casefold():
            lines.append(name_en.title() if name_en.isupper() else name_en)

        category = (asana.get("category_name") or "").strip()
        if category:
            lines.append(f"📂 {category}")

        level = asana.get("difficulty")
        if level:
            lines.append(
                f"{t(lang, 'lbl_difficulty')}: "
                f"{difficulty_stars(level)} ({level}/5)"
            )

        effects = kb.effect_summary(lang, asana)
        if effects:
            lines.append(f"{t(lang, 'lbl_effects')}: {effects}")

        contraindications = asana.get("contraindications") or []
        # Противопоказания в каталоге только по-русски, поэтому в English
        # интерфейсе их не показываем — вместо перевода был бы мусор.
        if contraindications and lang == "ru":
            lines.append(
                f"{t(lang, 'lbl_contra')}: "
                + ", ".join(str(item) for item in contraindications)
            )

        text = "\n".join(lines)

        description, truncated = truncate_text(
            asana.get("description"), DESCRIPTION_LIMIT
        )
        if description:
            text += f"\n\n{description}"
        if truncated:
            text += (
                f"\n\n[{t(lang, 'card_more')}]"
                f"({site_url(lang, f'asana/{name}')})"
            )
        return text

    async def _photo(self, asana: dict) -> Any | None:
        name = asana.get("name") or ""
        if not name:
            return None

        now = time.monotonic()
        cached = self._photos.get(name)
        if cached is not None and cached[0] > now:
            return cached[1]

        data = await self.catalog.photo(asana.get("image_url") or "")
        if not data:
            return None

        try:
            upload = await self.bot.upload_media(
                InputMediaBuffer(
                    data,
                    filename=f"{name}.jpg",
                    type=UploadType.IMAGE,
                )
            )
        except Exception:
            logger.warning("Не удалось загрузить фото асаны %s", name, exc_info=True)
            return None

        self._photos[name] = (now + PHOTO_TTL, upload)
        return upload

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
