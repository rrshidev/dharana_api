"""Общая настройка тестов.

Ставит изолированную тестовую SQLite-базу и ключи ДО импорта приложения,
чтобы все тест-модули использовали один временный каталог/БД.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="dharana_test_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["BOT_ADMIN_KEY"] = "test-bot-admin-key-123"
os.environ["JWT_SECRET"] = "test-jwt-secret-not-used"
os.environ["JWT_EXPIRE_MINUTES"] = "59"
os.environ["BOT_DATA_DIR"] = os.path.join(_TMP, "bot_data")
