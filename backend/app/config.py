from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="allow")
    
    DATABASE_URL: str
    REDIS_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str

    CALL_WINDOW_START_HOUR: int = 9
    CALL_WINDOW_END_HOUR: int = 18
    TIMEZONE: str = "Asia/Kolkata"

    MAX_CALL_RETRIES: int = 3
    RETRY_DELAY_SECONDS: int = 30

       # Email (real SMTP)
    SMTP_HOST: str       = "smtp.gmail.com"
    SMTP_PORT: int       = 587
    SMTP_USER: str       = ""
    SMTP_PASSWORD: str   = ""
    EMAIL_FROM_NAME: str = "Sales Cadence Engine"
    EMAIL_FROM: str      = ""



settings = Settings()


