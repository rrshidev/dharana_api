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
    def __init__(self, fail_upload: bool = False) -> None:
        self.sent: list[dict] = []
        self.uploads: list = []
        self.fail_upload = fail_upload

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def upload_media(self, media):
        if self.fail_upload:
            raise RuntimeError("MAX rejected the image")
        self.uploads.append(media)
        return SimpleNamespace(
            type="image",
            payload=SimpleNamespace(token=f"token-{len(self.uploads)}"),
        )


def make_asana(name: str, category: str = "sit_lie+") -> dict:
    return {
        "name": name,
        "name_en": "TEST ASANA",
        "name_ru": name,
        "category_id": category,
        "category_name": "Асаны сидя и лёжа",
        "description": "Описание асаны.\n\nВторой абзац.",
        "image_url": f"/api/v1/media/photos/{category}/{name}.jpg",
        "difficulty": 3,
        "effects": ["strength", "balance"],
        "contraindications": ["Беременность"],
    }


class FakeCatalog:
    def __init__(self, fail_photo: bool = False) -> None:
        self.fail_photo = fail_photo
        self.categories_data = [
            {
                "id": "sit_lie+",
                "display_name": "Асаны сидя и лёжа",
                "description": "",
                "asana_count": 10,
            },
            {
                "id": "stay+",
                "display_name": "Асаны стоя",
                "description": "",
                "asana_count": 2,
            },
        ]
        self.category_items = {
            "sit_lie+": [{"name": f"Асана {i}"} for i in range(1, 11)],
            "stay+": [{"name": f"Стоя {i}"} for i in range(1, 3)],
        }
        self.details = {
            item["name"]: make_asana(item["name"])
            for item in self.category_items["sit_lie+"]
        }
        self.details["Асана дня"] = make_asana("Асана дня")
        self.basics_items = [
            {"name": f"Раздел {i}", "name_en": f"Section {i}", "content": "Длинный текст. " * 200}
            for i in range(1, 11)
        ]
        self.steps_items = [{"name": "ЯМА", "name_en": "YAMA", "content": "Короткий текст."}]

    async def categories(self, lang):
        return self.categories_data

    async def category_asanas(self, category_id, lang):
        return self.category_items.get(category_id)

    async def search(self, query, lang, limit=10):
        found = [
            {"name": name}
            for name in self.details
            if query.casefold() in name.casefold()
        ]
        return {"total": len(found), "items": found[:limit]}

    async def asana(self, name, lang):
        return self.details.get(name)

    async def daily(self, lang):
        return self.details["Асана дня"]

    async def random_asana(self, lang):
        return self.details["Асана 1"]

    async def basics(self, lang):
        return self.basics_items

    async def steps(self, lang):
        return self.steps_items

    async def photo(self, image_url):
        return None if self.fail_photo else b"\xff\xd8\xff fake jpeg"


