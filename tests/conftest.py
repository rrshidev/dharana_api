"""Общая настройка тестов.

Ставит изолированную тестовую SQLite-базу и ключи ДО импорта приложения,
чтобы все тест-модули использовали один временный каталог/БД.
"""
from tests.support import BOT_KEY, JWT_SECRET, TIMER_KEY

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="dharana_test_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["BOT_ADMIN_KEY"] = BOT_KEY
os.environ["TIMER_BOT_KEY"] = TIMER_KEY
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ["JWT_EXPIRE_MINUTES"] = "59"
os.environ["BOT_DATA_DIR"] = os.path.join(_TMP, "bot_data")
# Внешний DNS-поиск в тестах не делаем
os.environ["EMAIL_DELIVERABILITY_CHECK"] = "false"
# Секрет SMTP.BZ не должен просачиваться из .env в тесты (реальные отправки не нужны)
os.environ["API_SMPT"] = ""
os.environ["SMTPBZ_FROM"] = "noreply@dharana.ru"
