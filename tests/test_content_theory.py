import os

import pytest

from app.config import settings
from app.services.asana_service import asana_service


@pytest.fixture
def theory_dirs():
    basics = os.path.join(settings.BOT_DATA_DIR, "basics")
    steps = os.path.join(settings.BOT_DATA_DIR, "steps")
    os.makedirs(basics, exist_ok=True)
    os.makedirs(steps, exist_ok=True)

    # Основы: файл с числовым префиксом + EN-версия + картинка.
    with open(os.path.join(basics, "9.ЧАКРЫ.txt"), "w", encoding="utf-8") as f:
        f.write("Чакры: описание на русском")
    with open(os.path.join(basics, "9.ЧАКРЫ.en.txt"), "w", encoding="utf-8") as f:
        f.write("CHAKRAS\n\nChakras: english description")
    with open(os.path.join(basics, "9.ЧАКРЫ.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")

    # Ступени: без числового префикса, EN-версия без картинки.
    with open(os.path.join(steps, "ЯМА.txt"), "w", encoding="utf-8") as f:
        f.write("Яма: пять заповедей")
    with open(os.path.join(steps, "ЯМА.en.txt"), "w", encoding="utf-8") as f:
        f.write("YAMA\n\nYama: five precepts")

    yield
    asana_service.refresh_catalog_cache()
    for d in (basics, steps):
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                os.remove(p)


def test_basics_localized(theory_dirs):
    ru = asana_service.get_basics("ru")
    assert len(ru) == 1
    item = ru[0]
    assert item["name"] == "ЧАКРЫ"  # числовой префикс убран
    assert item["name_en"] == "CHAKRAS"
    assert item["content"] == "Чакры: описание на русском"
    assert item["image_url"] == "/api/v1/media/basics/9.ЧАКРЫ.png"

    en = asana_service.get_basics("en")
    assert en[0]["content"] == "CHAKRAS\n\nChakras: english description"


def test_steps_localized(theory_dirs):
    ru = asana_service.get_steps("ru")
    assert len(ru) == 1
    item = ru[0]
    assert item["name"] == "ЯМА"
    assert item["name_en"] == "YAMA"
    assert item["content"] == "Яма: пять заповедей"
    assert item["image_url"] is None  # .png нет

    en = asana_service.get_steps("en")
    assert en[0]["content"] == "YAMA\n\nYama: five precepts"


def test_steps_duplicates_collapse(theory_dirs):
    # Файлы 10.БАНДХИ и 12.БАНДХИ — дубли с одинаковым распарсенным именем.
    steps = os.path.join(settings.BOT_DATA_DIR, "steps")
    for fn, content in (("10.БАНДХИ.txt", "Бандхи 1"), ("12.БАНДХИ.txt", "Бандхи 2")):
        with open(os.path.join(steps, fn), "w", encoding="utf-8") as f:
            f.write(content)
    items = asana_service.get_steps("ru")
    names = [i["name"] for i in items]
    assert names.count("БАНДХИ") == 1