import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "FlashGuard"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://flash_user:flash_password@localhost:5432/flashguard")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    RESERVATION_TIMEOUT_SECONDS: int = 600

settings = Settings()
