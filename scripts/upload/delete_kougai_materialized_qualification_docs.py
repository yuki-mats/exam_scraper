from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.upload.firebase_credentials import initialize_firebase_app


DEFAULT_MAPPING_JSON = REPO_ROOT / "output" / "kougai" / "category" / "qualification_mappings.json"
DEFAULT_PROJECT_ID = "repaso-rbaqy4"
BATCH_SIZE = 450
TARGET_COLLECTIONS = ("questions", "questionSets", "folders")


def load_mapping(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def qualification_ids_from_mapping(mapping: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in mapping.get("qualifications", []):
        if not isinstance(item, dict):
            continue
        qualification_id = str(item.get("qualificationId") or "").strip()
        if qualification_id and qualification_id != "kougai" and qualification_id not in ids:
            ids.append(qualification_id)
    return ids


def collect_docs(db: Any, qualification_ids: list[str]) -> dict[str, dict[str, list[Any]]]:
    result: dict[str, dict[str, list[Any]]] = {
        collection: {qualification_id: [] for qualification_id in qualification_ids}
        for collection in TARGET_COLLECTIONS
    }
    for collection in TARGET_COLLECTIONS:
        for qualification_id in qualification_ids:
            docs = list(
                db.collection(collection)
                .where("qualificationId", "==", qualification_id)
                .stream()
            )
            result[collection][qualification_id] = docs
    return result


def print_summary(docs_by_collection: dict[str, dict[str, list[Any]]]) -> None:
    grand_total = 0
    for collection in TARGET_COLLECTIONS:
        collection_total = sum(len(docs) for docs in docs_by_collection[collection].values())
        grand_total += collection_total
        print(f"{collection}: {collection_total}")
        for qualification_id, docs in docs_by_collection[collection].items():
            if docs:
                print(f"  - {qualification_id}: {len(docs)}")
    print(f"total: {grand_total}")


def delete_docs(db: Any, docs_by_collection: dict[str, dict[str, list[Any]]]) -> int:
    refs = [
        doc.reference
        for collection in TARGET_COLLECTIONS
        for docs in docs_by_collection[collection].values()
        for doc in docs
    ]
    deleted = 0
    for start in range(0, len(refs), BATCH_SIZE):
        batch = db.batch()
        chunk = refs[start : start + BATCH_SIZE]
        for ref in chunk:
            batch.delete(ref)
        batch.commit()
        deleted += len(chunk)
        print(f"deleted: {deleted}/{len(refs)}")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete old kougai per-qualification materialized Firestore docs."
    )
    parser.add_argument("--mapping-json", type=Path, default=DEFAULT_MAPPING_JSON)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete docs. Default is dry-run.",
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON. If omitted, GOOGLE_APPLICATION_CREDENTIALS is used.",
    )
    args = parser.parse_args()

    mapping = load_mapping(args.mapping_json.expanduser().resolve())
    qualification_ids = qualification_ids_from_mapping(mapping)
    if len(qualification_ids) != 13:
        raise SystemExit(f"expected 13 kougai qualification ids, got {len(qualification_ids)}: {qualification_ids}")

    initialize_firebase_app(
        project_id=args.project_id,
        credentials_json=args.credentials_json,
    )
    from firebase_admin import firestore

    db = firestore.client()
    docs_by_collection = collect_docs(db, qualification_ids)
    print_summary(docs_by_collection)
    if not args.delete:
        print("dry-run: no docs deleted")
        return 0
    deleted = delete_docs(db, docs_by_collection)
    print(f"delete complete: {deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
