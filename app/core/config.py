from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    APP_NAME: str = "Fitness Steps API"
    ENV: str = "dev"
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_MIN: int = 30  # 30 minutes
    REFRESH_TOKEN_DAYS: int = 90  # 90 days for persistent login ✨
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "https://192.168.4.5:3000","http://192.168.4.5:3000","https://social-app-ui-qa.azurewebsites.net","https://cbiqa.dev.honeywellcloud.com"]
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    
    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"

settings = Settings()