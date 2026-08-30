import os
import logging
import random
from typing import List, Optional, Dict

from app.config import settings
from app.data.asana_effects import ASANA_EFFECTS, ASANA_DIFFICULTY, ASANA_CONTRAINDICATIONS

logger = logging.getLogger(__name__)

CATEGORY_DESCRIPTIONS = {
    "sit_lie+": {"display_name": "Асаны сидя и лёжа", "description": "Список асан сидя и лёжа"},
    "stay+": {"display_name": "Асаны стоя", "description": "Список асан стоя"},
    "hand+": {"display_name": "Балансы на руках", "description": "Список балансов на руках"},
    "coup+": {"display_name": "Перевёрнутые асаны", "description": "Список перевернутых асан"},
    "sag+": {"display_name": "Прогибы", "description": "Список асан с прогибами"},
    "power+": {"display_name": "Силовые асаны", "description": "Список силовых асан"},
}


class AsanaService:
    def __init__(self):
        self.catalog_dir = os.path.join(settings.BOT_DATA_DIR, "catalog")
        self.basics_dir = os.path.join(settings.BOT_DATA_DIR, "basics")
        self.steps_dir = os.path.join(settings.BOT_DATA_DIR, "steps")
        self._categories_cache: Optional[Dict] = None

    def _get_categories(self) -> Dict:
        if self._categories_cache is not None:
            return self._categories_cache

        categories = {}
        if not os.path.exists(self.catalog_dir):
            logger.error(f"Catalog directory not found: {self.catalog_dir}")
            return categories

        for entry in os.listdir(self.catalog_dir):
            entry_path = os.path.join(self.catalog_dir, entry)
            if os.path.isdir(entry_path) and entry in CATEGORY_DESCRIPTIONS:
                asana_files = set()
                for f in os.listdir(entry_path):
                    name = os.path.splitext(f)[0]
                    asana_files.add(name)
                categories[entry] = sorted(asana_files)

        self._categories_cache = categories
        return categories

    def get_all_categories(self) -> List[Dict]:
        categories = self._get_categories()
        result = []
        for cat_key, asana_names in categories.items():
            info = CATEGORY_DESCRIPTIONS[cat_key]
            result.append({
                "id": cat_key,
                "display_name": info["display_name"],
                "description": info["description"],
                "asana_count": len(asana_names),
            })
        return result

    def get_category_asanas(self, category_id: str) -> List[Dict]:
        categories = self._get_categories()
        if category_id not in categories:
            return []
        asana_names = categories[category_id]
        return [self._build_asana_summary(name, category_id) for name in asana_names]

    def get_all_asanas(
        self,
        difficulty: Optional[int] = None,
        effect: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict:
        categories = self._get_categories()
        all_asanas = []
        for cat_key, asana_names in categories.items():
            for name in asana_names:
                all_asanas.append((name, cat_key))

        filtered = []
        for name, cat_key in all_asanas:
            if category is not None:
                if cat_key != category:
                    continue
            if difficulty is not None:
                d = ASANA_DIFFICULTY.get(name)
                if d is None or d != difficulty:
                    continue
            if effect is not None:
                effects = ASANA_EFFECTS.get(name, [])
                if effect not in effects:
                    continue
            if search is not None:
                if search.lower() not in name.lower():
                    continue
            filtered.append((name, cat_key))

        total = len(filtered)
        page = filtered[offset : offset + limit]
        items = [self._build_asana_summary(name, cat_key) for name, cat_key in page]

        return {"total": total, "items": items, "limit": limit, "offset": offset}

    def get_asana_detail(self, asana_name: str) -> Optional[Dict]:
        categories = self._get_categories()
        category_id = None
        for cat_key, asana_names in categories.items():
            if asana_name in asana_names:
                category_id = cat_key
                break

        if category_id is None:
            return None

        description = self._read_file(
            os.path.join(self.catalog_dir, category_id, f"{asana_name}.txt")
        )

        image_filename = None
        for ext in (".jpg", ".png"):
            path = os.path.join(self.catalog_dir, category_id, f"{asana_name}{ext}")
            if os.path.exists(path):
                image_filename = f"{category_id}/{asana_name}{ext}"
                break

        return {
            "name": asana_name,
            "category_id": category_id,
            "category_name": CATEGORY_DESCRIPTIONS.get(category_id, {}).get(
                "display_name", category_id
            ),
            "description": description,
            "image_url": f"/api/v1/media/photos/{image_filename}" if image_filename else None,
            "difficulty": ASANA_DIFFICULTY.get(asana_name, 1),
            "effects": ASANA_EFFECTS.get(asana_name, []),
            "contraindications": ASANA_CONTRAINDICATIONS.get(asana_name, []),
        }

    def get_random_asana(self) -> Optional[Dict]:
        categories = self._get_categories()
        all_names = []
        for cat_key, asana_names in categories.items():
            for name in asana_names:
                all_names.append((name, cat_key))
        if not all_names:
            return None
        name, cat_key = random.choice(all_names)
        return self.get_asana_detail(name)

    def get_basics(self) -> List[Dict]:
        if not os.path.exists(self.basics_dir):
            return []
        items = []
        for f in sorted(os.listdir(self.basics_dir)):
            if f.endswith(".txt"):
                name = f[:-4]
                if name and name[0].isdigit():
                    parts = name.split(".", 1)
                    if len(parts) > 1:
                        name = parts[1].strip()
                items.append({"name": name, "content": self._read_file(os.path.join(self.basics_dir, f))})
        return items

    def get_steps(self) -> List[Dict]:
        if not os.path.exists(self.steps_dir):
            return []
        items = []
        for f in sorted(os.listdir(self.steps_dir)):
            if f.endswith(".txt"):
                name = f[:-4]
                if name and name[0].isdigit():
                    parts = name.split(".", 1)
                    if len(parts) > 1:
                        name = parts[1].strip()
                items.append({"name": name, "content": self._read_file(os.path.join(self.steps_dir, f))})
        return items

    def _build_asana_summary(self, name: str, category_id: str) -> Dict:
        image_filename = None
        for ext in (".jpg", ".png"):
            path = os.path.join(self.catalog_dir, category_id, f"{name}{ext}")
            if os.path.exists(path):
                image_filename = f"{category_id}/{name}{ext}"
                break

        return {
            "name": name,
            "category_id": category_id,
            "image_url": f"/api/v1/media/photos/{image_filename}" if image_filename else None,
            "difficulty": ASANA_DIFFICULTY.get(name, 1),
            "effects": ASANA_EFFECTS.get(name, []),
        }

    @staticmethod
    def _read_file(path: str) -> str:
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            return ""


asana_service = AsanaService()
