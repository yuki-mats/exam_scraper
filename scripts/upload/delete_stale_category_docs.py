from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_PROJECT_ID = "repaso-rbaqy4"
DEFAULT_FIREBASE_TOOLS_CONFIG = (
    Path.home() / ".config" / "configstore" / "firebase-tools.json"
)


def load_category(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def collect_allowed_ids(category: dict[str, Any]) -> tuple[set[str], set[str]]:
    folder_ids = {
        str(folder["folderId"])
        for folder in category.get("folders", [])
        if folder.get("folderId")
    }
    question_set_ids = {
        str(question_set["questionSetId"])
        for question_set in category.get("questionSets", [])
        if question_set.get("questionSetId")
    }
    return folder_ids, question_set_ids


def load_access_token(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    token = data.get("tokens", {}).get("access_token")
    if not token:
        raise RuntimeError(
            f"Firebase CLI の access_token が見つかりません: {config_path}"
        )
    return str(token)


def field_value(field: dict[str, Any]) -> Any:
    if "stringValue" in field:
        return field["stringValue"]
    if "integerValue" in field:
        return int(field["integerValue"])
    if "doubleValue" in field:
        return float(field["doubleValue"])
    if "booleanValue" in field:
        return field["booleanValue"]
    if "timestampValue" in field:
        return field["timestampValue"]
    if "nullValue" in field:
        return None
    if "arrayValue" in field:
        return [field_value(v) for v in field["arrayValue"].get("values", [])]
    if "mapValue" in field:
        return {
            key: field_value(value)
            for key, value in field["mapValue"].get("fields", {}).items()
        }
    return field


def document_to_dict(document: dict[str, Any]) -> dict[str, Any]:
    doc = {
        key: field_value(value)
        for key, value in document.get("fields", {}).items()
    }
    doc["_id"] = document["name"].rsplit("/", 1)[-1]
    return doc


def request_json(
    *,
    url: str,
    method: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Firestore API error: {e.code} {e.reason}: {error_body}"
        ) from e
    return json.loads(body) if body else None


def run_query(
    *,
    project_id: str,
    token: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    url = (
        "https://firestore.googleapis.com/v1/projects/"
        f"{project_id}/databases/(default)/documents:runQuery"
    )
    rows = request_json(url=url, method="POST", token=token, payload=payload)
    return [
        document_to_dict(row["document"])
        for row in rows
        if isinstance(row, dict) and row.get("document")
    ]


def fetch_folders(
    *,
    project_id: str,
    token: str,
    license_name: str,
) -> list[dict[str, Any]]:
    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "folders"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "licenseName"},
                    "op": "EQUAL",
                    "value": {"stringValue": license_name},
                }
            },
            "orderBy": [{"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"}],
        }
    }
    return run_query(project_id=project_id, token=token, payload=payload)


def fetch_question_sets(
    *,
    project_id: str,
    token: str,
    folder_ids: list[str],
) -> list[dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for index in range(0, len(folder_ids), 30):
        batch = folder_ids[index : index + 30]
        payload = {
            "structuredQuery": {
                "from": [{"collectionId": "questionSets"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "folderId"},
                        "op": "IN",
                        "value": {
                            "arrayValue": {
                                "values": [{"stringValue": folder_id} for folder_id in batch]
                            }
                        },
                    }
                },
                "orderBy": [
                    {"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"}
                ],
            }
        }
        for doc in run_query(project_id=project_id, token=token, payload=payload):
            docs[doc["_id"]] = doc
    return sorted(docs.values(), key=lambda doc: (str(doc.get("folderId", "")), doc["_id"]))


