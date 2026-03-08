from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic
    anthropic_api_key: str = ""

    # Kakao
    kakao_rest_api_key: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = ""
    kakao_admin_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://user:pass@localhost/fitnudge"
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "change-me"
    max_daily_messages: int = 5
    agent_silent_after_hour: int = 22


settings = Settings()
