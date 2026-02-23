import os
import logging
from importlib.metadata import PackageNotFoundError, version
from functools import lru_cache
from pydantic import BaseModel


DEFAULT_MODEL_NAME = "gemini-2.5-flash"


class Settings(BaseModel):
    gemini_api_key: str
    model_name: str = DEFAULT_MODEL_NAME


def _parse_major_minor(raw_version: str) -> tuple[int, int]:
    parts = raw_version.split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return major, minor


def _warn_if_dependency_mismatch() -> None:
    """Log compatibility warnings for core agent dependencies."""
    try:
        adk_version = version("google-adk")
        genai_version = version("google-genai")
    except PackageNotFoundError:
        logging.warning("Could not verify dependency compatibility for google-adk/google-genai")
        return

    adk_major, adk_minor = _parse_major_minor(adk_version)
    genai_major, genai_minor = _parse_major_minor(genai_version)

    if adk_major != 1:
        logging.warning("Untested google-adk major version detected: %s", adk_version)
    if genai_major != 1:
        logging.warning("Untested google-genai major version detected: %s", genai_version)
    # Track a known-good floor for current pipelines and model usage.
    if (adk_major, adk_minor) < (1, 25):
        logging.warning("google-adk version may be too old for current workflow patterns: %s", adk_version)
    if (genai_major, genai_minor) < (1, 64):
        logging.warning("google-genai version may be too old for current SDK behavior: %s", genai_version)


@lru_cache
def get_settings() -> Settings:
    google_api_key = os.getenv("GOOGLE_API_KEY")
    gemini_api_key_env = os.getenv("GEMINI_API_KEY")
    gemini_api_key = google_api_key or gemini_api_key_env
    model_name = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    if not google_api_key and gemini_api_key_env:
        os.environ["GOOGLE_API_KEY"] = gemini_api_key_env
        # Keep a single canonical key variable for SDK clients to avoid noisy warnings.
        os.environ.pop("GEMINI_API_KEY", None)
    elif google_api_key and gemini_api_key_env:
        logging.warning("Both GOOGLE_API_KEY and GEMINI_API_KEY are set; using GOOGLE_API_KEY")

    _warn_if_dependency_mismatch()
    return Settings(gemini_api_key=gemini_api_key, model_name=model_name)
