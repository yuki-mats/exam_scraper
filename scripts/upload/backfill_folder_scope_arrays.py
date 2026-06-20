from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrape.common import load_local_secure_env
from scripts.upload.firebase_credentials import DEFAULT_PROJECT_ID, initialize_firebase_app


BATCH_SIZE = 450


def normalize_scope_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def build_scope_update(data: dict[str, Any]) -> dict[str, list[str]]:
    updates: dict[str, list[str]] = {}

    license_names = normalize_scope_values(data.get("licenseNames"))
    license_name = str(data.get("licenseName") or "").strip()
    if not license_names and license_name:
        license_names = [license_name]
    if license_names != data.get("licenseNames"):
        updates["licenseNames"] = license_names

    qualification_ids = normalize_scope_values(data.get("qualificationIds"))
    qualification_id = str(data.get("qualificationId") or "").strip()
    if not qualification_ids and qualification_id:
        qualification_ids = [qualification_id]
    if qualification_ids != data.get("qualificationIds"):
        updates["qualificationIds"] = qualification_ids

    return updates


def collect_updates(db: Any) -> list[tuple[Any, dict[str, list[str]]]]:
    from google.cloud.firestore_v1 import FieldFilter

    targets: list[tuple[Any, dict[str, list[str]]]] = []
    docs = db.collection("folders").where(
        filter=FieldFilter("isOfficial", "==", True)
    ).stream()
    for doc in docs:
        data = doc.to_dict() or {}
        updates = build_scope_update(data)
        if updates:
            targets.append((doc.reference, updates))
    return targets


def apply_updates(db: Any, targets: list[tuple[Any, dict[str, list[str]]]]) -> int:
    updated = 0
    for start in range(0, len(targets), BATCH_SIZE):
        batch = targets[start : start + BATCH_SIZE]
        firestore_batch = db.batch()
        for ref, updates in batch:
            firestore_batch.update(ref, updates)
        firestore_batch.commit()
        updated += len(batch)
        print(f"updated: {updated}/{len(targets)}")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill standard folders.qualificationIds/licenseNames arrays "
            "from existing scalar qualificationId/licenseName fields."
        )
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON. If omitted, GOOGLE_APPLICATION_CREDENTIALS is used.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Actually update Firestore. Default is dry-run.",
    )
    args = parser.parse_args()

    load_local_secure_env()
    initialize_firebase_app(
        project_id=args.project_id,
        credentials_json=args.credentials_json,
    )
    from firebase_admin import firestore

    db = firestore.client()
    targets = collect_updates(db)
    print(f"target folders: {len(targets)}")
    for ref, updates in targets[:20]:
        print(f"- {ref.id}: {updates}")
    if len(targets) > 20:
        print(f"... {len(targets) - 20} more")
    if not args.update:
        print("dry-run: no docs updated")
        return 0
    updated = apply_updates(db, targets)
    print(f"update complete: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
