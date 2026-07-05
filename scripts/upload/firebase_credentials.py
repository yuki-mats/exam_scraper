from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "repaso-rbaqy4")
DEFAULT_SECURE_ENV = Path.home() / ".config" / "exam_scraper" / "secure.env"


def load_secure_env_if_present(path: Path = DEFAULT_SECURE_ENV) -> None:
    """ローカルの secure.env があれば、未設定の環境変数だけ補完する。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


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
    load_secure_env_if_present()
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return credentials.ApplicationDefault()
    raise RuntimeError(
        "--credentials-json または GOOGLE_APPLICATION_CREDENTIALS を指定してください。"
    )
