"""Settings, overridable by SCHEMASAGE_* environment variables or .env."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEMASAGE_", env_file=".env", extra="ignore")

    app_db: Path = Path("app.db")
    data_dir: Path = Path("data")
    export_dir: Path = Path("docs")
    benchmark_dir: Path = Path("benchmark")  # questions.jsonl and gold.sql in, results.csv out
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")  # alias bypasses the prefix
    # Langfuse tracing turns on when both keys are set
    langfuse_public_key: str | None = Field(None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(None, validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_base_url: str = Field("https://cloud.langfuse.com", validation_alias="LANGFUSE_BASE_URL")


settings = Settings()
