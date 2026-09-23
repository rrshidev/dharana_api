from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./dharana.db"

    JWT_SECRET: str = "your-super-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "dharana-media"
    MINIO_SECURE: bool = False

    CORS_ORIGINS: str = '["http://localhost:3000","http://localhost:8080"]'

    BOT_DATA_DIR: str = "./bot_data"
    MEDIA_DIR: str = "/opt/dharana/media"
    ADMIN_TELEGRAM_ID: str = ""
    BOT_TOKEN: str = ""
    BOT_ADMIN_KEY: str = ""
    TIMER_BOT_KEY: str = ""

    # Проверка доставляемости почты (DNS MX) при регистрации. Отключать
    # только в тестах/изолированных средах без внешнего DNS.
    EMAIL_DELIVERABILITY_CHECK: bool = True

    # SMTP.BZ — транзакционная почта (верификация email при регистрации).
    # API_SMPT: ключ (Authorization header). Пусто — отправка молча пропускается.
    API_SMPT: str = ""
    SMTPBZ_FROM: str = "noreply@dharana.ru"
    SMTPBZ_FROM_NAME: str = "Dharana"
    # Базовый URL веб-приложения для ссылок верификации из писем.
    EMAIL_VERIFY_BASE_URL: str = "https://dharana.ru"

    # Google OAuth 2.0 — Client ID/Secret из Google Cloud Console.
    # GOOGLE_CLIENT_ID (Web) используется и для валидации audience web-токенов.
    # GOOGLE_ANDROID_CLIENT_ID — для id_token'ов от Flutter (google_sign_in).
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_ANDROID_CLIENT_ID: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return json.loads(self.CORS_ORIGINS)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
