import os
from functools import lru_cache
from pydantic import BaseModel


class Settings(BaseModel):
    gemini_api_key: str
    model_name: str = "gemini-3-flash-preview"


@lru_cache
def get_settings() -> Settings:
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model_name = os.getenv("MODEL_NAME", "gemini-3-flash-preview")
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required")
    os.environ.setdefault("GOOGLE_API_KEY", gemini_api_key)
    return Settings(gemini_api_key=gemini_api_key, model_name=model_name)
