import json
import logging
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials, firestore

from ..config import get_firebase_settings


def _build_firebase_credential():
    settings = get_firebase_settings()
    if settings.service_account_json:
        try:
            return credentials.Certificate(json.loads(settings.service_account_json))
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc

    return credentials.ApplicationDefault()


@lru_cache
def get_firebase_admin_app():
    settings = get_firebase_settings()
    try:
        return firebase_admin.get_app()
    except ValueError:
        options = {"projectId": settings.project_id} if settings.project_id else None
        try:
            return firebase_admin.initialize_app(_build_firebase_credential(), options=options)
        except Exception as exc:  # pragma: no cover - depends on local credential setup
            logging.exception("Firebase Admin initialization failed")
            raise RuntimeError("Firebase Admin credentials are not configured correctly") from exc


@lru_cache
def get_firestore_client():
    return firestore.client(app=get_firebase_admin_app())