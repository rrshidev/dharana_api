# -*- coding: utf-8 -*-
"""Тесты обработчиков MAX-бота (без сети и без платформы MAX)."""

from types import SimpleNamespace

from maxapi import Dispatcher

from maxbot.handlers import LANG_MENU_CALLBACK, MaxBotHandlers


class FakeApi:
    def __init__(self, language: str = "ru", fail: bool = False) -> None:
        self.language = language
        self.fail = fail
        self.registered: list[dict] = []
        self.changed: list[tuple[int, str]] = []

    async def register_user(self, max_id, name=None, username=None, language=None):
        self.registered.append(
            {
                "max_id": max_id,
                "name": name,
                "username": username,
                "language": language,
            }
        )
        if self.fail:
            return None
        return {"language": self.language}

    async def set_language(self, max_id: int, language: str):
        self.changed.append((max_id, language))
        if self.fail:
            return None
        self.language = language
        return language


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeMessage:
    def __init__(self, sender) -> None:
        self.sender = sender
        self.replies: list[tuple[str, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.replies.append((text, kwargs))


class FakeMessageEvent:
    def __init__(self, sender, locale: str | None = "ru") -> None:
        self.message = FakeMessage(sender)
        self.user_locale = locale


class FakeCallbackEvent:
    def __init__(self, payload: str, user, locale: str | None = "ru") -> None:
        self.callback = SimpleNamespace(payload=payload, user=user)
        self.user_locale = locale
        self.answers: list[dict] = []

    async def answer(self, **kwargs):
        self.answers.append(kwargs)


def make_user(user_id: int = 1, name: str = "Иван Петров", username: str = "ivan"):
    return SimpleNamespace(
        user_id=user_id,
        first_name="Иван",
        last_name="Петров",
        username=username,
        full_name=name,
    )


def make_handlers(language: str = "ru", fail: bool = False):
    bot = FakeBot()
    api = FakeApi(language=language, fail=fail)
    handlers = MaxBotHandlers(bot, api)  # type: ignore[arg-type]
    return handlers, bot, api


def buttons(kb) -> list[dict]:
    return kb.payload.buttons[0]


async def test_register_wires_handlers_on_real_dispatcher():
    handlers, _, _ = make_handlers()
    dp = Dispatcher()
    handlers.register(dp)


async def test_start_registers_user_and_replies():
    handlers, _, api = make_handlers("ru")
    event = FakeMessageEvent(make_user())

    await handlers.on_start(event)

    assert api.registered == [
        {
            "max_id": 1,
            "name": "Иван Петров",
            "username": "ivan",
            "language": "ru",
        }
    ]
    text, kwargs = event.message.replies[0]
    assert "Dharana" in text
    assert "Намаскар, Иван Петров" in text
    assert len(kwargs["attachments"]) == 1
    assert kwargs["parse_mode"].value == "markdown"


async def test_start_falls_back_to_locale_when_api_down():
    handlers, _, _ = make_handlers("ru", fail=True)
    event = FakeMessageEvent(make_user(), locale="en")

    await handlers.on_start(event)

    text, _ = event.message.replies[0]
    assert "Welcome to" in text


async def test_help_english_text():
    handlers, _, _ = make_handlers("en")
    user = make_user()
    handlers._languages[user.user_id] = "en"
    event = FakeMessageEvent(user, locale="ru")

    await handlers.on_help(event)

    text, kwargs = event.message.replies[0]
    assert "What the bot can do" in text
    assert kwargs["parse_mode"].value == "markdown"


async def test_language_command_shows_both_languages():
    handlers, _, api = make_handlers("ru")
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user)

    await handlers.on_language(event)

    text, kwargs = event.message.replies[0]
    assert "язык" in text
    rows = buttons(kwargs["attachments"][0])
    assert [b.payload for b in rows] == ["lang:ru", "lang:en"]
    assert api.registered == []


async def test_callback_menu_and_change_language():
    handlers, _, api = make_handlers("ru")
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    menu = FakeCallbackEvent(LANG_MENU_CALLBACK, user)
    await handlers.on_callback(menu)
    assert menu.answers[0]["new_text"].startswith("🌐 Выбери язык")
    assert len(buttons(menu.answers[0]["attachments"][0])) == 2

    changed = FakeCallbackEvent("lang:en", user)
    await handlers.on_callback(changed)
    assert api.changed == [(1, "en")]
    assert handlers._languages[1] == "en"
    assert "English" in changed.answers[0]["new_text"]
    assert changed.answers[0]["attachments"] == []


async def test_callback_language_error_keeps_old_language():
    handlers, _, _ = make_handlers("ru", fail=True)
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeCallbackEvent("lang:en", user)

    await handlers.on_callback(event)

    assert handlers._languages[1] == "ru"
    assert "ответ" in event.answers[0]["new_text"]


async def test_unknown_text_answers_with_help_hint():
    handlers, _, _ = make_handlers("ru")
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user)

    await handlers.on_text(event)

    assert "/help" in event.message.replies[0][0]


async def test_bot_started_sends_welcome_to_chat():
    handlers, bot, api = make_handlers("ru")
    user = make_user()
    event = SimpleNamespace(chat_id=99, user=user, user_locale="ru", payload=None)

    await handlers.on_bot_started(event)

    assert api.registered[0]["max_id"] == 1
    assert bot.sent[0]["chat_id"] == 99
    assert "Dharana" in bot.sent[0]["text"]
    assert bot.sent[0]["parse_mode"].value == "markdown"
