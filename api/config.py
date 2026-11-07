"""Settings, overridable by SCHEMASAGE_* environment variables or .env."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEMASAGE_", env_file=".env", extra="ignore")

    app_db: Path = Path("app.db")
    data_dir: Path = Path("data")
    export_dir: Path = Path("docs")
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")  # alias bypasses the prefix


settings = Settings()
