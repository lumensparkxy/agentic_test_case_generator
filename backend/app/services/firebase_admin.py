import json
import logging
import os
from pathlib import Path
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

    application_default_path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if application_default_path and not Path(application_default_path).exists():
        raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS file was not found: {application_default_path}")

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
            raise RuntimeError(f"Firebase Admin credentials are not configured correctly: {exc}") from exc


@lru_cache
def get_firestore_client():
    return firestore.client(app=get_firebase_admin_app())