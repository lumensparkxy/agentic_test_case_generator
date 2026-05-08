import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency fallback
    load_dotenv = None


DEFAULT_MODEL_NAME = "gemini-3-flash-preview"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORS_ALLOW_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class _SuppressNonTextPartsWarning(logging.Filter):
    """Suppress a known noisy google-genai warning triggered by expected tool-call responses."""

    SUPPRESSED_PREFIX = "Warning: there are non-text parts in the response:"

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - tiny adapter
        message = record.getMessage()
        return not message.startswith(self.SUPPRESSED_PREFIX)


def _load_environment_file() -> None:
    """Load environment variables from repo/root .env so project config wins for local dev."""
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
            load_dotenv(dotenv_path=resolved, override=True)


def _configure_library_warning_filters() -> None:
    logger = logging.getLogger("google_genai.types")
    if any(isinstance(existing_filter, _SuppressNonTextPartsWarning) for existing_filter in logger.filters):
        return
    logger.addFilter(_SuppressNonTextPartsWarning())


_load_environment_file()
_configure_library_warning_filters()


class Settings(BaseModel):
    gemini_api_key: str
    model_name: str = DEFAULT_MODEL_NAME


class AuthSettings(BaseModel):
    google_client_id: str = ""
    google_client_ids: list[str] = Field(default_factory=list)
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60


class FirebaseSettings(BaseModel):
    project_id: str = ""
    service_account_json: str = ""


class JiraSettings(BaseModel):
    connection_secret_key: str = ""
    api_timeout_seconds: int = 15
    project_page_size: int = 50
    issue_page_size: int = 50


class AzureDevOpsSettings(BaseModel):
    connection_secret_key: str = ""
    api_timeout_seconds: int = 15
    api_version: str = "7.1"
    project_page_size: int = 50
    work_item_page_size: int = 50


class BillingSettings(BaseModel):
    pricing_version: str = "pilot-v1"
    token_unit_size: int = 4
    pilot_requirements_limit: int = 200
    pilot_test_cases_limit: int = 200
    contact_email: str = "hello@spica-digital.eu"
    launch_date: Optional[datetime] = None
    shadow_mode: bool = True
    admin_emails: list[str] = Field(default_factory=list)
    max_overdraft_units: int = 0


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


