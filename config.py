from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Claude
    anthropic_api_key: str = ""
    agent_model: str = "claude-opus-5-5"
    utility_model: str = "claude-opus-5-5"
    agent_effort: str = "medium"           # low | medium | high | xhigh | max
    agent_max_turns: int = 12
    agent_timeout_seconds: int = 180
    claude_config_dir: str = ".claude-agent"  # isolated state dir for the Agent SDK's CLI subprocess
    transcript_max_age_minutes: int = 60   # CLI transcripts are deleted after each run; sweep leftovers
    bot_name: str = "운완이"

    # Channels
    primary_channel: str = "discord"       # discord | kakao (future)
    discord_application_id: str = ""
    discord_bot_token: str = ""
    discord_guild_id: str = ""             # slash commands sync to this guild instantly during dev

    # Kakao (future expansion — unused in the Discord drill)
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
    log_content: bool = False              # never enable in production

    # Guards (enforced in code, see utils/message_guard.py)
    max_daily_proactive: int = 4
    max_daily_replies: int = 50
    max_messages_per_run: int = 2
    max_message_sentences: int = 3         # send_message rejects longer texts so the model rewrites
    max_message_chars: int = 400
    max_unanswered_proactive: int = 3
    quiet_start: int = 22                  # local hour; proactive messages blocked from here...
    quiet_end: int = 8                     # ...until here

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, url: str) -> str:
        """Hosts (e.g. Railway) hand out postgres:// or postgresql:// URLs; the app needs asyncpg."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url


settings = Settings()
