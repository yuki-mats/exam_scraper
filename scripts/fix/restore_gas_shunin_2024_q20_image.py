#!/usr/bin/env python3
"""ガス主任技術者甲種2024年消費機器問20の公式図を限定復元する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.upload.firebase_credentials import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)


SCHEMA_VERSION = "gas-shunin-2024-q20-image-repair/v1"
ORIGINAL_QUESTION_ID = "a227f152a94ed421"
QUESTION_IDS = tuple(
    f"gas-shunin-kou-2024-shohi-q20-s{index:02d}" for index in range(1, 6)
)
IMAGE_URL = (
    "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
    "question_images%2Fofficial%2Fgas-shunin-kou%2F"
    "official-source-2024-a227f152a94ed421-6c0009988bdc123b.png?alt=media"
)
IMAGE_STORAGE_PATH = (
    "question_images/official/gas-shunin-kou/"
    "official-source-2024-a227f152a94ed421-6c0009988bdc123b.png"
)
IMAGE_SHA256 = "6c0009988bdc123bfa1f90fea8653e1e755d7b318a6c588b748f5c6beddf6c08"
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
MIN_SYNC_UPDATED_AT = datetime(2026, 9, 5, 2, 42, tzinfo=timezone.utc)
OFFICIAL_PDF_URL = "https://www.jia-page.or.jp/files/user/doc/exam/q_kou_r6.pdf"
OFFICIAL_PDF_SHA256 = "079226cf086a83e12bd6f6789a6929c713cc2a1e8769925286567629242295c5"
CANONICAL_PATH = (
    ROOT
    / "output/gas-shunin-kou/questions_json/2024/25_verified_publication"
    / "2024_verified_publication.json"
)
CORRECTION_PATH = (
    ROOT
    / "output/gas-shunin-kou/questions_json/2024/24_questionIssueCorrections"
    / "ui-qir-20260730213120-718f82e86e_official-a227f152a94ed421_a227f152a94ed421.json"
)
DEFAULT_RECEIPT = (
    ROOT
    / "document/temporary"
    / "2026-09-05_gas_shunin_2024_q20_image_sync_receipt.json"
)
EXPECTED_IDENTITY = {
    "qualificationId": "gas-shunin-kou",
    "examYear": 2024,
    "originalQuestionId": ORIGINAL_QUESTION_ID,
    "isDeleted": False,
    "isChoiceOnly": False,
}
READ_FIELDS = tuple(EXPECTED_IDENTITY) + (
    "questionImageUrls",
    "updatedAt",
    "updatedById",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_document(question_id: str, document: dict[str, Any]) -> str:
    identity = {field: document.get(field) for field in EXPECTED_IDENTITY}
    if identity != EXPECTED_IDENTITY:
        raise ValueError(
            f"{question_id} のidentityが事前条件と一致しません: "
            f"{json.dumps(identity, ensure_ascii=False, sort_keys=True)}"
        )

    current = document.get("questionImageUrls")
    if current is None or current == []:
        return "needs_update"
    if current == [IMAGE_URL]:
        return "already_applied"
    raise ValueError(
        f"{question_id} のquestionImageUrlsが"
        "修正前・修正後のどちらとも一致しません。"
        "別の画像を上書きしないため停止します。"
    )


def sync_metadata_is_current(document: dict[str, Any]) -> bool:
    updated_at = document.get("updatedAt")
    return (
        isinstance(updated_at, datetime)
        and updated_at >= MIN_SYNC_UPDATED_AT
        and document.get("updatedById") == UPDATED_BY_ID
    )


def receipt_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def verify_canonical(path: Path = CANONICAL_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list):
        raise ValueError(f"公開正本のquestionsが配列ではありません: {path}")
    targets = {
        str(question.get("questionId")): question
        for question in questions
        if isinstance(question, dict)
        and str(question.get("questionId")) in QUESTION_IDS
    }
    if set(targets) != set(QUESTION_IDS):
        raise ValueError("公開正本に対象5件がそろっていません。")
    for question_id in QUESTION_IDS:
        if targets[question_id].get("questionImageUrls") != [IMAGE_URL]:
            raise ValueError(
                f"公開正本のquestionImageUrlsが期待値と一致しません: {question_id}"
            )
    return payload


def fetch_image_sha256() -> str:
    with urllib.request.urlopen(IMAGE_URL, timeout=20) as response:
        content = response.read()
    return hashlib.sha256(content).hexdigest()


def build_receipt(
    *,
    project_id: str,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    statuses: dict[str, str],
    needs_writes: dict[str, bool],
) -> dict[str, Any]:
    expected_after = {
        "questionImageUrls": [IMAGE_URL],
        "updatedById": UPDATED_BY_ID,
        "updatedAtMinimum": MIN_SYNC_UPDATED_AT.isoformat(),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": (
            "applied"
            if any(needs_writes.values())
            else "already_applied"
        ),
        "executedAt": utc_now(),
        "projectId": project_id,
        "target": {
            "collection": "questions",
            "questionIds": list(QUESTION_IDS),
            "originalQuestionId": ORIGINAL_QUESTION_ID,
            "identity": EXPECTED_IDENTITY,
        },
        "evidence": {
            "officialPdfUrl": OFFICIAL_PDF_URL,
            "officialPdfSha256": OFFICIAL_PDF_SHA256,
            "locator": "PDF 34ページ（冊子33ページ）、消費機器 問20",
            "imageStoragePath": IMAGE_STORAGE_PATH,
            "imageUrl": IMAGE_URL,
            "imageSha256": IMAGE_SHA256,
            "canonicalPath": str(CANONICAL_PATH.relative_to(ROOT)),
            "correctionPath": str(CORRECTION_PATH.relative_to(ROOT)),
        },
        "change": {
            "allowedFields": ["questionImageUrls", "updatedAt", "updatedById"],
            "after": expected_after,
            "afterHash": canonical_hash(expected_after),
            "perQuestion": {
                question_id: {
                    "statusBefore": statuses[question_id],
                    "needsWrite": needs_writes[question_id],
                    "beforeFieldExisted": before[question_id]["fieldExisted"],
                    "before": before[question_id]["questionImageUrls"],
                    "beforeUpdatedAt": before[question_id]["updatedAt"],
                    "beforeUpdatedById": before[question_id]["updatedById"],
                    "beforeUpdateTime": before[question_id]["updateTime"],
                }
                for question_id in QUESTION_IDS
            },
        },
        "readback": {
            "allMatchExpectedAfter": all(
                after[question_id]["questionImageUrls"] == [IMAGE_URL]
                and after[question_id]["updatedById"] == UPDATED_BY_ID
                and datetime.fromisoformat(after[question_id]["updatedAt"])
                >= MIN_SYNC_UPDATED_AT
                for question_id in QUESTION_IDS
            ),
            "perQuestion": after,
        },
        "rollback": {
            "allowedFields": ["questionImageUrls", "updatedAt", "updatedById"],
            "perQuestion": {
                question_id: {
                    "questionImageUrls": {
                        "deleteField": not before[question_id]["fieldExisted"],
                        "value": before[question_id]["questionImageUrls"],
                    },
                    "updatedAt": before[question_id]["updatedAt"],
                    "updatedById": before[question_id]["updatedById"],
                    "preconditionUpdateTime": after[question_id]["updateTime"],
                }
                for question_id in QUESTION_IDS
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--credentials-json", type=Path)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verify_canonical()
    actual_image_hash = fetch_image_sha256()
    if actual_image_hash != IMAGE_SHA256:
        raise ValueError(
            "Storage画像のSHA-256が公式切り出し画像と一致しません: "
            f"expected={IMAGE_SHA256} actual={actual_image_hash}"
        )

    initialize_firebase_app(
        project_id=args.project_id,
        credentials_json=args.credentials_json,
    )

    from firebase_admin import firestore
    from google.cloud.firestore_v1 import LastUpdateOption

    db = firestore.client()
    refs = [db.collection("questions").document(question_id) for question_id in QUESTION_IDS]
    snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(READ_FIELDS))
    }
    if set(snapshots) != set(QUESTION_IDS):
        raise ValueError("Firestore readbackに対象5件がそろっていません。")

    before: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    needs_writes: dict[str, bool] = {}
    for question_id in QUESTION_IDS:
        snapshot = snapshots[question_id]
        if not snapshot.exists or snapshot.update_time is None:
            raise ValueError(f"対象レコードが存在しません: {question_id}")
        document = snapshot.to_dict() or {}
        statuses[question_id] = verify_document(question_id, document)
        needs_writes[question_id] = (
            statuses[question_id] == "needs_update"
            or not sync_metadata_is_current(document)
        )
        before[question_id] = {
            "fieldExisted": "questionImageUrls" in document,
            "questionImageUrls": document.get("questionImageUrls"),
            "updatedAt": receipt_value(document.get("updatedAt")),
            "updatedById": document.get("updatedById"),
            "updateTime": snapshot.update_time.isoformat(),
        }

    preview = {
        "status": "needs_update"
        if any(needs_writes.values())
        else "already_applied",
        "projectId": args.project_id,
        "questionIds": list(QUESTION_IDS),
        "questionImageUrls": [IMAGE_URL],
        "perQuestion": statuses,
        "needsWrite": needs_writes,
        "imageSha256": actual_image_hash,
    }
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    batch = db.batch()
    update_count = 0
    for question_id in QUESTION_IDS:
        if not needs_writes[question_id]:
            continue
        snapshot = snapshots[question_id]
        batch.update(
            snapshot.reference,
            {
                "questionImageUrls": [IMAGE_URL],
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "updatedById": UPDATED_BY_ID,
            },
            option=LastUpdateOption(snapshot.update_time),
        )
        update_count += 1
    if update_count:
        batch.commit()

    after_snapshots = {
        snapshot.id: snapshot
        for snapshot in db.get_all(refs, field_paths=list(READ_FIELDS))
    }
    after: dict[str, dict[str, Any]] = {}
    for question_id in QUESTION_IDS:
        snapshot = after_snapshots.get(question_id)
        if snapshot is None or not snapshot.exists or snapshot.update_time is None:
            raise ValueError(f"Firestore readbackに失敗しました: {question_id}")
        document = snapshot.to_dict() or {}
        if verify_document(question_id, document) != "already_applied":
            raise ValueError(f"Firestore readbackが期待値と一致しません: {question_id}")
        if not sync_metadata_is_current(document):
            raise ValueError(
                f"Firestore同期metadataが期待値と一致しません: {question_id}"
            )
        after[question_id] = {
            "questionImageUrls": document.get("questionImageUrls"),
            "updatedAt": receipt_value(document.get("updatedAt")),
            "updatedById": document.get("updatedById"),
            "identityMatches": all(
                document.get(field) == value
                for field, value in EXPECTED_IDENTITY.items()
            ),
            "updateTime": snapshot.update_time.isoformat(),
        }

    receipt = build_receipt(
        project_id=args.project_id,
        before=before,
        after=after,
        statuses=statuses,
        needs_writes=needs_writes,
    )
    if not receipt["readback"]["allMatchExpectedAfter"]:
        raise ValueError("Firestore readbackが修正後の期待値と一致しません。")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
