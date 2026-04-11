import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from shutil import copyfile
from typing import Any

import firebase_admin
from firebase_admin import firestore

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.questions_json_paths import list_group_ids_in_base_dir
from scripts.upload.firebase_credentials import (
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)

PROJECT_ID = DEFAULT_PROJECT_ID
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
CREATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"


def init_firestore(credentials_json: Path | None = None):
    initialize_firebase_app(project_id=PROJECT_ID, credentials_json=credentials_json)
    return firestore.client()


def load_category_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_key_values(obj: Any, key_name: str) -> list:
    """Recursively collect values for keys matching `key_name` in a JSON-like structure."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key_name:
                found.append(v)
            found.extend(find_key_values(v, key_name))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_key_values(item, key_name))
    return found


def is_archived_path(path: Path) -> bool:
    return "old" in path.parts


def gather_source_files(source: str) -> list[Path]:
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"source が見つかりません: {src}")
    if src.is_file():
        return [src]
    return [p for p in src.rglob("*.json") if not is_archived_path(p)]


def find_latest_upload_file(upload_dir: Path, list_group_id: str) -> Path | None:
    candidates = sorted(upload_dir.glob(f"{list_group_id}_firestore_*.json"))
    if not candidates:
        legacy = upload_dir / f"{list_group_id}_firestore.json"
        if legacy.exists():
            return legacy
        return None
    return candidates[-1]


def gather_all_list_group_latest_files(questions_json_dir: Path) -> list[Path]:
    upload_dir = questions_json_dir / "upload_to_firestore"
    if not upload_dir.exists():
        raise FileNotFoundError(f"upload_to_firestore が見つかりません: {upload_dir}")

    list_group_ids = list_group_ids_in_base_dir(questions_json_dir)
    if not list_group_ids:
        raise FileNotFoundError(f"list_group_id ディレクトリが見つかりません: {questions_json_dir}")

    files: list[Path] = []
    missing: list[str] = []
    for list_group_id in list_group_ids:
        latest = find_latest_upload_file(upload_dir, list_group_id)
        if latest is None:
            missing.append(list_group_id)
            continue
        files.append(latest)

    print(f"検出したlist_group_id数: {len(list_group_ids)}")
    print(f"集計対象ファイル数: {len(files)}")
    if missing:
        print("Warning: upload_to_firestore が見つからない list_group_id:")
        print("  " + ", ".join(missing))

    return files


def aggregate_question_set_counts(files: list[Path]) -> Counter:
    counter: Counter = Counter()
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception as e:
            print(f"Warning: failed to load {p}: {e}", file=sys.stderr)
            continue
        qset_vals = find_key_values(obj, "questionSetId")
        for value in qset_vals:
            if value is not None and str(value) != "":
                counter[str(value)] += 1
    return counter


def apply_counts_to_category(data: dict, counts: Counter) -> None:
    for qset in data.get("questionSets", []):
        qset_id = qset.get("questionSetId")
        qset_count = int(counts.get(str(qset_id), 0))
        qset["questionCount"] = qset_count

    folder_counts: dict[str, int] = {}
    for qset in data.get("questionSets", []):
        folder_id = qset.get("folderId")
        qset_count = int(qset.get("questionCount", 0) or 0)
        folder_counts[folder_id] = folder_counts.get(folder_id, 0) + qset_count

    for folder in data.get("folders", []):
        folder_id = folder.get("folderId")
        folder["questionCount"] = int(folder_counts.get(folder_id, 0))


def delete_folders_and_question_sets(db, data):
    for folder in data.get("folders", []):
        folder_id = folder.get("folderId")
        try:
            db.collection("folders").document(folder_id).delete()
            print(f"Deleted folder: {folder_id}")
        except Exception as e:
            print(f"Warning: failed to delete folder {folder_id}: {e}")
    for qset in data.get("questionSets", []):
        qset_id = qset.get("questionSetId")
        try:
            db.collection("questionSets").document(qset_id).delete()
            print(f"Deleted questionSet: {qset_id}")
        except Exception as e:
            print(f"Warning: failed to delete questionSet {qset_id}: {e}")


def upsert_folder(db, folder, now, license_name: str):
    folder_id = folder.get("folderId")
    doc_ref = db.collection("folders").document(folder_id)
    created_at = now
    try:
        existing_doc = doc_ref.get()
        if existing_doc.exists:
            existing_data = existing_doc.to_dict()
            if "createdAt" in existing_data:
                created_at = existing_data["createdAt"]
    except Exception as e:
        print(f"Warning: failed to fetch existing folder {folder_id}: {e}")
    doc_data = {
        "name": folder["name"],
        # "description": folder.get("description", ""),
        "isDeleted": False,
        "isPublic": True,
        "isOfficial": True,
        "licenseName": license_name,
        "questionCount": folder.get("questionCount", 0),
        "createdById": CREATED_BY_ID,
        "createdAt": created_at,
        "updatedById": UPDATED_BY_ID,
        "updatedAt": now,
    }
    doc_ref.set(doc_data, merge=True)
    print(f"Uploaded folder: {folder_id}")


def upsert_question_set(db, qset, now):
    qset_id = qset.get("questionSetId")
    doc_ref = db.collection("questionSets").document(qset_id)
    created_at = now
    try:
        existing_doc = doc_ref.get()
        if existing_doc.exists:
            existing_data = existing_doc.to_dict()
            if "createdAt" in existing_data:
                created_at = existing_data["createdAt"]
    except Exception as e:
        print(f"Warning: failed to fetch existing questionSet {qset_id}: {e}")
    doc_data = {
        "name": qset["name"],
        # "description": qset.get("description", ""),
        "isDeleted": False,
        "isOfficial": True,
        "folderId": qset.get("folderId"),
        "questionCount": qset.get("questionCount", 0),
        "createdById": CREATED_BY_ID,
        "createdAt": created_at,
        "updatedById": UPDATED_BY_ID,
        "updatedAt": now,
    }
    doc_ref.set(doc_data, merge=True)
    print(f"Uploaded questionSet: {qset_id}")


def main():
    parser = argparse.ArgumentParser(description="category.jsonをFirestoreにアップロード/削除")
    parser.add_argument("category_json", type=str, help="category.jsonのパス")
    parser.add_argument("--source", type=str, default=None, help="問題データのJSONファイルまたはディレクトリ（questionCount集計対象）")
    parser.add_argument("--all-list-groups", action="store_true", help="資格配下の全list_group_idの最新upload_to_firestoreをまとめて集計")
    parser.add_argument("--questions-json-dir", type=str, default=None, help="questions_jsonディレクトリのパス（--all-list-groups時に使用）")
    parser.add_argument("--licenseName", "-l", required=True, help="アップロードするフォルダに設定する licenseName を指定（必須）")
    parser.add_argument("--upload", action="store_true", help="実際に Firestore にアップロードする。指定しない場合は dry-run を行う")
    parser.add_argument("--delete", action="store_true", help="Firestoreからcategory.json記載のfolders/questionSetsを物理削除する")
    parser.add_argument("--write-category", action="store_true", help="集計した questionCount を category.json に書き戻す（バックアップを作成）")
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス。未指定時は GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    args = parser.parse_args()

    # Init DB only when needed (avoid accidental network calls during dry-run)
    db = None
    if args.upload or args.delete:
        db = init_firestore(args.credentials_json)

    data = load_category_json(args.category_json)

    if args.source and args.all_list_groups:
        print("エラー: --source と --all-list-groups は同時に指定できません", file=sys.stderr)
        return

    source_files: list[Path] = []
    if args.all_list_groups:
        if args.questions_json_dir:
            questions_json_dir = Path(args.questions_json_dir).expanduser().resolve()
        else:
            questions_json_dir = Path(args.category_json).expanduser().resolve().parents[1] / "questions_json"
        source_files = gather_all_list_group_latest_files(questions_json_dir)
    elif args.source:
        source_files = gather_source_files(args.source)

    if source_files:
        counts = aggregate_question_set_counts(source_files)
        apply_counts_to_category(data, counts)
        print(f"questionCountを集計して反映しました（対象ファイル数: {len(source_files)}）")

    if args.delete:
        delete_folders_and_question_sets(db, data)
        print("削除完了")
        return


    now = datetime.now()

    # category.jsonの値をそのまま使ってアップロード
    # フォルダ登録
    for folder in data.get("folders", []):
        folder_id = folder.get("folderId")
        question_count = folder.get("questionCount", 0)
        # Skip upload/printing for empty categories
        if question_count == 0:
            if args.upload and db is not None:
                print(f"Skipping upload of folder {folder_id}: questionCount is 0")
            else:
                print(f"Skipping folder {folder_id}: questionCount=0")
            continue

        if args.upload and db is not None:
            upsert_folder(db, {**folder, "questionCount": question_count}, now, args.licenseName)
        else:
            print(f"Folder {folder_id}: questionCount -> {question_count}")

    # 問題集登録
    for qset in data.get("questionSets", []):
        qset_id = qset.get("questionSetId")
        question_count = qset.get("questionCount", 0)
        # Skip uploading questionSets with zero questions
        if question_count == 0:
            if args.upload and db is not None:
                print(f"Skipping upload of questionSet {qset_id}: questionCount is 0")
            else:
                print(f"Skipping questionSet {qset_id}: questionCount=0")
            continue

        if args.upload and db is not None:
            upsert_question_set(db, {**qset, "questionCount": question_count}, now)
        else:
            print(f"QuestionSet {qset_id}: questionCount -> {question_count}")

    # Write back to category.json if requested (create timestamped backup)
    if args.write_category:
        try:
            src_path = Path(args.category_json)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_dir = src_path.parent / "old"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{src_path.name}.bak_{timestamp}"
            copyfile(src_path, backup_path)
            with open(src_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Wrote updated category.json and created backup: {backup_path}")
        except Exception as e:
            print(f"Failed to write category.json: {e}", file=sys.stderr)

    print("完了")

if __name__ == "__main__":
    main()
