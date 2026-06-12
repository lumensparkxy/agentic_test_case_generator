from decimal import Decimal
from functools import lru_cache

from ..config import get_billing_settings
from ..models import BillingCatalogEntry


CATALOG_ORDER = (
    "requirements.parse",
    "requirements.refine",
    "testcases.generate",
    "testcases.refine",
    "requirements.enrich",
    "export.csv",
    "export.excel",
    "export.json",
    "export.jira",
    "automation.playwright.generate",
)


@lru_cache
def get_billing_catalog() -> dict[str, BillingCatalogEntry]:
    settings = get_billing_settings()
    return {
        "requirements.parse": BillingCatalogEntry(
            billing_key="requirements.parse",
            display_name="Requirement generation",
            unit="requirement",
            units_per_item=settings.token_unit_size,
            billable=True,
        ),
        "requirements.refine": BillingCatalogEntry(
            billing_key="requirements.refine",
            display_name="Requirement modification",
            unit="requirement",
            units_per_item=max(1, settings.token_unit_size // 2),
            billable=True,
        ),
        "testcases.generate": BillingCatalogEntry(
            billing_key="testcases.generate",
            display_name="Test case generation",
            unit="test_case",
            units_per_item=max(1, settings.token_unit_size // 2),
            billable=True,
        ),
        "testcases.refine": BillingCatalogEntry(
            billing_key="testcases.refine",
            display_name="Test case modification",
            unit="test_case",
            units_per_item=1,
            billable=True,
        ),
        "requirements.enrich": BillingCatalogEntry(
            billing_key="requirements.enrich",
            display_name="Context enrichment",
            unit="artifact_source",
            units_per_item=0,
            billable=False,
        ),
        "export.csv": BillingCatalogEntry(
            billing_key="export.csv",
            display_name="CSV export",
            unit="test_case",
            units_per_item=0,
            billable=False,
        ),
        "export.excel": BillingCatalogEntry(
            billing_key="export.excel",
            display_name="Excel export",
            unit="test_case",
            units_per_item=0,
            billable=False,
        ),
        "export.json": BillingCatalogEntry(
            billing_key="export.json",
            display_name="JSON export",
            unit="test_case",
            units_per_item=0,
            billable=False,
        ),
        "export.jira": BillingCatalogEntry(
            billing_key="export.jira",
            display_name="JIRA export",
            unit="test_case",
            units_per_item=0,
            billable=False,
        ),
        "automation.playwright.generate": BillingCatalogEntry(
            billing_key="automation.playwright.generate",
            display_name="Playwright automation stub generation",
            unit="test_case",
            units_per_item=0,
            billable=False,
        ),
    }


def get_billing_catalog_entries() -> list[BillingCatalogEntry]:
    catalog = get_billing_catalog()
    return [catalog[billing_key] for billing_key in CATALOG_ORDER if billing_key in catalog]


def get_billing_catalog_entry(billing_key: str) -> BillingCatalogEntry | None:
    return get_billing_catalog().get(str(billing_key or "").strip())


def get_minimum_start_units(billing_key: str) -> int:
    entry = get_billing_catalog_entry(billing_key)
    if entry is None or not entry.billable:
        return 0
    return max(1, int(entry.units_per_item or 0))


def calculate_units_for_quantity(*, billing_key: str, quantity: int) -> int:
    entry = get_billing_catalog_entry(billing_key)
    if entry is None or not entry.billable:
        return 0
    return max(0, int(quantity or 0)) * max(0, int(entry.units_per_item or 0))


def format_units_as_tokens(balance_units: int, token_unit_size: int | None = None) -> str:
    normalized_units = max(0, int(balance_units or 0))
    normalized_unit_size = max(1, int(token_unit_size or get_billing_settings().token_unit_size or 1))
    token_value = Decimal(normalized_units) / Decimal(normalized_unit_size)
    rendered = format(token_value, "f").rstrip("0").rstrip(".")
    return rendered or "0"
