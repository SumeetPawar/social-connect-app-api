from pathlib import Path
from pydantic_settings import BaseSettings
import os

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    APP_NAME: str = "Fitness Steps API"
    ENV: str = "dev"
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_MIN: int = 43200  # 30 days — keeps users signed in long-term
    REFRESH_TOKEN_DAYS: int = 365  # 1 year for persistent login
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "https://192.168.4.5:3000","http://192.168.4.5:3000","https://social-app-ui-qa.azurewebsites.net","https://cbiqa.dev.honeywellcloud.com"]
    VAPID_PUBLIC_KEY: str = "BKBAzuVjLcwZxhflTsS79lz2cWz6MYIATlyuSs07MTmI1uRFSAITiCbpXz25_BeeoC3nAJA425oOhwxkOyXFvPQ"
    VAPID_PRIVATE_KEY: str = "CjkoYuaMHbxAkkXLWbwi6gxnWhMkbeCiWF-rNDQT1pE"

    # ── AI provider ───────────────────────────────────────────────────────────
    # AI_PROVIDER: "anthropic" (default) | "azure"
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "azure")  # Change to "azure" to use Azure OpenAI instead of Anthropic

    # Anthropic (used when AI_PROVIDER=anthropic)
    # Reads ANTHROPIC_API_KEY from env automatically; no extra field needed.

    # Azure OpenAI (used when AI_PROVIDER=azure)
    # Set these in .env locally and in App Service > Configuration on Azure.
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-5.3-chat"
    AZURE_OPENAI_API_VERSION: str = "2025-04-01-preview"

    # Google OAuth (used for Google Fit sync)
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", os.getenv("NEXT_PUBLIC_GOOGLE_CLIENT_ID", ""))
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    
    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"

settings = Settings()