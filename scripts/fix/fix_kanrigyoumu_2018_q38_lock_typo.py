#!/usr/bin/env python3
"""管理業務主任者2018年度問38イの「鏡」を「錠」へ限定修正する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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


SCHEMA_VERSION = "kanrigyoumu-2018-q38-lock-typo-repair/v1"
QUESTION_ID = "condominiummanager-C-13027"
OFFICIAL_PDF_URL = (
    "https://www.kanrikyo.or.jp/kanri/mondaiseikai/pdf/h30_0.pdf"
)
OFFICIAL_PDF_SHA256 = (
    "db3a5ded8ecd0b69c284dcc5349afad59f73aae7354aab31f1a247d5b52ecaf7"
)
DEFAULT_RECEIPT = (
    ROOT
    / "document"
    / "temporary"
    / "2026-09-01_kanrigyoumu_2018_q38_lock_typo_firestore_receipt.json"
)

IDENTITY_FIELDS = {
    "qualificationId": "condominiummanager",
    "examYear": 2018,
    "questionNumber": 38,
    "originalQuestionId": "kanrigyo-2018-38",
    "isDeleted": False,
    "isChoiceOnly": False,
}
BEFORE_FIELDS = {
    "originalQuestionChoiceText": "玄関扉は、鏡及び内部塗装部分のみが専有部分である。",
    "knowledgeText": "玄関扉は、鏡及び内部塗装部分を専有部分とする〈標規（単）7条2項2号〉。",
    "questionText": (
        "専有部分の範囲に関する次の記述のうち、標準管理規約に従い、"
        "正しいかどうか答えよ。（改題）\n"
        "[quote]玄関扉は、鏡及び内部塗装部分のみが専有部分である。[/quote]"
    ),
    "explanationText": (
        "標規（単）7条2項2号｜理由：玄関扉は鏡及び内部塗装部分を"
        "専有部分とすると定めるため"
    ),
}
AFTER_FIELDS = {
    field: value.replace("鏡", "錠") for field, value in BEFORE_FIELDS.items()
}
READ_FIELDS = tuple(IDENTITY_FIELDS) + tuple(BEFORE_FIELDS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_snapshot(document: dict[str, Any]) -> str:
    identity = {field: document.get(field) for field in IDENTITY_FIELDS}
    if identity != IDENTITY_FIELDS:
        raise ValueError(
            "対象レコードのidentityが事前条件と一致しません: "
            f"{json.dumps(identity, ensure_ascii=False, sort_keys=True)}"
        )

    current = {field: document.get(field) for field in BEFORE_FIELDS}
    if current == BEFORE_FIELDS:
        return "needs_update"
    if current == AFTER_FIELDS:
        return "already_applied"
    raise ValueError(
        "対象4 fieldが修正前値・修正後値のどちらとも一致しません。"
        "同時変更又は別内容を上書きしないため停止します。"
    )


def build_receipt(
    *,
    project_id: str,
    status: str,
    before_update_time: str,
    after_update_time: str,
    readback: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "executedAt": utc_now(),
        "projectId": project_id,
        "target": {
            "collection": "questions",
            "questionId": QUESTION_ID,
            "identity": IDENTITY_FIELDS,
        },
        "evidence": {
            "officialPdfUrl": OFFICIAL_PDF_URL,
            "officialPdfSha256": OFFICIAL_PDF_SHA256,
            "locator": "PDF 29ページ（紙面27ページ）、問38、設問イ",
            "verifiedText": "玄関扉は、錠及び内部塗装部分のみが専有部分である。",
            "ruleLocator": "マンション標準管理規約（単棟型）第7条第2項第2号",
        },
        "change": {
            "allowedFields": list(AFTER_FIELDS),
            "before": BEFORE_FIELDS,
            "after": AFTER_FIELDS,
            "beforeHash": canonical_hash(BEFORE_FIELDS),
            "afterHash": canonical_hash(AFTER_FIELDS),
        },
        "precondition": {"beforeUpdateTime": before_update_time},
        "readback": {
            "afterUpdateTime": after_update_time,
            "fields": {field: readback.get(field) for field in AFTER_FIELDS},
            "matchesExpectedAfter": all(
                readback.get(field) == value for field, value in AFTER_FIELDS.items()
            ),
            "identityMatches": all(
                readback.get(field) == value
                for field, value in IDENTITY_FIELDS.items()
            ),
        },
        "rollback": {
            "allowedFields": list(BEFORE_FIELDS),
            "values": BEFORE_FIELDS,
            "preconditionUpdateTime": after_update_time,
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
    initialize_firebase_app(
        project_id=args.project_id,
        credentials_json=args.credentials_json,
    )

    from firebase_admin import firestore
    from google.cloud.firestore_v1 import LastUpdateOption

    db = firestore.client()
    ref = db.collection("questions").document(QUESTION_ID)
    before = ref.get(field_paths=list(READ_FIELDS))
    if not before.exists:
        raise ValueError(f"対象レコードが存在しません: {QUESTION_ID}")

    before_document = before.to_dict() or {}
    state = verify_snapshot(before_document)
    preview = {
        "status": state,
        "projectId": args.project_id,
        "questionId": QUESTION_ID,
        "beforeUpdateTime": before.update_time.isoformat(),
        "changes": {
            field: {"before": BEFORE_FIELDS[field], "after": AFTER_FIELDS[field]}
            for field in AFTER_FIELDS
        },
    }
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    if state == "needs_update":
        ref.update(
            AFTER_FIELDS,
            option=LastUpdateOption(before.update_time),
        )

    after = ref.get(field_paths=list(READ_FIELDS))
    after_document = after.to_dict() or {}
    verify_snapshot(after_document)
    if any(after_document.get(field) != value for field, value in AFTER_FIELDS.items()):
        raise ValueError("Firestore readbackが修正後の期待値と一致しません。")

    receipt = build_receipt(
        project_id=args.project_id,
        status="applied" if state == "needs_update" else "already_applied",
        before_update_time=before.update_time.isoformat(),
        after_update_time=after.update_time.isoformat(),
        readback=after_document,
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
