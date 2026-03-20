from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    # App / infra
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/newsapp"
    redis_url: str = "redis://redis:6379/0"
    backend_base_url: str = "http://app:8000"

    # Logging
    # Examples: DEBUG, INFO, WARNING, ERROR
    log_level: str = "INFO"

    # Telegram bot
    bot_token: str = "8717739005:AAHcIUr6P17HNCV_w60BE8h7EFN2iNUBv4A"
    bot_username: str = ""

    # Admin alerts (two-vars format)
    admin_chat_id_1: int | None = None
    admin_chat_id_2: int | None = None

    # Support link (username or link)
    admin_support_username: str = ""

    # Reader (Telethon credentials)
    tg_api_id: str = ""
    tg_api_hash: str = ""
    tg_phone_number: str = ""
    tg_session_name: str = "reader"
    tg_session_dir: str = "/code/sessions"

    # Reader identity (so backend can target one reader)
    reader_id: int = 1

    # Limits
    max_sources_per_user_free: int = 10
    max_sources_per_user_paid: int = 20  # paid later
    tg_joins_daily_limit_per_reader: int = 5

    # Digest defaults
    digest_default_time_hhmm: str = "09:00"

    # Reader loop
    reader_poll_interval_sec: int = 30
    reader_heartbeat_interval_sec: int = 30


settings = Settings()

