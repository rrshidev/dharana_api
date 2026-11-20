# -*- coding: utf-8 -*-
"""Инлайн-клавиатуры MAX-бота Dharana.

Payload-строки — единственное состояние навигации: они переживают рестарт
процесса, потому что кодируют категорию, страницу и имя асаны. Лимит MAX на
payload — 256 символов, на текст кнопки — 64.
"""

from __future__ import annotations

import math

from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from .i18n import (
    APK_URL,
    INSTAGRAM_URL,
    MAX_BOT_URL,
    TELEGRAM_CHANNEL_URL,
    TIKTOK_URL,
    YOUTUBE_URL,
    effect_label,
    effects_text,
    site_url,
    t,
)

MENU_MAIN = "menu:main"
MENU_ABOUT = "menu:about"
MENU_LANG = "lang:menu"
MENU_DAILY = "menu:daily"
MENU_RANDOM = "menu:random"
MENU_CATALOG = "menu:catalog"
MENU_BASICS = "menu:basics"
MENU_STEPS = "menu:steps"
MENU_SOCIALS = "menu:socials"
NOOP = "noop"

CATALOG_PREFIX = "cat:"
ASANA_PREFIX = "a:"
CONTENT_PREFIX = "content:"

CONTENT_KINDS = ("basics", "steps")

PAGE_SIZE = 8
BUTTON_TEXT_LIMIT = 64
TITLE_LIMIT = 60


def _cut(text: str, limit: int = TITLE_LIMIT) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _btn(text: str, payload: str) -> CallbackButton:
    # Текст кнопки у MAX — 1..64 символа, лишнее обрезаем.
    return CallbackButton(text=_cut(text, BUTTON_TEXT_LIMIT), payload=payload)


def _link(text: str, url: str) -> LinkButton:
    return LinkButton(text=_cut(text, BUTTON_TEXT_LIMIT), url=url)


def _back_row(lang: str, *buttons: CallbackButton) -> list:
    return [*buttons, _btn(t(lang, "back_menu"), MENU_MAIN)]


def pages_count(total: int) -> int:
    return max(1, math.ceil(total / PAGE_SIZE))


# ----------------------------------------------------------------------
# Меню
# ----------------------------------------------------------------------


def main_menu(lang: str):
    builder = InlineKeyboardBuilder()
    builder.row(_btn(t(lang, "menu_catalog"), MENU_CATALOG))
    builder.row(
        _btn(t(lang, "menu_daily"), MENU_DAILY),
        _btn(t(lang, "menu_random"), MENU_RANDOM),
    )
    builder.row(
        _btn(t(lang, "menu_basics"), MENU_BASICS),
        _btn(t(lang, "menu_steps"), MENU_STEPS),
    )
    builder.row(_btn(t(lang, "menu_socials"), MENU_SOCIALS))
    builder.row(
        _link(t(lang, "menu_site"), site_url(lang)),
        _link(t(lang, "menu_app"), APK_URL),
    )
    builder.row(
        _btn(t(lang, "menu_about"), MENU_ABOUT),
        _btn(t(lang, "btn_language"), MENU_LANG),
    )
    return builder.as_markup()


def socials_menu(lang: str):
    builder = InlineKeyboardBuilder()
    builder.row(_link("Telegram", TELEGRAM_CHANNEL_URL))
    builder.row(_link("MAX", MAX_BOT_URL))
    builder.row(_link("Instagram", INSTAGRAM_URL))
    builder.row(_link("YouTube", YOUTUBE_URL))
    builder.row(_link("TikTok", TIKTOK_URL))
    builder.row(_btn(t(lang, "back_menu"), MENU_MAIN))
    return builder.as_markup()


def language_menu(lang: str):
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(t("ru", "lang_ru"), "lang:ru"),
        _btn(t("en", "lang_en"), "lang:en"),
    )
    builder.row(_btn(t(lang, "back_menu"), MENU_MAIN))
    return builder.as_markup()


# ----------------------------------------------------------------------
# Каталог
# ----------------------------------------------------------------------


def categories_menu(categories: list[dict], lang: str):
    builder = InlineKeyboardBuilder()
    for category in categories:
        label = f"{category.get('display_name') or category.get('id')}"
        count = category.get("asana_count")
        if count:
            label += f" · {count}"
        builder.row(_btn(label, f"{CATALOG_PREFIX}{category.get('id')}:0"))
    builder.row(_btn(t(lang, "back_menu"), MENU_MAIN))
    return builder.as_markup()


