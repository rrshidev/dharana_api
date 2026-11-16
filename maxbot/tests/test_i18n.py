# -*- coding: utf-8 -*-
"""Тесты строк MAX-бота."""

import pytest

from maxbot.i18n import LANGUAGES, TRANSLATIONS, lang_from_locale, normalize_lang, t


def test_normalize_lang():
    assert normalize_lang("en-US") == "en"
    assert normalize_lang("EN") == "en"
    assert normalize_lang("ru") == "ru"
    assert normalize_lang(None) == "ru"
    assert normalize_lang("de") == "ru"


def test_lang_from_locale():
    assert lang_from_locale("en") == "en"
    assert lang_from_locale("ru-RU") == "ru"
    assert lang_from_locale(None) == "ru"


def test_translations_have_same_keys():
    assert set(TRANSLATIONS) == set(LANGUAGES)
    keys_ru = set(TRANSLATIONS["ru"])
    keys_en = set(TRANSLATIONS["en"])
    assert keys_ru == keys_en


def test_unknown_key_falls_back_to_key():
    assert t("en", "no_such_key") == "no_such_key"


def test_format_kwargs():
    assert "Русский" in t("ru", "lang_changed", name="Русский")
    assert "English" in t("en", "lang_changed", name="English")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_privacy_mentions_max_and_documents(lang):
    text = t(lang, "start_welcome") + t(lang, "about_us_text")
    assert "MAX" in text
    assert "username" in text
    assert f"https://dharana.ru/{lang}/privacy" in text
    assert f"https://dharana.ru/{lang}/consent" in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_help_lists_all_mvp_commands(lang):
    text = t(lang, "help_text")
    for command in ("/start", "/help", "/about_us", "/language"):
        assert command in text
