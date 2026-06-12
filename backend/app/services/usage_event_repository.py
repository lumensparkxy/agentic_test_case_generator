"""Repository boundary for usage-event reporting reads."""

from __future__ import annotations

import logging
from typing import Any, Iterable, Protocol

from .audit_service import USAGE_EVENTS_COLLECTION
from .firestore_repository import get_optional_firestore_collection


class UsageEventRepository(Protocol):
    def iter_usage_events(self) -> tuple[Iterable[Any], list[str]]: ...


class FirestoreUsageEventRepository:
    def iter_usage_events(self) -> tuple[Iterable[Any], list[str]]:
        collection = get_optional_firestore_collection(
            USAGE_EVENTS_COLLECTION,
            unavailable_message="Firestore usage report collection is unavailable",
        )
        if collection is None:
            return [], ["Firestore usage report collection is unavailable."]

        try:
            return collection.stream(), []
        except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
            logging.warning("Firestore usage report query failed: %s", exc)
            return [], [f"Firestore usage report query failed: {exc}"]
