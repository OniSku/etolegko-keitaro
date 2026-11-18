from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr
    keitaro_base_url: str
    keitaro_api_key: SecretStr = Field(min_length=1)
    app_token: SecretStr = Field(min_length=24)
    campaign_prefix: str = Field(pattern=r"^test_[a-z0-9]+_$")
    google_url: str
    request_timeout: float = Field(default=15, gt=0, le=120)

    @field_validator("keitaro_base_url", "google_url")
    @classmethod
    def https_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Expected an HTTPS URL without embedded credentials")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def async_postgres(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("Use postgresql+asyncpg://")
        return value
