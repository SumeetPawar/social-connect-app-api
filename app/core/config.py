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
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "https://sumee-mnj0fhty-eastus2.cognitiveservices.azure.com/")          # e.g. https://your-resource.openai.azure.com
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "REMOVED_SECRET")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.3-chat") # your deployment name
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    
    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"

settings = Settings()