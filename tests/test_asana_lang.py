import os

import pytest

from app.config import settings
from app.services.asana_service import (
    NAME_RU_OVERRIDES,
    asana_service,
    resolve_lang,
)


@pytest.fixture
def catalog():
    base = os.path.join(settings.BOT_DATA_DIR, "catalog", "stay+")
    os.makedirs(base, exist_ok=True)
    name = "Тестасана"
    with open(os.path.join(base, name + ".txt"), "w", encoding="utf-8") as f:
        f.write("RU описание")
    with open(os.path.join(base, name + ".en.txt"), "w", encoding="utf-8") as f:
        f.write("TEST ASANA\n\nEN description")
    asana_service.refresh_catalog_cache()
    yield name
    asana_service.refresh_catalog_cache()
    for fn in (name + ".txt", name + ".en.txt"):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            os.remove(p)


def test_resolve_lang():
    assert resolve_lang("en", None) == "en"
    assert resolve_lang("ru", "en-US,en;q=0.9") == "ru"
    assert resolve_lang(None, "en-US,en;q=0.9") == "en"
    assert resolve_lang(None, None) == "ru"
    assert resolve_lang("de", "en") == "en"
    assert resolve_lang("de", None) == "ru"


def test_en_description_and_no_phantom(catalog):
    names = [a["name"] for a in asana_service.get_category_asanas("stay+")]
    assert catalog in names
    assert not any(n.endswith(".en") for n in names)

    ru = asana_service.get_asana_detail(catalog, "ru")
    en = asana_service.get_asana_detail(catalog, "en")
    assert ru["description"] == "RU описание"
    assert en["description"] == "TEST ASANA\n\nEN description"
    assert en["category_name"] == "Standing Asanas"
    assert ru["category_name"] == "Асаны стоя"
    assert en["name_en"] == "TEST ASANA"
    assert ru["name_en"] == "TEST ASANA"
    assert en["name_ru"] == catalog
    assert ru["name_ru"] == catalog
    summaries = asana_service.get_category_asanas("stay+")
    assert all("name_en" in s for s in summaries)
    assert all("name_ru" in s for s in summaries)


def test_name_ru_overrides():
    assert NAME_RU_OVERRIDES["Маричасана 3"] == "Маричиасана 3"
    assert NAME_RU_OVERRIDES["Маричасана 4"] == "Маричиасана 4"
    assert NAME_RU_OVERRIDES["ВИРАБХАДРАСАНА 2"] == "Вирабхадрасана 2"
    assert NAME_RU_OVERRIDES["Уттхита Баддха Паршваконасанаv"] == "Уттхита Баддха Паршваконасана"


def test_phantom_now_readable(catalog):
    base = os.path.join(settings.BOT_DATA_DIR, "catalog", "stay+")
    name = "Маричасана 4"
    with open(os.path.join(base, name + ".txt"), "w", encoding="utf-8") as f:
        f.write("Группа асан: сидя и лежа (скрутки)")
    with open(os.path.join(base, name + ".en.txt"), "w", encoding="utf-8") as f:
        f.write("MARICHIASANA IV\n\nEN text")
    asana_service.refresh_catalog_cache()
    try:
        ru = asana_service.get_asana_detail(name, "ru")
        en = asana_service.get_asana_detail(name, "en")
        assert ru is not None, "phantom asana must resolve after adding .txt"
        assert ru["name_ru"] == "Маричиасана 4"
        assert en["name_en"] == "MARICHIASANA IV"
        assert en["description"] == "MARICHIASANA IV\n\nEN text"
    finally:
        for fn in (name + ".txt", name + ".en.txt"):
            p = os.path.join(base, fn)
            if os.path.exists(p):
                os.remove(p)
        asana_service.refresh_catalog_cache()


def test_en_fallback_when_missing(catalog):
    base = os.path.join(settings.BOT_DATA_DIR, "catalog", "stay+")
    os.remove(os.path.join(base, catalog + ".en.txt"))
    asana_service.refresh_catalog_cache()
    en = asana_service.get_asana_detail(catalog, "en")
    assert en["description"] == "RU описание"


def test_categories_localized():
    ru = {c["id"]: c["display_name"] for c in asana_service.get_all_categories("ru")}
    en = {c["id"]: c["display_name"] for c in asana_service.get_all_categories("en")}
    assert ru["stay+"] == "Асаны стоя"
    assert en["stay+"] == "Standing Asanas"


def test_search_by_localized_names(catalog):
    res = asana_service.get_all_asanas(search="TEST ASANA")
    assert catalog in [a["name"] for a in res["items"]]
    res_lower = asana_service.get_all_asanas(search="test asana")
    assert catalog in [a["name"] for a in res_lower["items"]]
    res_ru = asana_service.get_all_asanas(search="Тестасана")
    assert catalog in [a["name"] for a in res_ru["items"]]


def test_generate_sequence_localized(catalog):
    from app.services.sequence_generator import sequence_generator

    seq_en = sequence_generator.generate_sequence(
        difficulty="beginner", duration_minutes=15, focus="balance", lang="en"
    )
    assert seq_en["items"]
    assert all("name_en" in i and "name_ru" in i for i in seq_en["items"])
    item = next(i for i in seq_en["items"] if i["name"] == catalog)
    assert item["name_en"] == "TEST ASANA"
    assert item["name_ru"] == catalog
    assert item["category_name"] == "Standing Asanas"

    seq_ru = sequence_generator.generate_sequence(
        difficulty="beginner", duration_minutes=15, focus="balance", lang="ru"
    )
    item_ru = next(i for i in seq_ru["items"] if i["name"] == catalog)
    assert item_ru["name_en"] == "TEST ASANA"
    assert item_ru["name_ru"] == catalog
    assert item_ru["category_name"] == "Асаны стоя"