def asanas_menu(
    category_id: str,
    items: list[dict],
    page: int,
    total: int,
    lang: str,
):
    """Список асан категории одной страницей + навигация."""
    builder = InlineKeyboardBuilder()
    for item in items:
        name = item.get("name") or ""
        if not name:
            continue
        builder.row(_btn(name, f"{ASANA_PREFIX}{category_id}:{page}:{name}"))

    pages = pages_count(total)
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"{CATALOG_PREFIX}{category_id}:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{pages}", NOOP))
    if page + 1 < pages:
        nav.append(_btn("▶️", f"{CATALOG_PREFIX}{category_id}:{page + 1}"))
    builder.row(*nav)

    builder.row(
        _btn(t(lang, "back_categories"), MENU_CATALOG),
        _btn(t(lang, "back_menu"), MENU_MAIN),
    )
    return builder.as_markup()


def search_menu(items: list[dict], lang: str):
    builder = InlineKeyboardBuilder()
    for item in items:
        name = item.get("name") or ""
        if name:
            # Пустая категория = «назад в меню» в карточке.
            builder.row(_btn(name, f"{ASANA_PREFIX}:0:{name}"))
    builder.row(
        _btn(t(lang, "menu_catalog"), MENU_CATALOG),
        _btn(t(lang, "back_menu"), MENU_MAIN),
    )
    return builder.as_markup()


def asana_card_menu(
    lang: str,
    asana: dict,
    *,
    category_id: str | None = None,
    page: int = 0,
):
    builder = InlineKeyboardBuilder()
    name = asana.get("name") or ""
    path = f"asana/{name}"
    builder.row(_link(t(lang, "open_on_site"), site_url(lang, path)))

    back_buttons = []
    if category_id:
        back_buttons.append(
            _btn(t(lang, "back_list"), f"{CATALOG_PREFIX}{category_id}:{page}")
        )
    back_buttons.append(_btn(t(lang, "back_menu"), MENU_MAIN))
    builder.row(*back_buttons)
    return builder.as_markup()


# ----------------------------------------------------------------------
# Основы / ступени
# ----------------------------------------------------------------------


def content_menu(
    kind: str,
    items: list[dict],
    page: int,
    total: int,
    lang: str,
):
    """items — уже срез страницы, total — размер полного списка."""
    builder = InlineKeyboardBuilder()
    for offset, item in enumerate(items):
        name = item.get("name") or ""
        label = item.get("name_en") if lang == "en" else None
        label = (label or name or "").strip()
        if name:
            builder.row(
                _btn(
                    label,
                    f"{CONTENT_PREFIX}{kind}:i:{page * PAGE_SIZE + offset}",
                )
            )

    pages = pages_count(total)
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"{CONTENT_PREFIX}{kind}:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{pages}", NOOP))
    if page + 1 < pages:
        nav.append(_btn("▶️", f"{CONTENT_PREFIX}{kind}:{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(_btn(t(lang, "back_menu"), MENU_MAIN))
    return builder.as_markup()


def content_item_menu(kind: str, page: int, lang: str):
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(t(lang, "back_list"), f"{CONTENT_PREFIX}{kind}:{page}"),
        _btn(t(lang, "back_menu"), MENU_MAIN),
    )
    return builder.as_markup()


def content_site_path(kind: str, item: dict) -> str:
    name = item.get("name") or ""
    return f"{kind}/{name}"


def effect_summary(lang: str, asana: dict) -> str:
    return effects_text(lang, asana.get("effects"))


__all__ = [
    "MENU_MAIN",
    "MENU_ABOUT",
    "MENU_LANG",
    "MENU_DAILY",
    "MENU_RANDOM",
    "MENU_CATALOG",
    "MENU_BASICS",
    "MENU_STEPS",
    "NOOP",
    "CATALOG_PREFIX",
    "ASANA_PREFIX",
    "CONTENT_PREFIX",
    "CONTENT_KINDS",
    "PAGE_SIZE",
    "pages_count",
    "main_menu",
    "language_menu",
    "categories_menu",
    "asanas_menu",
    "search_menu",
    "asana_card_menu",
    "content_menu",
    "content_item_menu",
    "content_site_path",
    "effect_summary",
    "effect_label",
]