class FakeMessage:
    def __init__(self, sender, text: str = "") -> None:
        self.sender = sender
        self.body = SimpleNamespace(text=text)
        self.replies: list[tuple[str, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.replies.append((text, kwargs))


class FakeMessageEvent:
    def __init__(self, sender, locale: str | None = "ru", text: str = "") -> None:
        self.message = FakeMessage(sender, text)
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


def make_handlers(
    language: str = "ru",
    fail: bool = False,
    fail_upload: bool = False,
    fail_photo: bool = False,
):
    bot = FakeBot(fail_upload=fail_upload)
    api = FakeApi(language=language, fail=fail)
    catalog = FakeCatalog(fail_photo=fail_photo)
    handlers = MaxBotHandlers(bot, api, catalog)  # type: ignore[arg-type]
    return handlers, bot, api, catalog


def rows(kb) -> list[list]:
    return kb.payload.buttons


def all_buttons(kb) -> list:
    return [button for row in rows(kb) for button in row]


def payloads(kb) -> list[str]:
    return [
        button.payload
        for button in all_buttons(kb)
        if getattr(button, "payload", None)
    ]


def first_row_payloads(kb) -> list[str]:
    return [button.payload for button in rows(kb)[0]]


async def test_register_wires_handlers_on_real_dispatcher():
    handlers, _, _, _ = make_handlers()
    dp = Dispatcher()
    handlers.register(dp)


async def test_start_registers_user_and_replies():
    handlers, _, api, _ = make_handlers("ru")
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


def test_main_menu_has_catalog_and_language():
    from maxbot import keyboards

    menu = keyboards.main_menu("ru")
    assert "menu:catalog" in payloads(menu)
    assert LANG_MENU_CALLBACK in payloads(menu)
    links = [b.url for b in all_buttons(menu) if getattr(b, "url", None)]
    assert any(url.endswith("/download/dharana.apk") for url in links)
    assert any(url == "https://dharana.ru/ru" for url in links)


async def test_start_falls_back_to_locale_when_api_down():
    handlers, _, _, _ = make_handlers("ru", fail=True)
    event = FakeMessageEvent(make_user(), locale="en")

    await handlers.on_start(event)

    text, _ = event.message.replies[0]
    assert "Welcome to" in text


async def test_help_english_text():
    handlers, _, _, _ = make_handlers("en")
    user = make_user()
    handlers._languages[user.user_id] = "en"
    event = FakeMessageEvent(user, locale="ru")

    await handlers.on_help(event)

    text, kwargs = event.message.replies[0]
    assert "What the bot can do" in text
    assert kwargs["parse_mode"].value == "markdown"


async def test_language_command_shows_both_languages():
    handlers, _, api, _ = make_handlers("ru")
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user)

    await handlers.on_language(event)

    text, kwargs = event.message.replies[0]
    assert "язык" in text
    assert first_row_payloads(kwargs["attachments"][0]) == ["lang:ru", "lang:en"]
    assert api.registered == []


async def test_callback_menu_and_change_language():
    handlers, _, api, _ = make_handlers("ru")
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    menu = FakeCallbackEvent(LANG_MENU_CALLBACK, user)
    await handlers.on_callback(menu)
    assert menu.answers[0]["new_text"].startswith("🌐 Выбери язык")
    assert first_row_payloads(menu.answers[0]["attachments"][0]) == [
        "lang:ru",
        "lang:en",
    ]

    changed = FakeCallbackEvent("lang:en", user)
    await handlers.on_callback(changed)
    assert api.changed == [(1, "en")]
    assert handlers._languages[1] == "en"
    assert "English" in changed.answers[0]["new_text"]
    assert "menu:catalog" in payloads(changed.answers[0]["attachments"][0])


async def test_callback_language_error_keeps_old_language():
    handlers, _, _, _ = make_handlers("ru", fail=True)
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeCallbackEvent("lang:en", user)

    await handlers.on_callback(event)

    assert handlers._languages[1] == "ru"
    assert "ответ" in event.answers[0]["new_text"]


async def test_catalog_command_lists_categories():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user)

    await handlers.on_catalog(event)

    text, kwargs = event.message.replies[0]
    assert "Каталог асан" in text
    kb = kwargs["attachments"][0]
    assert rows(kb)[0][0].text.startswith("Асаны сидя и лёжа")
    assert payloads(kb)[:2] == ["cat:sit_lie+:0", "cat:stay+:0"]


async def test_category_pagination_moves_between_pages():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    first = FakeCallbackEvent("cat:sit_lie+:0", user)
    await handlers.on_callback(first)
    kb = first.answers[0]["attachments"][0]
    asana_rows = [row for row in rows(kb) if row[0].payload.startswith("a:")]
    assert len(asana_rows) == 8
    assert "cat:sit_lie+:1" in payloads(kb)

    second = FakeCallbackEvent("cat:sit_lie+:1", user)
    await handlers.on_callback(second)
    kb = second.answers[0]["attachments"][0]
    asana_rows = [row for row in rows(kb) if row[0].payload.startswith("a:")]
    assert len(asana_rows) == 2
    assert "cat:sit_lie+:0" in payloads(kb)
    assert "cat:sit_lie+:2" not in payloads(kb)


async def test_asana_card_sends_photo_and_site_link():
    handlers, bot, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("a:sit_lie+:0:Асана 3", user)
    await handlers.on_callback(event)

    text = event.answers[0]["new_text"]
    assert "Асана 3" in text
    assert "Test Asana" in text
    assert "Сложность" in text
    assert "●●●○○" in text
    assert "сила, баланс" in text
    assert "Беременность" in text

    attachments = event.answers[0]["attachments"]
    assert len(attachments) == 2
    assert attachments[0].payload.token == "token-1"
    assert len(bot.uploads) == 1
    links = [
        button.url
        for button in all_buttons(attachments[1])
        if getattr(button, "url", None)
    ]
    assert links == ["https://dharana.ru/ru/asana/%D0%90%D1%81%D0%B0%D0%BD%D0%B0%203"]
    assert "cat:sit_lie+:0" in payloads(attachments[1])

    # Повторное открытие карточки не грузит фото заново.
    again = FakeCallbackEvent("a:sit_lie+:0:Асана 3", user)
    await handlers.on_callback(again)
    assert len(bot.uploads) == 1


async def test_asana_card_falls_back_to_text_when_upload_fails():
    handlers, _, _, _ = make_handlers(fail_upload=True)
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("a:sit_lie+:0:Асана 1", user)
    await handlers.on_callback(event)

    attachments = event.answers[0]["attachments"]
    assert len(attachments) == 1
    assert event.answers[0]["new_text"].startswith("**Асана 1**")


