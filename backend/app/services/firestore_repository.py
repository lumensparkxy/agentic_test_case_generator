"""Firestore collection adapter used by persistence-facing service modules."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .firebase_admin import get_firestore_client


def get_optional_firestore_collection(
    collection_name: str,
    *,
    unavailable_message: str,
) -> Optional[Any]:
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime state
        logging.warning("%s: %s", unavailable_message, exc)
        return None

    return client.collection(collection_name)


def get_required_firestore_collection(
    collection_name: str,
    *,
    unavailable_message: str,
) -> Any:
    try:
        client = get_firestore_client()
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime state
        raise RuntimeError(unavailable_message) from exc

    return client.collection(collection_name)
