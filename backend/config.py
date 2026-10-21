"""Settings, overridable by SCHEMASAGE_* environment variables or .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEMASAGE_", env_file=".env", extra="ignore")

    app_db: Path = Path("app.db")
    data_dir: Path = Path("data")


settings = Settings()
