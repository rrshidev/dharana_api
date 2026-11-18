# -*- coding: utf-8 -*-
"""Тексты MAX-бота Dharana (ru/en).

Держим все строки в одном месте: бот маленький, отдельный файл проще
править, чем разносить текст по обработчикам.
"""

from __future__ import annotations

import re
from urllib.parse import quote

LANGUAGES = ("ru", "en")

SITE_URL = "https://dharana.ru"
APK_URL = f"{SITE_URL}/download/dharana.apk"
STARS_TOTAL = 5


def normalize_lang(lang: str | None) -> str:
    """Приводит значение к 'ru'|'en' (всё, что не 'en', считаем русским)."""
    if lang and str(lang).lower().startswith("en"):
        return "en"
    return "ru"


def lang_from_locale(locale: str | None) -> str:
    """Язык по умолчанию из локали MAX ('ru', 'en-US', ...)."""
    return normalize_lang(locale)


def t(lang: str | None, key: str, **kwargs) -> str:
    """Перевод строки. Неизвестный ключ -> 'ru' -> сам ключ."""
    lang = normalize_lang(lang)
    text = TRANSLATIONS.get(lang, {}).get(key)
    if text is None:
        text = TRANSLATIONS["ru"].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def site_url(lang: str | None, path: str = "") -> str:
    """URL страницы сайта в нужной локали (имена асан — кириллицей)."""
    clean = (path or "").strip("/")
    suffix = f"/{quote(clean, safe='/')}" if clean else ""
    return f"{SITE_URL}/{normalize_lang(lang)}{suffix}"


def effect_label(lang: str | None, key: str) -> str:
    """Человекочитаемое название эффекта асаны."""
    return t(lang, f"effect_{key}") if key else ""


def effects_text(lang: str | None, effects: list[str] | None) -> str:
    labels = [effect_label(lang, item) for item in (effects or []) if item]
    return ", ".join(label for label in labels if label)


def difficulty_stars(level: int | None) -> str:
    try:
        filled = max(0, min(STARS_TOTAL, int(level or 0)))
    except (TypeError, ValueError):
        filled = 0
    return "●" * filled + "○" * (STARS_TOTAL - filled)


