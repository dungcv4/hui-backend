from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # DATABASE_URL - DigitalOcean inject tự động khi attach Managed PostgreSQL
    database_url: Optional[str] = None

    # Fallback: manual PostgreSQL/MySQL config (nếu không có DATABASE_URL)
    db_host: Optional[str] = "localhost"
    db_port: int = 5432
    db_user: Optional[str] = "root"
    db_password: Optional[str] = ""
    db_name: Optional[str] = "huipro"

    # Local dev: dùng SQLite khi không có DATABASE_URL
    use_sqlite: bool = True

    # Sepay
    sepay_app_id: str
    sepay_secret_key: str
    sepay_webhook_secret: str

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # CORS
    cors_origins: str = "*"

    # Telegram
    telegram_bot_token: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    def get_database_url(self) -> str:
        # 1. DATABASE_URL từ DigitalOcean (ưu tiên cao nhất)
        if self.database_url:
            return self.database_url
        # 2. SQLite cho local dev
        if self.use_sqlite:
            return "sqlite:///./sql_app.db"
        # 3. PostgreSQL config thủ công
        return f"postgresql+psycopg2://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"


settings = Settings()