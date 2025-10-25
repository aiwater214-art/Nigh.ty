from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Nigh.ty Dashboard"
    database_url: str = "sqlite:///./app.db"
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
