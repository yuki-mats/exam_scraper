#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.repaso_firestore_schema import QUESTION_SCHEMA  # noqa: E402
from scripts.scrape.common import load_local_secure_env  # noqa: E402
from scripts.upload.firebase_credentials import DEFAULT_PROJECT_ID  # noqa: E402

FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
DEFAULT_LICENSE_NAME = "ガス主任技術者"
DEFAULT_FOLDER_PREFIX = "chiefgasengineerlicense-A-"
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "output" / "gas-shunin-kou" / "firestore_snapshot"
IN_QUERY_LIMIT = 30


def utc_now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def firestore_value_to_python(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "timestampValue" in value:
        return value["timestampValue"]
    if "nullValue" in value:
        return None
    if "arrayValue" in value:
        return [
            firestore_value_to_python(item)
            for item in value["arrayValue"].get("values", [])
        ]
    if "mapValue" in value:
        return {
            key: firestore_value_to_python(item)
            for key, item in value["mapValue"].get("fields", {}).items()
        }
    if "referenceValue" in value:
        return value["referenceValue"]
    if "bytesValue" in value:
        return value["bytesValue"]
    if "geoPointValue" in value:
        point = value["geoPointValue"]
        return {
            "latitude": point.get("latitude"),
            "longitude": point.get("longitude"),
        }
    return value


def firestore_document_id(document: dict[str, Any]) -> str:
    return str(document["name"]).rsplit("/", 1)[-1]


def decode_firestore_document(document: dict[str, Any]) -> dict[str, Any]:
    decoded = {
        key: firestore_value_to_python(value)
        for key, value in document.get("fields", {}).items()
    }
    decoded["_id"] = firestore_document_id(document)
    return decoded


def raw_document_record(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "_id": firestore_document_id(document),
        "name": document.get("name"),
        "createTime": document.get("createTime"),
        "updateTime": document.get("updateTime"),
        "fields": document.get("fields", {}),
        "decoded": decode_firestore_document(document),
    }


def get_access_token(credentials_json: Path | None) -> str:
    scopes = [FIRESTORE_SCOPE]
    if credentials_json is not None:
        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_json),
            scopes=scopes,
        )
    else:
        credentials, _ = google.auth.default(scopes=scopes)
    credentials.refresh(Request())
    return str(credentials.token)


def request_json(
    *,
    project_id: str,
    token: str,
    payload: dict[str, Any],
) -> Any:
    url = (
        "https://firestore.googleapis.com/v1/projects/"
        f"{project_id}/databases/(default)/documents:runQuery"
    )
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Firestore API error: {exc.code} {exc.reason}: {error_body}"
        ) from exc
    return json.loads(body) if body else []


