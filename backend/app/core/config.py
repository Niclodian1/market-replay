from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://marketuser:marketpass@postgres:5432/marketreplay"
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]
    DEBUG: bool = False
    APP_PASSWORD: str = "admin123"


settings = Settings()