async def test_asana_card_without_photo_when_catalog_has_none():
    handlers, bot, _, _ = make_handlers(fail_photo=True)
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("a:sit_lie+:0:Асана 2", user)
    await handlers.on_callback(event)

    assert len(event.answers[0]["attachments"]) == 1
    assert bot.uploads == []


async def test_search_finds_asanas_by_text():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user, text="Асана 7")

    await handlers.on_text(event)

    text, kwargs = event.message.replies[0]
    assert "«Асана 7»" in text
    assert payloads(kwargs["attachments"][0])[0] == "a::0:Асана 7"


async def test_search_reports_nothing_found():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"
    event = FakeMessageEvent(user, text="нет такого")

    await handlers.on_text(event)

    text, kwargs = event.message.replies[0]
    assert "ничего не нашлось" in text
    assert "menu:catalog" in payloads(kwargs["attachments"][0])


async def test_short_or_command_text_falls_back_to_help_hint():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    for value in ("", "a", "/unknown"):
        event = FakeMessageEvent(user, text=value)
        await handlers.on_text(event)
        text, _ = event.message.replies[0]
        assert "/help" in text


async def test_basics_list_and_item_with_site_link():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    listing = FakeCallbackEvent("content:basics:0", user)
    await handlers.on_callback(listing)
    kb = listing.answers[0]["attachments"][0]
    item_rows = [
        row for row in rows(kb) if row[0].payload.startswith("content:basics:i:")
    ]
    assert len(item_rows) == 8
    assert "content:basics:1" in payloads(kb)

    item = FakeCallbackEvent("content:basics:i:0", user)
    await handlers.on_callback(item)
    text = item.answers[0]["new_text"]
    assert "**Раздел 1**" in text
    assert "Читать полностью" in text
    assert "https://dharana.ru/ru/basics/" in text
    assert "content:basics:0" in payloads(item.answers[0]["attachments"][0])


async def test_steps_item_without_truncation_has_no_link():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("content:steps:i:0", user)
    await handlers.on_callback(event)

    text = event.answers[0]["new_text"]
    assert "ЯМА" in text
    assert "Короткий текст." in text
    assert "dharana.ru" not in text


async def test_content_uses_english_names_for_buttons_and_header():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "en"

    listing = FakeCallbackEvent("content:basics:0", user)
    await handlers.on_callback(listing)
    kb = listing.answers[0]["attachments"][0]
    item_rows = [row for row in rows(kb) if row[0].payload.startswith("content:basics:i:")]
    assert item_rows[0][0].text == "Section 1"
    assert item_rows[1][0].text == "Section 2"

    item = FakeCallbackEvent("content:basics:i:0", user)
    await handlers.on_callback(item)
    text = item.answers[0]["new_text"]
    assert "**Section 1**" in text
    assert "Раздел 1" not in text
    assert "https://dharana.ru/en/basics/" in text

    steps = FakeCallbackEvent("content:steps:i:0", user)
    await handlers.on_callback(steps)
    assert "**YAMA**" in steps.answers[0]["new_text"]


async def test_daily_and_random_commands_render_cards():
    handlers, bot, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    daily = FakeMessageEvent(user)
    await handlers.on_daily(daily)
    assert "Асана дня" in daily.message.replies[0][0]

    random_event = FakeMessageEvent(user)
    await handlers.on_random(random_event)
    text, kwargs = random_event.message.replies[0]
    assert "Асана 1" in text
    assert len(kwargs["attachments"]) == 2
    assert len(bot.uploads) == 2


async def test_noop_callback_answers_silently():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("noop", user)
    await handlers.on_callback(event)

    assert event.answers == [{}]


async def test_unknown_payload_is_ignored():
    handlers, _, _, _ = make_handlers()
    user = make_user()
    handlers._languages[user.user_id] = "ru"

    event = FakeCallbackEvent("something-else", user)
    await handlers.on_callback(event)

    assert event.answers == [{}]


async def test_parse_payload_helpers():
    assert MaxBotHandlers._parse_category("cat:stay+:2") == ("stay+", 2)
    assert MaxBotHandlers._parse_category("cat:stay+") == ("stay+", 0)
    assert MaxBotHandlers._parse_asana("a:stay+:1:Асана 1") == (
        "stay+",
        1,
        "Асана 1",
    )
    assert MaxBotHandlers._parse_asana("a::0:Асана 1") == (
        None,
        0,
        "Асана 1",
    )


async def test_bot_started_sends_welcome_to_chat():
    handlers, bot, api, _ = make_handlers("ru")
    user = make_user()
    event = SimpleNamespace(chat_id=99, user=user, user_locale="ru", payload=None)

    await handlers.on_bot_started(event)

    assert api.registered[0]["max_id"] == 1
    assert bot.sent[0]["chat_id"] == 99
    assert "Dharana" in bot.sent[0]["text"]
    assert bot.sent[0]["parse_mode"].value == "markdown"
    assert "menu:catalog" in payloads(bot.sent[0]["attachments"][0])
