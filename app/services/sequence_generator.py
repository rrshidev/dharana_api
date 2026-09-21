"""Генератор практик — порт логики Telegram-бота в API.

Структура практики (как в боте): разминка ~10% времени, основная часть по
фокусу ~75%, заминка ~15%. В отличие от бота:

- количество асан масштабируется под целевую длительность (бот генерил
  заметно короче запрошенного времени из-за жёстких лимитов 3+5+2);
- асаны не повторяются между секциями (дедупликация);
- отдых не отдаётся отдельными "Отдых"-шагами, а задаётся через
  per-step `rest_seconds` (веб/приложение вставляют паузы сами).
"""

import random
from typing import Dict, List, Optional, Tuple

from app.services.asana_service import asana_service

# Сложность практики → максимальный уровень сложности каталога (1-5).
DIFFICULTY_MAX = {
    "beginner": 2,
    "intermediate": 3,
    "advanced": 4,
}

# Фокус → категории каталога (соответствует маппингу бота).
FOCUS_CATEGORIES = {
    "back": ["sag+", "coup+"],
    "legs": ["coup+", "sit_lie+"],
    "balance": ["stay+"],
    "flexibility": ["sit_lie+", "sag+"],
    "energy": ["coup+", "power+"],
}

WARMUP_CATEGORIES = ["coup+", "sit_lie+"]
COOLDOWN_CATEGORIES = ["sit_lie+", "sag+"]

# MET для оценки калорий (как в боте, вес 70 кг).
MET_BY_DIFFICULTY = {
    "beginner": 2.5,
    "intermediate": 3.0,
    "advanced": 3.5,
}

WARMUP_FRACTION = 0.10
MAIN_FRACTION = 0.75
REST_SECONDS = 15


class SequenceGenerator:
    """Генерирует последовательность асан по параметрам."""

    def get_candidates(self, categories: List[str], max_difficulty: int) -> List[Tuple[str, str, int]]:
        """Все подходящие по сложности асаны из заданных категорий."""
        used = asana_service._get_categories()
        candidates = []
        for category in categories:
            for name in used.get(category, []):
                from app.data.asana_effects import ASANA_DIFFICULTY

                difficulty = ASANA_DIFFICULTY.get(name, 1)
                if difficulty <= max_difficulty:
                    candidates.append((name, category, difficulty))
        return candidates

    def _pick(self, pool: List[Tuple[str, str, int]], count: int, used_names: set) -> List[Tuple[str, str, int]]:
        """Случайно выбирает до `count` асан с уникальными именами.

        Не повторяет ни уже выбранные (`used_names`), ни два вхождения одного
        имени в пуле (имя может встречаться в нескольких категориях каталога).
        Если неповторяющихся меньше `count` — возвращает сколько есть.
        """
        seen = set(used_names)
        picked = []
        shuffled = list(pool)
        random.shuffle(shuffled)
        for candidate in shuffled:
            if candidate[0] in seen:
                continue
            seen.add(candidate[0])
            picked.append(candidate)
            if len(picked) >= count:
                break
        return picked

    def _build_item(
        self, name: str, category: str, duration_seconds: int, rest_seconds: int,
        lang: str = "ru",
    ) -> Dict:
        detail = asana_service.get_asana_detail(name, lang) or {}
        return {
            "name": name,
            "name_en": detail.get("name_en"),
            "name_ru": detail.get("name_ru"),
            "category_id": category,
            "category_name": detail.get("category_name") or category,
            "description": detail.get("description") or "",
            "image_url": detail.get("image_url"),
            "difficulty": detail.get("difficulty", 1),
            "duration_seconds": duration_seconds,
            "rest_seconds": rest_seconds,
        }

    def generate_sequence(
        self,
        *,
        difficulty: str,
        duration_minutes: int,
        focus: str,
        lang: str = "ru",
    ) -> Dict:
        max_difficulty = DIFFICULTY_MAX[difficulty]
        total_seconds = duration_minutes * 60

        warmup_seconds = int(total_seconds * WARMUP_FRACTION)
        main_seconds = int(total_seconds * MAIN_FRACTION)
        cooldown_seconds = total_seconds - warmup_seconds - main_seconds

        used_names: set = set()
        items = []

        # 1. Разминка
        warmup_pool = self.get_candidates(WARMUP_CATEGORIES, max_difficulty)
        warmup_items = self._build_section(
            warmup_pool, warmup_seconds, min_seconds=20, max_seconds=60,
            item_pool_count_estimate=40, min_count=3, max_count=10,
            rest_seconds=0, used_names=used_names, lang=lang,
        )
        items.extend(warmup_items)

        # 2. Основная часть по фокусу
        main_categories = FOCUS_CATEGORIES.get(focus, WARMUP_CATEGORIES)
        main_pool = self.get_candidates(main_categories, max_difficulty)
        if not main_pool:
            # Запасной вариант: базовые категории (как в боте).
            main_pool = self.get_candidates(["coup+", "sit_lie+"], 5)
        main_items = self._build_section(
            main_pool, main_seconds, min_seconds=45, max_seconds=90,
            item_pool_count_estimate=75, min_count=4, max_count=24,
            rest_seconds=REST_SECONDS, used_names=used_names, lang=lang,
        )
        items.extend(main_items)

        # 3. Заминка
        cooldown_pool = self.get_candidates(COOLDOWN_CATEGORIES, max_difficulty)
        cooldown_items = self._build_section(
            cooldown_pool, cooldown_seconds, min_seconds=20, max_seconds=60,
            item_pool_count_estimate=45, min_count=2, max_count=6,
            rest_seconds=0, used_names=used_names, lang=lang,
        )
        items.extend(cooldown_items)

        actual_duration = sum(item["duration_seconds"] for item in items)
        calories = self._estimate_calories(difficulty, actual_duration)

        return {
            "params": {
                "difficulty": difficulty,
                "duration_minutes": duration_minutes,
                "focus": focus,
            },
            "items": items,
            "total_duration_seconds": actual_duration,
            "estimated_calories": calories,
        }

    def _build_section(
        self,
        pool: List[Tuple[str, str, int]],
        section_seconds: int,
        *,
        min_seconds: int,
        max_seconds: int,
        item_pool_count_estimate: int,
        min_count: int,
        max_count: int,
        rest_seconds: int,
        used_names: set,
        lang: str = "ru",
    ) -> List[Dict]:
        if not pool:
            return []

        count = max(min_count, min(max_count, round(section_seconds / item_pool_count_estimate)))

        per_asana = section_seconds // count if count else 0
        per_asana = max(min_seconds, min(max_seconds, per_asana))

        selected = self._pick(pool, count, used_names)
        for name in (c[0] for c in selected):
            used_names.add(name)

        return [
            self._build_item(name, category, per_asana, rest_seconds, lang)
            for name, category, _difficulty in selected
        ]

    def _estimate_calories(self, difficulty: str, duration_seconds: int) -> int:
        met = MET_BY_DIFFICULTY.get(difficulty, 3.0)
        calories_per_minute = met * 70 / 60
        return int(calories_per_minute * (duration_seconds / 60))


sequence_generator = SequenceGenerator()