def truncate_text(text: str | None, limit: int) -> tuple[str, bool]:
    """Обрезает длинный текст по границе слова.

    Возвращает (текст, обрезано ли). Пустые строки сохраняем: описания асан
    размечены абзацами и нумерованными шагами, «схлопывать» их в одну строку
    нельзя.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\t", " ")
    lines = [line.strip() for line in raw.split("\n")]
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(clean) <= limit:
        return clean, False
    cut = clean[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" .,;:-\n") + "…", True


TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        # --- Команды ---
        "start_greeting": "Намаскар! 🙏",
        "start_greeting_named": "Намаскар, {name}! 🙏",
        "start_welcome": (
            "Добро пожаловать в **Dharana** — твой гид по йоге! 🧘\n\n"
            "Dharana — это каталог асан, готовые комплексы, таймер практики "
            "и приложение для регулярных занятий.\n\n"
            "Этот бот — точка входа в проект: здесь можно узнать о Dharana, "
            "сменить язык и связаться с авторами.\n\n"
            "**Команды**\n"
            "• `/start` — приветствие и меню\n"
            "• `/catalog` — каталог асан\n"
            "• `/daily` — асана дня\n"
            "• `/random` — случайная асана\n"
            "• `/basics` — основы йоги\n"
            "• `/steps` — ступени йоги\n"
            "• `/help` — помощь\n"
            "• `/about_us` — о проекте и авторах\n"
            "• `/language` — сменить язык\n\n"
            "Можно и просто написать название асаны — бот найдёт её "
            "в каталоге.\n\n"
            "🔒 **Конфиденциальность**\n"
            "При запуске бота мы сохраняем ваш MAX ID, имя и username, "
            "чтобы бот мог работать. Запуская бота, вы соглашаетесь с "
            "[Политикой конфиденциальности](https://dharana.ru/ru/privacy) "
            "и обработкой персональных данных. "
            "Если вы не согласны — пожалуйста, не используйте бота."
        ),
        "help_text": (
            "**Что умеет бот**\n\n"
            "• Показать каталог асан: категории, карточки, фото\n"
            "• Прислать асану дня и случайную асану\n"
            "• Рассказать об основах и ступенях йоги\n"
            "• Найти асану по названию — просто напишите её\n"
            "• Сменить язык интерфейса (русский / English)\n"
            "• Подсказать, как с нами связаться\n\n"
            "**Команды**\n"
            "• `/start` — приветствие и меню\n"
            "• `/catalog` — каталог асан\n"
            "• `/daily` — асана дня\n"
            "• `/random` — случайная асана\n"
            "• `/basics` — основы йоги\n"
            "• `/steps` — ступени йоги\n"
            "• `/help` — эта справка\n"
            "• `/about_us` — о проекте и авторах\n"
            "• `/language` — сменить язык\n\n"
            "Нужна помощь с приложением или сайтом — напишите нам:\n"
            "@RrshiDev · @yogaolleg"
        ),
        "about_us_text": (
            "🙏 **О проекте и его авторах**\n\n"
            "Меня зовут **Руслан** — я автор и разработчик Dharana. "
            "Проект родился из давней любви к йоге и желания сделать практику "
            "доступной каждому: я занимаюсь разработкой, администрированием "
            "серверов и всем, что помогает проекту расти.\n\n"
            "Немного о пути: раньше я практиковал и преподавал йогу, а в некоторых "
            "публичных медиа с асанами — мои фотографии. То есть Dharana делали "
            "люди, для которых йога — не просто слова.\n\n"
            "**Олег** — мой друг и партнёр, йогин. Он создаёт медиа-контент: "
            "готовит видео с асанами и комплексами, а дальше займётся развитием "
            "и продвижением проекта.\n\n"
            "Два человека. Йога. Немного кода. И желание, чтобы ваша практика "
            "была регулярной и приносила радость.\n\n"
            "Хорошей практики! 🙏\n\n"
            "**Связаться с нами**\n"
            "@RrshiDev · @yogaolleg\n"
            "instagram.com/yogaolleg/\n\n"
            "🔒 **Персональные данные**\n"
            "Мы обрабатываем MAX ID, имя и username, чтобы бот работал. "
            "Подробнее: [Политика конфиденциальности](https://dharana.ru/ru/privacy) · "
            "[Согласие на обработку персональных данных](https://dharana.ru/ru/consent)."
        ),
        "unknown_text": "Не понял команду. Список команд — /help.",
        "error_generic": "Не удалось получить ответ. Попробуйте позже.",

        # --- Меню и каталог ---
        "menu_title": "🏠 **Меню**\n\nВыбери раздел:",
        "menu_catalog": "🔎 Каталог асан",
        "menu_daily": "🧘 Асана дня",
        "menu_random": "🎲 Случайная асана",
        "menu_basics": "📖 Основы",
        "menu_steps": "🪜 Ступени",
        "menu_site": "🌐 Сайт Dharana",
        "menu_app": "📲 Скачать приложение",
        "menu_about": "ℹ️ О проекте",
        "back": "⬅️ Назад",
        "back_menu": "🏠 В меню",
        "back_categories": "⬅️ К категориям",
        "back_list": "⬅️ К списку",
        "open_on_site": "🌐 Открыть на сайте",
        "catalog_title": "🔎 **Каталог асан**\n\nВыбери категорию:",
        "category_caption": "**{name}**\n{count} асан · стр. {page}/{pages}",
        "asana_caption": "**{name}**\nАсан: {count} · стр. {page}/{pages}",
        "search_caption": "🔎 По запросу «{query}» нашлось: {count}",
        "search_more": "\n\nПоказаны первые {shown}. Уточни запрос или открой каталог.",
        "search_empty": (
            "🔎 По запросу «{query}» ничего не нашлось.\n\nПосмотри каталог:"
        ),
        "basics_title": "📖 **Основы йоги**\n\nВыбери раздел:",
        "steps_title": "🪜 **Ступени йоги**\n\nВыбери раздел:",
        "not_found": "Не нашёл этот раздел. Открой меню и попробуй ещё раз.",
        "asana_not_found": "Не нашёл такую асану. Посмотри каталог:",
        "lbl_difficulty": "Сложность",
        "lbl_effects": "✨ Эффекты",
        "lbl_contra": "⚠️ Противопоказания",
        "card_more": "Читать полностью",
        "effect_strength": "сила",
        "effect_balance": "баланс",
        "effect_flexibility": "гибкость",
        "effect_back_pain": "здоровье спины",
        "effect_boost_energy": "энергия",
        "effect_calm_mind": "спокойствие ума",
        "effect_digestion": "пищеварение",
        "effect_stress_relief": "антистресс",

        # --- Язык ---
        "lang_menu_title": "🌐 Выбери язык бота:",
        "lang_changed": "🌐 Язык изменён на {name} ✅",
        "lang_ru": "🇷🇺 Русский",
        "lang_en": "🇬🇧 English",

        # --- Кнопки ---
        "btn_open_site": "🌐 Открыть сайт Dharana",
        "btn_language": "🌐 Язык",
    },
    "en": {
        # --- Commands ---
        "start_greeting": "Namaskar! 🙏",
        "start_greeting_named": "Namaskar, {name}! 🙏",
        "start_welcome": (
            "Welcome to **Dharana** — your yoga guide! 🧘\n\n"
            "Dharana is an asana catalog, ready-made sequences, a practice timer "
            "and an app for regular practice.\n\n"
            "This bot is the entry point to the project: learn about Dharana, "
            "change the language and get in touch with the authors.\n\n"
            "**Commands**\n"
            "• `/start` — greeting and menu\n"
            "• `/catalog` — asana catalog\n"
            "• `/daily` — asana of the day\n"
            "• `/random` — random asana\n"
            "• `/basics` — yoga basics\n"
            "• `/steps` — yoga steps\n"
            "• `/help` — help\n"
            "• `/about_us` — about the project and authors\n"
            "• `/language` — change language\n\n"
            "You can also just type an asana name — the bot will find it "
            "in the catalog.\n\n"
            "🔒 **Privacy**\n"
            "When you start the bot, we store your MAX ID, first name and username "
            "so that the bot can work. By starting the bot, you agree to the "
            "[Privacy Policy](https://dharana.ru/en/privacy) "
            "and to personal data processing. "
            "If you disagree, please do not use the bot."
        ),
        "help_text": (
            "**What the bot can do**\n\n"
            "• Show the asana catalog: categories, cards, photos\n"
            "• Send the asana of the day and a random asana\n"
            "• Tell you about yoga basics and steps\n"
            "• Find an asana by name — just type it\n"
            "• Switch the interface language (English / Русский)\n"
            "• Share our contact details\n\n"
            "**Commands**\n"
            "• `/start` — greeting and menu\n"
            "• `/catalog` — asana catalog\n"
            "• `/daily` — asana of the day\n"
            "• `/random` — random asana\n"
            "• `/basics` — yoga basics\n"
            "• `/steps` — yoga steps\n"
            "• `/help` — this help\n"
            "• `/about_us` — about the project and authors\n"
            "• `/language` — change language\n\n"
            "Need help with the app or website — message us:\n"
            "@RrshiDev · @yogaolleg"
        ),
        "about_us_text": (
            "🙏 **About the project and its authors**\n\n"
            "My name is **Ruslan** — I am the author and developer of Dharana. "
            "The project was born from a long-standing love of yoga and the wish "
            "to make practice available to everyone: I handle development, "
            "server administration and everything that helps the project grow.\n\n"
            "A bit about the journey: I used to practice and teach yoga, and in some "
            "public media with asanas you will find my photos. In other words, "
            "Dharana was made by people for whom yoga is not just words.\n\n"
            "**Oleg** — my friend and partner, a yogi. He creates media content: "
            "he is preparing videos with asanas and sequences, and later he will "
            "work on development and promotion of the project.\n\n"
            "Two people. Yoga. A bit of code. And the wish that your practice "
            "be regular and joyful.\n\n"
            "Have a great practice! 🙏\n\n"
            "**Contact us**\n"
            "@RrshiDev · @yogaolleg\n"
            "instagram.com/yogaolleg/\n\n"
            "🔒 **Personal data**\n"
            "We process your MAX ID, first name and username so that the bot can work. "
            "Learn more: [Privacy Policy](https://dharana.ru/en/privacy) · "
            "[Consent to personal data processing](https://dharana.ru/en/consent)."
        ),
        "unknown_text": "I don't understand this command. See /help.",
        "error_generic": "Couldn't get a response. Please try again later.",

        # --- Menu and catalog ---
        "menu_title": "🏠 **Menu**\n\nChoose a section:",
        "menu_catalog": "🔎 Asana catalog",
        "menu_daily": "🧘 Asana of the day",
        "menu_random": "🎲 Random asana",
        "menu_basics": "📖 Basics",
        "menu_steps": "🪜 Steps",
        "menu_site": "🌐 Dharana website",
        "menu_app": "📲 Download the app",
        "menu_about": "ℹ️ About",
        "back": "⬅️ Back",
        "back_menu": "🏠 Menu",
        "back_categories": "⬅️ Categories",
        "back_list": "⬅️ To the list",
        "open_on_site": "🌐 Open on the website",
        "catalog_title": "🔎 **Asana catalog**\n\nChoose a category:",
        "category_caption": "**{name}**\n{count} asanas · page {page}/{pages}",
        "asana_caption": "**{name}**\nAsanas: {count} · page {page}/{pages}",
        "search_caption": "🔎 Found {count} for “{query}”",
        "search_more": "\n\nShowing the first {shown}. Refine the query or open the catalog.",
        "search_empty": "🔎 Nothing found for “{query}”.\n\nTry the catalog:",
        "basics_title": "📖 **Yoga basics**\n\nChoose a section:",
        "steps_title": "🪜 **Yoga steps**\n\nChoose a section:",
        "not_found": "I couldn't find that section. Open the menu and try again.",
        "asana_not_found": "I couldn't find that asana. Try the catalog:",
        "lbl_difficulty": "Difficulty",
        "lbl_effects": "✨ Effects",
        "lbl_contra": "⚠️ Contraindications",
        "card_more": "Read more",
        "effect_strength": "strength",
        "effect_balance": "balance",
        "effect_flexibility": "flexibility",
        "effect_back_pain": "back health",
        "effect_boost_energy": "energy",
        "effect_calm_mind": "calm mind",
        "effect_digestion": "digestion",
        "effect_stress_relief": "stress relief",

        # --- Language ---
        "lang_menu_title": "🌐 Choose your language:",
        "lang_changed": "🌐 Language changed to {name} ✅",
        "lang_ru": "🇷🇺 Русский",
        "lang_en": "🇬🇧 English",

        # --- Buttons ---
        "btn_open_site": "🌐 Open the Dharana website",
        "btn_language": "🌐 Language",
    },
}
