#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from scripts.upload.firebase_credentials import (  # noqa: E402
        DEFAULT_PROJECT_ID,
        initialize_firebase_app,
    )
else:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    from scripts.upload.firebase_credentials import DEFAULT_PROJECT_ID, initialize_firebase_app


UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
BATCH_SIZE = 500


def is_list_group_dir(path: Path) -> bool:
    return path.is_dir() and path.name.isdigit()


def latest_upload_file_for_list_group(upload_dir: Path, list_group_id: str) -> Path | None:
    candidates = sorted(upload_dir.glob(f"{list_group_id}_firestore_*.json"))
    if candidates:
        return candidates[-1]
    legacy = upload_dir / f"{list_group_id}_firestore.json"
    return legacy if legacy.exists() else None


def latest_upload_files(questions_json_dir: Path) -> list[Path]:
    upload_dir = questions_json_dir / "upload_to_firestore"
    if not upload_dir.is_dir():
        raise FileNotFoundError(f"upload_to_firestore not found: {upload_dir}")

    files: list[Path] = []
    missing: list[str] = []
    for group_dir in sorted(questions_json_dir.iterdir()):
        if not is_list_group_dir(group_dir):
            continue
        latest = latest_upload_file_for_list_group(upload_dir, group_dir.name)
        if latest is None:
            missing.append(group_dir.name)
        else:
            files.append(latest)
    if missing:
        raise FileNotFoundError(
            "latest upload json not found for list_group_id: " + ", ".join(missing)
        )
    return files


def load_local_question_ids(files: list[Path]) -> set[str]:
    ids: set[str] = set()
    for path in files:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        questions = payload.get("questions", []) if isinstance(payload, dict) else []
        for question in questions:
            if not isinstance(question, dict):
                continue
            question_id = str(question.get("questionId") or "").strip()
            if question_id:
                ids.add(question_id)
    return ids


def init_firestore(credentials_json: Path | None = None):
    initialize_firebase_app(project_id=DEFAULT_PROJECT_ID, credentials_json=credentials_json)
    from firebase_admin import firestore

    return firestore.client()


def fetch_active_question_docs(db, qualification: str) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    query = db.collection("questions").where("qualificationId", "==", qualification)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        if data.get("isDeleted") is True:
            continue
        docs[snapshot.id] = data
    return docs


def build_report(
    *,
    qualification: str,
    upload_files: list[Path],
    local_ids: set[str],
    active_docs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stale_ids = sorted(set(active_docs) - local_ids)
    stale_questions = [
        {
            "questionId": question_id,
            "questionSetId": active_docs[question_id].get("questionSetId"),
            "examYear": active_docs[question_id].get("examYear"),
            "questionText": str(active_docs[question_id].get("questionText") or "")[:160],
        }
        for question_id in stale_ids
    ]
    return {
        "qualification": qualification,
        "uploadFiles": [str(path) for path in upload_files],
        "localQuestionIdCount": len(local_ids),
        "activeFirestoreQuestionCount": len(active_docs),
        "staleQuestionCount": len(stale_ids),
        "staleQuestions": stale_questions,
    }


def write_report(path: Path, report: dict[str, Any], *, execute: bool) -> None:
    payload = {
        "mode": "execute" if execute else "dry-run",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        **report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def mark_stale_deleted(db, stale_ids: list[str]) -> int:
    from firebase_admin import firestore

    now = datetime.now(timezone.utc)
    updated = 0
    for start in range(0, len(stale_ids), BATCH_SIZE):
        batch = db.batch()
        chunk = stale_ids[start : start + BATCH_SIZE]
        for question_id in chunk:
            ref = db.collection("questions").document(question_id)
            batch.set(
                ref,
                {
                    "isDeleted": True,
                    "deletedAt": now,
                    "updatedAt": now,
                    "updatedById": UPDATED_BY_ID,
                },
                merge=True,
            )
        batch.commit()
        updated += len(chunk)
        print(f"[OK] tombstone batch: {updated}/{len(stale_ids)}")
    return updated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="latest upload JSON に存在しない active questions を isDeleted=true にする",
    )
    parser.add_argument("qualification", help="資格コード。例: 2nd-class-kenchikushi")
    parser.add_argument(
        "--questions-json-dir",
        type=Path,
        default=None,
        help="questions_json ディレクトリ。省略時は output/<qualification>/questions_json",
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス。未指定時は secure.env / GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    parser.add_argument("--report", type=Path, default=None, help="結果 JSON の保存先")
    parser.add_argument("--execute", action="store_true", help="実際に tombstone を書き込む")
    parser.add_argument(
        "--expected-stale-count",
        type=int,
        default=None,
        help="stale 件数がこの値と違う場合は中断する",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    questions_json_dir = (
        args.questions_json_dir.expanduser().resolve()
        if args.questions_json_dir is not None
        else (ROOT_DIR / "output" / args.qualification / "questions_json").resolve()
    )
    upload_files = latest_upload_files(questions_json_dir)
    local_ids = load_local_question_ids(upload_files)

    db = init_firestore(args.credentials_json)
    active_docs = fetch_active_question_docs(db, args.qualification)
    report = build_report(
        qualification=args.qualification,
        upload_files=upload_files,
        local_ids=local_ids,
        active_docs=active_docs,
    )
    stale_ids = [item["questionId"] for item in report["staleQuestions"]]

    print(
        "[SUMMARY] "
        f"qualification={args.qualification} uploadFiles={len(upload_files)} "
        f"localQuestionIds={report['localQuestionIdCount']} "
        f"activeFirestoreQuestions={report['activeFirestoreQuestionCount']} "
        f"staleQuestions={report['staleQuestionCount']}"
    )
    for item in report["staleQuestions"][:50]:
        print(
            "[STALE] "
            f"{item['questionId']} qset={item.get('questionSetId')} "
            f"examYear={item.get('examYear')} text={item.get('questionText')}"
        )
    if len(report["staleQuestions"]) > 50:
        print(f"[STALE] ... and {len(report['staleQuestions']) - 50} more")

    if args.report:
        write_report(args.report, report, execute=args.execute)
        print(f"[REPORT] {args.report}")

    if args.expected_stale_count is not None and report["staleQuestionCount"] != args.expected_stale_count:
        print(
            "[ERROR] stale count mismatch: "
            f"expected={args.expected_stale_count} actual={report['staleQuestionCount']}",
            file=sys.stderr,
        )
        return 2

    if not args.execute:
        print("[DRY RUN] Firestore には書き込んでいません。")
        return 0

    updated = mark_stale_deleted(db, stale_ids)
    print(f"[DONE] tombstoned={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
