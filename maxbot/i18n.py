# -*- coding: utf-8 -*-
"""Тексты MAX-бота Dharana (ru/en).

Держим все строки в одном месте: бот маленький, отдельный файл проще
править, чем разносить текст по обработчикам.
"""

from __future__ import annotations

LANGUAGES = ("ru", "en")

SITE_URL = "https://dharana.ru"


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
            "• `/start` — приветствие\n"
            "• `/help` — помощь\n"
            "• `/about_us` — о проекте и авторах\n"
            "• `/language` — сменить язык\n\n"
            "🔒 **Конфиденциальность**\n"
            "При запуске бота мы сохраняем ваш MAX ID, имя и username, "
            "чтобы бот мог работать. Запуская бота, вы соглашаетесь с "
            "[Политикой конфиденциальности](https://dharana.ru/ru/privacy) "
            "и обработкой персональных данных. "
            "Если вы не согласны — пожалуйста, не используйте бота."
        ),
        "help_text": (
            "**Что умеет бот**\n\n"
            "• Рассказать о проекте Dharana и его авторах\n"
            "• Сменить язык интерфейса (русский / English)\n"
            "• Подсказать, как с нами связаться\n\n"
            "**Команды**\n"
            "• `/start` — приветствие и основные ссылки\n"
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
            "• `/start` — greeting\n"
            "• `/help` — help\n"
            "• `/about_us` — about the project and authors\n"
            "• `/language` — change language\n\n"
            "🔒 **Privacy**\n"
            "When you start the bot, we store your MAX ID, first name and username "
            "so that the bot can work. By starting the bot, you agree to the "
            "[Privacy Policy](https://dharana.ru/en/privacy) "
            "and to personal data processing. "
            "If you disagree, please do not use the bot."
        ),
        "help_text": (
            "**What the bot can do**\n\n"
            "• Tell you about the Dharana project and its authors\n"
            "• Switch the interface language (English / Русский)\n"
            "• Share our contact details\n\n"
            "**Commands**\n"
            "• `/start` — greeting and main links\n"
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