def run_query_raw(
    *,
    project_id: str,
    token: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = request_json(project_id=project_id, token=token, payload=payload)
    return [
        row["document"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("document"), dict)
    ]


def firestore_string(value: str) -> dict[str, str]:
    return {"stringValue": value}


def equal_filter(field_path: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "fieldFilter": {
            "field": {"fieldPath": field_path},
            "op": "EQUAL",
            "value": value,
        }
    }


def in_filter(field_path: str, values: list[str]) -> dict[str, Any]:
    return {
        "fieldFilter": {
            "field": {"fieldPath": field_path},
            "op": "IN",
            "value": {
                "arrayValue": {
                    "values": [firestore_string(value) for value in values]
                }
            },
        }
    }


def query_payload(collection_id: str, where: dict[str, Any]) -> dict[str, Any]:
    return {
        "structuredQuery": {
            "from": [{"collectionId": collection_id}],
            "where": where,
        }
    }


def fetch_equal(
    *,
    project_id: str,
    token: str,
    collection_id: str,
    field_path: str,
    value: str,
) -> list[dict[str, Any]]:
    payload = query_payload(collection_id, equal_filter(field_path, firestore_string(value)))
    return run_query_raw(project_id=project_id, token=token, payload=payload)


def chunked(values: list[str], chunk_size: int = IN_QUERY_LIMIT) -> list[list[str]]:
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def fetch_in(
    *,
    project_id: str,
    token: str,
    collection_id: str,
    field_path: str,
    values: list[str],
) -> list[dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for batch in chunked(values):
        if not batch:
            continue
        payload = query_payload(collection_id, in_filter(field_path, batch))
        for document in run_query_raw(project_id=project_id, token=token, payload=payload):
            documents[firestore_document_id(document)] = document
    return [documents[key] for key in sorted(documents)]


def is_active_display_question(question: dict[str, Any]) -> bool:
    return question.get("isDeleted") is False and question.get("isChoiceOnly") is False


def reconstruct_question(decoded_question: dict[str, Any]) -> dict[str, Any]:
    question = {
        key: value
        for key, value in decoded_question.items()
        if key in QUESTION_SCHEMA.allowed_fields
    }
    question["questionId"] = str(decoded_question["_id"])
    return question


def reconstruct_category(
    *,
    decoded_folders: list[dict[str, Any]],
    decoded_question_sets: list[dict[str, Any]],
    decoded_questions: list[dict[str, Any]],
    generated_at: str,
    license_name: str,
    folder_prefix: str,
) -> dict[str, Any]:
    active_display_counts_by_qset = Counter(
        str(question.get("questionSetId"))
        for question in decoded_questions
        if is_active_display_question(question)
    )
    active_display_counts_by_folder: Counter[str] = Counter()
    for question_set in decoded_question_sets:
        qset_id = str(question_set["_id"])
        folder_id = str(question_set.get("folderId") or "")
        active_display_counts_by_folder[folder_id] += active_display_counts_by_qset[qset_id]

    folders = []
    for folder in sorted(decoded_folders, key=lambda item: item["_id"]):
        folder_id = str(folder["_id"])
        count = int(active_display_counts_by_folder[folder_id])
        folders.append(
            {
                "folderId": folder_id,
                "name": folder.get("name", ""),
                "questionCount": count,
                "isDeleted": count <= 0,
                "isPublic": bool(folder.get("isPublic", True)),
                "isOfficial": bool(folder.get("isOfficial", True)),
                "licenseName": folder.get("licenseName", license_name),
                "qualificationId": folder.get("qualificationId", ""),
                "updatedAt": generated_at,
            }
        )

    question_sets = []
    for question_set in sorted(
        decoded_question_sets,
        key=lambda item: (str(item.get("folderId") or ""), str(item["_id"])),
    ):
        qset_id = str(question_set["_id"])
        count = int(active_display_counts_by_qset[qset_id])
        question_sets.append(
            {
                "questionSetId": qset_id,
                "folderId": question_set.get("folderId", ""),
                "name": question_set.get("name", ""),
                "questionCount": count,
                "isDeleted": count <= 0,
                "isOfficial": bool(question_set.get("isOfficial", True)),
                "qualificationId": question_set.get("qualificationId", ""),
                "updatedAt": generated_at,
            }
        )

    return {
        "metadata": {
            "source": "firestore",
            "generatedAt": generated_at,
            "licenseName": license_name,
            "folderIdPrefix": folder_prefix,
            "questionCountBasis": "isDeleted == false && isChoiceOnly == false",
        },
        "folders": folders,
        "questionSets": question_sets,
    }


def count_by_field(records: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    counts = Counter(str(record.get(field_name)) for record in records)
    return dict(sorted(counts.items()))


def build_validation_report(
    *,
    decoded_folders: list[dict[str, Any]],
    decoded_question_sets: list[dict[str, Any]],
    decoded_questions: list[dict[str, Any]],
    license_name: str,
    folder_prefix: str,
    generated_at: str,
) -> dict[str, Any]:
    qsets_by_id = {str(question_set["_id"]): question_set for question_set in decoded_question_sets}
    folder_by_id = {str(folder["_id"]): folder for folder in decoded_folders}

    active_questions = [
        question for question in decoded_questions if question.get("isDeleted") is False
    ]
    active_display_questions = [
        question for question in decoded_questions if is_active_display_question(question)
    ]
    active_choice_only_questions = [
        question
        for question in decoded_questions
        if question.get("isDeleted") is False and question.get("isChoiceOnly") is True
    ]
    deleted_questions = [
        question for question in decoded_questions if question.get("isDeleted") is True
    ]

    active_display_counts_by_qset = Counter(
        str(question.get("questionSetId")) for question in active_display_questions
    )
    active_display_counts_by_folder: Counter[str] = Counter()
    for qset_id, count in active_display_counts_by_qset.items():
        question_set = qsets_by_id.get(qset_id)
        if question_set is not None:
            active_display_counts_by_folder[str(question_set.get("folderId") or "")] += count

    question_set_count_mismatches = []
    for qset_id, question_set in sorted(
        qsets_by_id.items(),
        key=lambda item: (str(item[1].get("folderId") or ""), item[0]),
    ):
        source_count = int(question_set.get("questionCount") or 0)
        reconstructed_count = int(active_display_counts_by_qset[qset_id])
        if source_count != reconstructed_count:
            question_set_count_mismatches.append(
                {
                    "questionSetId": qset_id,
                    "folderId": question_set.get("folderId"),
                    "name": question_set.get("name"),
                    "sourceQuestionCount": source_count,
                    "reconstructedQuestionCount": reconstructed_count,
                }
            )

    folder_count_mismatches = []
    for folder_id, folder in sorted(folder_by_id.items()):
        source_count = int(folder.get("questionCount") or 0)
        reconstructed_count = int(active_display_counts_by_folder[folder_id])
        if source_count != reconstructed_count:
            folder_count_mismatches.append(
                {
                    "folderId": folder_id,
                    "name": folder.get("name"),
                    "sourceQuestionCount": source_count,
                    "reconstructedQuestionCount": reconstructed_count,
                }
            )

    question_id_missing = []
    question_id_mismatches = []
    for question in sorted(decoded_questions, key=lambda item: str(item["_id"])):
        existing_question_id = question.get("questionId")
        if existing_question_id is None:
            question_id_missing.append(str(question["_id"]))
        elif str(existing_question_id) != str(question["_id"]):
            question_id_mismatches.append(
                {
                    "documentId": question["_id"],
                    "questionId": existing_question_id,
                }
            )

    return {
        "generatedAt": generated_at,
        "filters": {
            "folders": {
                "licenseName": license_name,
                "folderIdPrefix": folder_prefix,
            },
            "questionSets": "folderId IN selected folder ids",
            "questions": "questionSetId IN selected questionSet ids",
            "activeDisplayQuestionCountBasis": "isDeleted == false && isChoiceOnly == false",
        },
        "summary": {
            "folderCount": len(decoded_folders),
            "questionSetCount": len(decoded_question_sets),
            "rawQuestionCount": len(decoded_questions),
            "activeQuestionCount": len(active_questions),
            "activeDisplayQuestionCount": len(active_display_questions),
            "activeChoiceOnlyQuestionCount": len(active_choice_only_questions),
            "deletedQuestionCount": len(deleted_questions),
            "sourceFolderQuestionCountSum": sum(int(folder.get("questionCount") or 0) for folder in decoded_folders),
            "sourceQuestionSetQuestionCountSum": sum(
                int(question_set.get("questionCount") or 0)
                for question_set in decoded_question_sets
            ),
            "reconstructedQuestionSetQuestionCountSum": len(active_display_questions),
            "questionIdFieldMissingCount": len(question_id_missing),
            "questionIdFieldMismatchCount": len(question_id_mismatches),
        },
        "distributions": {
            "rawQuestionsByExamYear": count_by_field(decoded_questions, "examYear"),
            "activeDisplayQuestionsByExamYear": count_by_field(active_display_questions, "examYear"),
            "rawQuestionsByQuestionType": count_by_field(decoded_questions, "questionType"),
            "activeDisplayQuestionsByQuestionType": count_by_field(active_display_questions, "questionType"),
            "activeDisplayQuestionsByQuestionSetId": dict(sorted(active_display_counts_by_qset.items())),
            "activeDisplayQuestionsByFolderId": dict(sorted(active_display_counts_by_folder.items())),
        },
        "countMismatches": {
            "folders": folder_count_mismatches,
            "questionSets": question_set_count_mismatches,
        },
        "questionIdPolicy": {
            "reconstructedQuestionId": "Firestore document id",
            "originalQuestionId": "preserve existing originalQuestionId field",
            "missingQuestionIdFieldDocumentIds": question_id_missing,
            "mismatchingQuestionIdFields": question_id_mismatches,
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="甲種ガス主任技術者の Firestore live snapshot を read-only 取得する",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("FIREBASE_PROJECT_ID", DEFAULT_PROJECT_ID),
        help=f"Firebase project id (default: {DEFAULT_PROJECT_ID})",
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON。未指定時は GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    parser.add_argument(
        "--license-name",
        default=DEFAULT_LICENSE_NAME,
        help=f"folders.licenseName filter (default: {DEFAULT_LICENSE_NAME})",
    )
    parser.add_argument(
        "--folder-prefix",
        default=DEFAULT_FOLDER_PREFIX,
        help=f"甲種判定用 folderId prefix (default: {DEFAULT_FOLDER_PREFIX})",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"snapshot output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="出力ディレクトリ名に使う timestamp。未指定時は UTC now。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="同名 snapshot ディレクトリが存在する場合に上書きする。",
    )
    return parser.parse_args(argv)


def export_snapshot(args: argparse.Namespace) -> Path:
    load_local_secure_env()
    project_id = os.environ.get("FIREBASE_PROJECT_ID", args.project_id)
    token = get_access_token(args.credentials_json)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    timestamp = args.timestamp or utc_now_label()
    snapshot_dir = args.output_root.expanduser().resolve() / timestamp
    if snapshot_dir.exists() and not args.overwrite:
        raise FileExistsError(f"snapshot directory already exists: {snapshot_dir}")

    folder_documents = fetch_equal(
        project_id=project_id,
        token=token,
        collection_id="folders",
        field_path="licenseName",
        value=args.license_name,
    )
    folder_records = [
        raw_document_record(document)
        for document in folder_documents
        if firestore_document_id(document).startswith(args.folder_prefix)
    ]
    folder_records = sorted(folder_records, key=lambda record: record["_id"])
    decoded_folders = [record["decoded"] for record in folder_records]
    folder_ids = [str(folder["_id"]) for folder in decoded_folders]

    question_set_documents = fetch_in(
        project_id=project_id,
        token=token,
        collection_id="questionSets",
        field_path="folderId",
        values=folder_ids,
    )
    question_set_records = sorted(
        [raw_document_record(document) for document in question_set_documents],
        key=lambda record: (
            str(record["decoded"].get("folderId") or ""),
            str(record["_id"]),
        ),
    )
    decoded_question_sets = [record["decoded"] for record in question_set_records]
    question_set_ids = [str(question_set["_id"]) for question_set in decoded_question_sets]

    question_documents = fetch_in(
        project_id=project_id,
        token=token,
        collection_id="questions",
        field_path="questionSetId",
        values=question_set_ids,
    )
    question_records = sorted(
        [raw_document_record(document) for document in question_documents],
        key=lambda record: str(record["_id"]),
    )
    decoded_questions = [record["decoded"] for record in question_records]

    reconstructed_category = reconstruct_category(
        decoded_folders=decoded_folders,
        decoded_question_sets=decoded_question_sets,
        decoded_questions=decoded_questions,
        generated_at=generated_at,
        license_name=args.license_name,
        folder_prefix=args.folder_prefix,
    )
    reconstructed_questions = {
        "metadata": {
            "source": "firestore",
            "generatedAt": generated_at,
            "questionIdPolicy": "questionId is Firestore document id",
            "originalQuestionIdPolicy": "preserve existing originalQuestionId field",
        },
        "questions": [reconstruct_question(question) for question in decoded_questions],
        "total_count": len(decoded_questions),
    }
    validation_report = build_validation_report(
        decoded_folders=decoded_folders,
        decoded_question_sets=decoded_question_sets,
        decoded_questions=decoded_questions,
        license_name=args.license_name,
        folder_prefix=args.folder_prefix,
        generated_at=generated_at,
    )

    write_json(snapshot_dir / "raw" / "folders.json", {"documents": folder_records})
    write_json(snapshot_dir / "raw" / "questionSets.json", {"documents": question_set_records})
    write_json(snapshot_dir / "raw" / "questions.json", {"documents": question_records})
    write_json(snapshot_dir / "reconstructed" / "category.json", reconstructed_category)
    write_json(snapshot_dir / "reconstructed" / "questions.json", reconstructed_questions)
    write_json(snapshot_dir / "validation_report.json", validation_report)

    summary = validation_report["summary"]
    print(f"snapshot_dir: {snapshot_dir}")
    print(f"folders: {summary['folderCount']}")
    print(f"questionSets: {summary['questionSetCount']}")
    print(f"raw questions: {summary['rawQuestionCount']}")
    print(f"active display questions: {summary['activeDisplayQuestionCount']}")
    print(
        "count mismatches:"
        f" folders={len(validation_report['countMismatches']['folders'])}"
        f" questionSets={len(validation_report['countMismatches']['questionSets'])}"
    )
    return snapshot_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    export_snapshot(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