def _parse_bool_env(raw_value: str, *, default: bool) -> bool:
    normalized = str(raw_value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logging.warning("Invalid boolean environment value %s. Falling back to %s.", raw_value, default)
    return default


def _parse_datetime_env(raw_value: str) -> Optional[datetime]:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logging.warning("Invalid datetime environment value %s. Ignoring it.", raw_value)
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_non_negative_int_env(raw_value: str, *, default: int, env_name: str) -> int:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return default
    try:
        parsed = int(normalized)
    except ValueError:
        logging.warning("Invalid %s=%s. Falling back to %s.", env_name, raw_value, default)
        return default
    if parsed < 0:
        logging.warning("Negative %s=%s. Falling back to %s.", env_name, raw_value, default)
        return default
    return parsed


def _parse_positive_int_env(raw_value: str, *, default: int, env_name: str) -> int:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return default
    try:
        parsed = int(normalized)
    except ValueError:
        logging.warning("Invalid %s=%s. Falling back to %s.", env_name, raw_value, default)
        return default
    if parsed <= 0:
        logging.warning("Non-positive %s=%s. Falling back to %s.", env_name, raw_value, default)
        return default
    return parsed


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
def get_firebase_settings() -> FirebaseSettings:
    return FirebaseSettings(
        project_id=(os.getenv("FIREBASE_PROJECT_ID") or "").strip(),
        service_account_json=(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip(),
    )


@lru_cache
def get_jira_settings() -> JiraSettings:
    auth_settings = get_auth_settings()
    connection_secret_key = (
        (os.getenv("JIRA_CONNECTION_SECRET_KEY") or "").strip()
        or auth_settings.jwt_secret_key
    )
    return JiraSettings(
        connection_secret_key=connection_secret_key,
        api_timeout_seconds=_parse_positive_int_env(
            os.getenv("JIRA_API_TIMEOUT_SECONDS", "15"),
            default=15,
            env_name="JIRA_API_TIMEOUT_SECONDS",
        ),
        project_page_size=_parse_positive_int_env(
            os.getenv("JIRA_PROJECT_PAGE_SIZE", "50"),
            default=50,
            env_name="JIRA_PROJECT_PAGE_SIZE",
        ),
        issue_page_size=_parse_positive_int_env(
            os.getenv("JIRA_ISSUE_PAGE_SIZE", "50"),
            default=50,
            env_name="JIRA_ISSUE_PAGE_SIZE",
        ),
    )


@lru_cache
def get_azure_devops_settings() -> AzureDevOpsSettings:
    auth_settings = get_auth_settings()
    connection_secret_key = (
        (os.getenv("AZURE_DEVOPS_CONNECTION_SECRET_KEY") or "").strip()
        or auth_settings.jwt_secret_key
    )
    api_version = (os.getenv("AZURE_DEVOPS_API_VERSION") or "7.1").strip() or "7.1"
    return AzureDevOpsSettings(
        connection_secret_key=connection_secret_key,
        api_timeout_seconds=_parse_positive_int_env(
            os.getenv("AZURE_DEVOPS_API_TIMEOUT_SECONDS", "15"),
            default=15,
            env_name="AZURE_DEVOPS_API_TIMEOUT_SECONDS",
        ),
        api_version=api_version,
        project_page_size=_parse_positive_int_env(
            os.getenv("AZURE_DEVOPS_PROJECT_PAGE_SIZE", "50"),
            default=50,
            env_name="AZURE_DEVOPS_PROJECT_PAGE_SIZE",
        ),
        work_item_page_size=_parse_positive_int_env(
            os.getenv("AZURE_DEVOPS_WORK_ITEM_PAGE_SIZE", "50"),
            default=50,
            env_name="AZURE_DEVOPS_WORK_ITEM_PAGE_SIZE",
        ),
    )


@lru_cache
def get_billing_settings() -> BillingSettings:
    pricing_version = (os.getenv("BILLING_PRICING_VERSION") or "pilot-v1").strip() or "pilot-v1"
    default_contact_email = BillingSettings().contact_email
    contact_email = (os.getenv("BILLING_CONTACT_EMAIL") or default_contact_email).strip() or default_contact_email
    launch_date = _parse_datetime_env(os.getenv("BILLING_LAUNCH_DATE", ""))
    shadow_mode = _parse_bool_env(os.getenv("BILLING_SHADOW_MODE", "true"), default=True)

    return BillingSettings(
        pricing_version=pricing_version,
        token_unit_size=_parse_positive_int_env(
            os.getenv("BILLING_TOKEN_UNIT_SIZE", "4"),
            default=4,
            env_name="BILLING_TOKEN_UNIT_SIZE",
        ),
        pilot_requirements_limit=_parse_positive_int_env(
            os.getenv("BILLING_PILOT_REQUIREMENTS_LIMIT", "200"),
            default=200,
            env_name="BILLING_PILOT_REQUIREMENTS_LIMIT",
        ),
        pilot_test_cases_limit=_parse_positive_int_env(
            os.getenv("BILLING_PILOT_TEST_CASE_LIMIT", "200"),
            default=200,
            env_name="BILLING_PILOT_TEST_CASE_LIMIT",
        ),
        contact_email=contact_email,
        launch_date=launch_date,
        shadow_mode=shadow_mode,
        admin_emails=_dedupe_preserving_order(_split_csv_env(os.getenv("BILLING_ADMIN_EMAILS", ""))),
        max_overdraft_units=_parse_non_negative_int_env(
            os.getenv("BILLING_MAX_OVERDRAFT_UNITS", "0"),
            default=0,
            env_name="BILLING_MAX_OVERDRAFT_UNITS",
        ),
    )


@lru_cache
def get_settings() -> Settings:
    google_api_key = (os.getenv("GOOGLE_API_KEY") or "").strip() or None
    gemini_api_key_env = (os.getenv("GEMINI_API_KEY") or "").strip() or None
    gemini_api_key = google_api_key or gemini_api_key_env
    model_name = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)

    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    if google_api_key and gemini_api_key_env and google_api_key != gemini_api_key_env:
        logging.warning("Both GOOGLE_API_KEY and GEMINI_API_KEY are set; using GOOGLE_API_KEY")

    # Keep a single canonical key variable for SDK clients to avoid noisy warnings.
    os.environ["GOOGLE_API_KEY"] = gemini_api_key
    os.environ.pop("GEMINI_API_KEY", None)

    _warn_if_dependency_mismatch()
    return Settings(gemini_api_key=gemini_api_key, model_name=model_name)