def compute_delete_candidates(
    *,
    category: dict[str, Any],
    firestore_folders: list[dict[str, Any]],
    firestore_question_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_folders, allowed_question_sets = collect_allowed_ids(category)
    firestore_folder_ids = {doc["_id"] for doc in firestore_folders}
    firestore_question_set_ids = {doc["_id"] for doc in firestore_question_sets}

    extra_folders = sorted(
        [doc for doc in firestore_folders if doc["_id"] not in allowed_folders],
        key=lambda doc: doc["_id"],
    )
    extra_question_sets = sorted(
        [
            doc
            for doc in firestore_question_sets
            if doc["_id"] not in allowed_question_sets
        ],
        key=lambda doc: (str(doc.get("folderId", "")), doc["_id"]),
    )

    return {
        "summary": {
            "categoryFolderCount": len(allowed_folders),
            "categoryQuestionSetCount": len(allowed_question_sets),
            "firestoreFolderCount": len(firestore_folders),
            "firestoreQuestionSetCount": len(firestore_question_sets),
            "deleteCandidateFolderCount": len(extra_folders),
            "deleteCandidateQuestionSetCount": len(extra_question_sets),
            "missingFolderCount": len(allowed_folders - firestore_folder_ids),
            "missingQuestionSetCount": len(
                allowed_question_sets - firestore_question_set_ids
            ),
        },
        "deleteCandidateFolders": [
            {
                "folderId": doc["_id"],
                "name": doc.get("name"),
                "questionCount": doc.get("questionCount"),
                "isDeleted": doc.get("isDeleted"),
            }
            for doc in extra_folders
        ],
        "deleteCandidateQuestionSets": [
            {
                "questionSetId": doc["_id"],
                "folderId": doc.get("folderId"),
                "name": doc.get("name"),
                "questionCount": doc.get("questionCount"),
                "isDeleted": doc.get("isDeleted"),
            }
            for doc in extra_question_sets
        ],
        "missingFolders": sorted(allowed_folders - firestore_folder_ids),
        "missingQuestionSets": sorted(
            allowed_question_sets - firestore_question_set_ids
        ),
    }


def delete_document(
    *,
    project_id: str,
    token: str,
    collection_id: str,
    document_id: str,
) -> None:
    url = (
        "https://firestore.googleapis.com/v1/projects/"
        f"{project_id}/databases/(default)/documents/{collection_id}/{document_id}"
    )
    request_json(url=url, method="DELETE", token=token)


def print_report(report: dict[str, Any], *, execute: bool) -> None:
    summary = report["summary"]
    mode = "EXECUTE" if execute else "DRY RUN"
    print(f"=== {mode} ===")
    print(f"category folders: {summary['categoryFolderCount']}")
    print(f"firestore folders: {summary['firestoreFolderCount']}")
    print(f"delete candidate folders: {summary['deleteCandidateFolderCount']}")
    print(f"missing folders: {summary['missingFolderCount']}")
    print(f"category questionSets: {summary['categoryQuestionSetCount']}")
    print(f"firestore questionSets: {summary['firestoreQuestionSetCount']}")
    print(
        "delete candidate questionSets: "
        f"{summary['deleteCandidateQuestionSetCount']}"
    )
    print(f"missing questionSets: {summary['missingQuestionSetCount']}")

    print("\n[DELETE CANDIDATE] folders")
    if report["deleteCandidateFolders"]:
        for item in report["deleteCandidateFolders"]:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print("(none)")

    print("\n[DELETE CANDIDATE] questionSets")
    if report["deleteCandidateQuestionSets"]:
        for item in report["deleteCandidateQuestionSets"]:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print("(none)")

    print("\n[MISSING IN FIRESTORE] folders")
    if report["missingFolders"]:
        for folder_id in report["missingFolders"]:
            print(folder_id)
    else:
        print("(none)")

    print("\n[MISSING IN FIRESTORE] questionSets")
    if report["missingQuestionSets"]:
        for question_set_id in report["missingQuestionSets"]:
            print(question_set_id)
    else:
        print("(none)")


def write_report(path: Path, report: dict[str, Any], *, execute: bool) -> None:
    payload = {
        "mode": "execute" if execute else "dry-run",
        **report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="category.json に含まれない folder/questionSet を Firestore から削除する",
    )
    parser.add_argument("category_json", type=Path, help="category.json のパス")
    parser.add_argument(
        "--license-name",
        required=True,
        help="対象 folders の licenseName",
    )
    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help=f"Firebase project id (default: {DEFAULT_PROJECT_ID})",
    )
    parser.add_argument(
        "--firebase-tools-config",
        type=Path,
        default=DEFAULT_FIREBASE_TOOLS_CONFIG,
        help="firebase-tools.json のパス",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="dry run / execute 結果を JSON で保存するパス",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="実際に削除する。未指定時は dry run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    category = load_category(args.category_json)
    allowed_folders, _ = collect_allowed_ids(category)
    token = load_access_token(args.firebase_tools_config)

    firestore_folders = fetch_folders(
        project_id=args.project_id,
        token=token,
        license_name=args.license_name,
    )
    query_folder_ids = sorted(allowed_folders | {doc["_id"] for doc in firestore_folders})
    firestore_question_sets = fetch_question_sets(
        project_id=args.project_id,
        token=token,
        folder_ids=query_folder_ids,
    )

    report = compute_delete_candidates(
        category=category,
        firestore_folders=firestore_folders,
        firestore_question_sets=firestore_question_sets,
    )
    print_report(report, execute=args.execute)

    if args.report:
        write_report(args.report, report, execute=args.execute)
        print(f"\nreport saved: {args.report}")

    if not args.execute:
        print("\n削除は実行していません。実行する場合は --execute を付けてください。")
        return 0

    for item in report["deleteCandidateQuestionSets"]:
        delete_document(
            project_id=args.project_id,
            token=token,
            collection_id="questionSets",
            document_id=item["questionSetId"],
        )
        print(f"deleted questionSet: {item['questionSetId']}")

    for item in report["deleteCandidateFolders"]:
        delete_document(
            project_id=args.project_id,
            token=token,
            collection_id="folders",
            document_id=item["folderId"],
        )
        print(f"deleted folder: {item['folderId']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
