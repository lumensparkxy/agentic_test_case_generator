import os
import logging
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
from functools import lru_cache
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency fallback
    load_dotenv = None


DEFAULT_MODEL_NAME = "gemini-2.5-flash"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORS_ALLOW_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _load_environment_file() -> None:
    """Load environment variables from repo/root .env so uvicorn cwd does not matter."""
    if load_dotenv is None:
        logging.warning("python-dotenv is not installed; .env auto-loading is disabled")
        return

    candidate_paths = [
        REPO_ROOT / ".env",
        Path.cwd() / ".env",
    ]

    seen = set()
    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            load_dotenv(dotenv_path=resolved, override=False)


_load_environment_file()


class Settings(BaseModel):
    gemini_api_key: str
    model_name: str = DEFAULT_MODEL_NAME


class AuthSettings(BaseModel):
    google_client_id: str = ""
    google_client_ids: list[str] = Field(default_factory=list)
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60


def get_cors_allow_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "")
    if not raw_origins.strip():
        return list(DEFAULT_CORS_ALLOW_ORIGINS)

    parsed_origins = [origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()]
    return parsed_origins or list(DEFAULT_CORS_ALLOW_ORIGINS)


def _parse_major_minor(raw_version: str) -> tuple[int, int]:
    parts = raw_version.split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return major, minor


def _split_csv_env(raw_value: str) -> list[str]:
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


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
def get_auth_settings() -> AuthSettings:
    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    google_client_ids = _dedupe_preserving_order(
        _split_csv_env(os.getenv("GOOGLE_CLIENT_IDS", ""))
        + [google_client_id, os.getenv("VITE_GOOGLE_CLIENT_ID", "").strip()]
    )
    jwt_secret_key = os.getenv("JWT_SECRET_KEY", "")
    jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expiration_raw = os.getenv("JWT_EXPIRATION_MINUTES", "60")

    try:
        jwt_expiration_minutes = int(jwt_expiration_raw)
    except ValueError:
        logging.warning("Invalid JWT_EXPIRATION_MINUTES=%s. Falling back to 60.", jwt_expiration_raw)
        jwt_expiration_minutes = 60

    return AuthSettings(
        google_client_id=google_client_id or (google_client_ids[0] if google_client_ids else ""),
        google_client_ids=google_client_ids,
        jwt_secret_key=jwt_secret_key,
        jwt_algorithm=jwt_algorithm,
        jwt_expiration_minutes=jwt_expiration_minutes,
    )


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
