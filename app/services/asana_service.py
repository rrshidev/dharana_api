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

    def refresh_catalog_cache(self):
        """Invalidate cached category listing after filesystem changes."""
        self._categories_cache = None

    def _find_category(self, asana_name: str) -> Optional[str]:
        categories = self._get_categories()
        for cat_key, asana_names in categories.items():
            if asana_name in asana_names:
                return cat_key
        return None

    @staticmethod
    def validate_asana_name(name: str) -> str:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Название асаны обязательно")
        if any(ch in clean for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|', '\0')):
            raise ValueError("Название содержит недопустимые символы")
        return clean

    def create_asana(self, *, name: str, category_id: str, description: str = "") -> str:
        """Create a new asana on disk (description .txt). Returns the asana name."""
        clean_name = self.validate_asana_name(name)
        if category_id not in CATEGORY_DESCRIPTIONS:
            raise ValueError(
                f"Неизвестная категория '{category_id}'. "
                f"Доступны: {', '.join(CATEGORY_DESCRIPTIONS)}"
            )
        cat_dir = os.path.join(self.catalog_dir, category_id)
        os.makedirs(cat_dir, exist_ok=True)
        txt_path = os.path.join(cat_dir, f"{clean_name}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write((description or "").strip())
        self.refresh_catalog_cache()
        logger.info(f"Created asana '{clean_name}' in category '{category_id}'")
        return clean_name

    def update_asana_description(self, *, name: str, description: str) -> None:
        """Update (or create) the .txt description for an existing asana."""
        clean_name = self.validate_asana_name(name)
        category_id = self._find_category(clean_name)
        if category_id is None:
            raise ValueError(f"Асана '{clean_name}' не найдена")
        txt_path = os.path.join(self.catalog_dir, category_id, f"{clean_name}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write((description or "").strip())
        self.refresh_catalog_cache()

    def upload_asana_photo(self, *, name: str, content: bytes, ext: str) -> str:
        """Save/replace an asana photo; returns the media sub-path."""
        clean_name = self.validate_asana_name(name)
        category_id = self._find_category(clean_name)
        if category_id is None:
            raise ValueError(f"Асана '{clean_name}' не найдена")
        if ext.lower() not in (".jpg", ".jpeg", ".png"):
            raise ValueError("Формат фото должен быть JPG или PNG")
        ext = ext.lower()
        cat_dir = os.path.join(self.catalog_dir, category_id)
        # Remove the previous photo if it had a different extension.
        for old_ext in (".jpg", ".jpeg", ".png"):
            old_path = os.path.join(cat_dir, f"{clean_name}{old_ext}")
            if old_ext != ext and os.path.exists(old_path):
                os.remove(old_path)
        path = os.path.join(cat_dir, f"{clean_name}{ext}")
        with open(path, "wb") as f:
            f.write(content)
        self.refresh_catalog_cache()
        return f"{category_id}/{clean_name}{ext}"

    def delete_asana_files(self, *, name: str) -> Optional[str]:
        """Remove photo + description files (not the video). Returns category_id."""
        clean_name = self.validate_asana_name(name)
        category_id = self._find_category(clean_name)
        if category_id is None:
            return None
        cat_dir = os.path.join(self.catalog_dir, category_id)
        for ext in (".jpg", ".jpeg", ".png", ".txt"):
            path = os.path.join(cat_dir, f"{clean_name}{ext}")
            if os.path.exists(path):
                os.remove(path)
        self.refresh_catalog_cache()
        logger.info(f"Deleted asana files for '{clean_name}'")
        return category_id


asana_service = AsanaService()
