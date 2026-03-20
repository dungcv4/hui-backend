from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # DigitalOcean injects DATABASE_URL automatically for Managed PostgreSQL
    database_url_override: Optional[str] = None  # maps to DATABASE_URL env var

    # MySQL/PostgreSQL fallback config
    mysql_host: Optional[str] = "localhost"
    mysql_port: int = 3306
    mysql_user: Optional[str] = "root"
    mysql_password: Optional[str] = ""
    mysql_database: Optional[str] = "huipro"

    use_sqlite: bool = True

    # Sepay Configuration
    sepay_app_id: str
    sepay_secret_key: str
    sepay_webhook_secret: str

    # JWT Configuration
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # CORS
    cors_origins: str = "*"

    # Telegram Configuration
    telegram_bot_token: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        env_prefix="",
        populate_by_name=True,
    )

    @property
    def database_url(self) -> str:
        # 1. DigitalOcean Managed PostgreSQL (auto-injected as DATABASE_URL)
        if self.database_url_override:
            return str(self.database_url_override)
        # 2. SQLite for local dev
        if self.use_sqlite:
            return "sqlite:///./sql_app.db"
        # 3. PostgreSQL manual config
        return f"postgresql+psycopg2://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

settings = Settings()