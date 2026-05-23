from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "repaso-rbaqy4")


def _load_firebase_modules():
    import firebase_admin
    from firebase_admin import credentials

    return firebase_admin, credentials


def initialize_firebase_app(
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    storage_bucket: str | None = None,
    credentials_json: str | Path | None = None,
) -> None:
    firebase_admin, _ = _load_firebase_modules()
    if firebase_admin._apps:
        return

    options: dict[str, str] = {}
    if project_id:
        options["projectId"] = project_id
    if storage_bucket:
        options["storageBucket"] = storage_bucket

    firebase_admin.initialize_app(_build_credential(credentials_json), options)


def _build_credential(credentials_json: str | Path | None = None):
    _, credentials = _load_firebase_modules()
    if credentials_json:
        return credentials.Certificate(str(credentials_json))
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return credentials.ApplicationDefault()
    raise RuntimeError(
        "--credentials-json または GOOGLE_APPLICATION_CREDENTIALS を指定してください。"
    )
