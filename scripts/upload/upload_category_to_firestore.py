import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from shutil import copyfile

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.questions_json_paths import list_group_ids_in_base_dir
from scripts.common.question_counting import analyze_question_file
from scripts.upload.firebase_credentials import (
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)
from scripts.common.repaso_firestore_schema import (
    validate_folder_doc,
    validate_question_set_doc,
)

PROJECT_ID = DEFAULT_PROJECT_ID
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
CREATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
FOLDER_REFERENCE_FIELDS = ("canonicalFolderId", "sourceSharedFolderId")
QUESTION_SET_REFERENCE_FIELDS = (
    "canonicalFolderId",
    "canonicalQuestionSetId",
    "sourceSharedFolderId",
    "sourceSharedQuestionSetId",
)
QUALIFICATION_NAME_BY_CODE = {
    "2nd-class-kenchikushi": "二級建築士",
    "kaigofukushi": "介護福祉士",
    "kounin-shinrishi": "公認心理師",
    "kougai": "公害防止管理者",
    "kyusuikouji-shunin": "給水装置工事主任技術者",
    "mecnet-kokushi": "医師",
}


def copy_reference_fields(doc_data: dict, source_data: dict, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        if field_name in source_data:
            doc_data[field_name] = source_data[field_name]


def infer_qualification_id_from_path(category_json_path: str) -> str:
    """
    output/<qualification>/category/category.json から qualificationId を推定する。
    推定できない場合は空文字を返す（この場合は strict validate で落ちる）。
    """
    path = Path(category_json_path).expanduser().resolve()
    parts = list(path.parts)
    for idx, part in enumerate(parts):
        if part == "output" and idx + 1 < len(parts):
            return str(parts[idx + 1])
    # フォールバック: output 配下でない場合は親ディレクトリ名を使う
    try:
        if path.name == "category.json" and path.parent.name == "category":
            return str(path.parents[1].name)
    except Exception:
        pass
    return ""


def init_firestore(credentials_json: Path | None = None):
    from firebase_admin import firestore

    initialize_firebase_app(project_id=PROJECT_ID, credentials_json=credentials_json)
    return firestore.client()


def load_category_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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


def resolve_license_name(
    category_json_path: str,
    explicit_license_name: str | None,
    category_data: dict | None = None,
) -> str:
    if explicit_license_name:
        return explicit_license_name

    metadata_license_name = (category_data or {}).get("metadata", {}).get("licenseName")
    if isinstance(metadata_license_name, str) and metadata_license_name.strip():
        return metadata_license_name.strip()

    category_path = Path(category_json_path).expanduser().resolve()
    qualification_code = ""
    try:
        qualification_code = category_path.parents[1].name
    except Exception:
        qualification_code = ""

    if qualification_code in QUALIFICATION_NAME_BY_CODE:
        return QUALIFICATION_NAME_BY_CODE[qualification_code]

    config_path = REPO_ROOT / "config" / "scrape_presets.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            preset = config.get(qualification_code)
            qualification_name = preset.get("qualification_name") if isinstance(preset, dict) else None
            if isinstance(qualification_name, str) and qualification_name.strip():
                return qualification_name.strip()
        except Exception:
            pass

    if qualification_code:
        return qualification_code
    return "unknown"


def aggregate_question_set_counts(files: list[Path]):
    counter: Counter = Counter()
    for p in files:
        try:
            _, file_counter, _ = analyze_question_file(p)
        except Exception as e:
            print(f"Warning: failed to load {p}: {e}", file=sys.stderr)
            continue
        counter.update(file_counter)
    return counter


def apply_counts_to_category(data: dict, counts: Counter) -> None:
    for qset in data.get("questionSets", []):
        qset_id = qset.get("questionSetId")
        qset_count = int(counts.get(str(qset_id), 0))
        qset["questionCount"] = qset_count
        qset["isDeleted"] = qset_count <= 0

    folder_counts: dict[str, int] = {}
    for qset in data.get("questionSets", []):
        folder_id = qset.get("folderId")
        qset_count = int(qset.get("questionCount", 0) or 0)
        folder_counts[folder_id] = folder_counts.get(folder_id, 0) + qset_count

    for folder in data.get("folders", []):
        folder_id = folder.get("folderId")
        folder_count = int(folder_counts.get(folder_id, 0))
        folder["questionCount"] = folder_count
        folder["isDeleted"] = folder_count <= 0


def normalize_question_count(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def resolve_question_set_is_deleted(qset: dict) -> bool:
    if "isDeleted" in qset:
        return bool(qset.get("isDeleted"))
    return normalize_question_count(qset.get("questionCount", 0)) <= 0


def resolve_folder_is_deleted(folder: dict) -> bool:
    if "isDeleted" in folder:
        return bool(folder.get("isDeleted"))
    return normalize_question_count(folder.get("questionCount", 0)) <= 0


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


def upsert_folder(db, folder, now, license_name: str, qualification_id: str):
    folder_id = folder.get("folderId")
    doc_ref = db.collection("folders").document(folder_id)
    created_at = now
    existing_data: dict | None = None
    try:
        existing_doc = doc_ref.get()
        if existing_doc.exists:
            existing_data = existing_doc.to_dict() or {}
            if "createdAt" in existing_data:
                created_at = existing_data["createdAt"]
    except Exception as e:
        print(f"Warning: failed to fetch existing folder {folder_id}: {e}")
        existing_data = None
    doc_data = {
        "name": folder["name"],
        # "description": folder.get("description", ""),
        "isDeleted": resolve_folder_is_deleted(folder),
        "isPublic": True,
        "isOfficial": True,
        "aggregatedQuestionTags": [],
        "licenseName": license_name,
        "qualificationId": qualification_id,
        "questionCount": folder.get("questionCount", 0),
        "createdById": CREATED_BY_ID,
        "createdAt": created_at,
    }
    copy_reference_fields(doc_data, folder, FOLDER_REFERENCE_FIELDS)
    # updatedAt は「差分が発生して変更される時のみ」更新する（ユーザー要望）
    if existing_data is not None:
        comparable_keys = (
            "name",
            "isDeleted",
            "isPublic",
            "isOfficial",
            "licenseName",
            "questionCount",
            *(field_name for field_name in FOLDER_REFERENCE_FIELDS if field_name in doc_data),
        )
        changed = any(existing_data.get(k) != doc_data.get(k) for k in comparable_keys)
        if not changed:
            print(f"Skip folder (no diff): {folder_id}")
            return

    doc_data["updatedById"] = UPDATED_BY_ID
    doc_data["updatedAt"] = now
    validate_folder_doc(doc_data, doc_id=str(folder_id))
    doc_ref.set(doc_data, merge=True)
    print(f"Uploaded folder: {folder_id}")


def upsert_question_set(db, qset, now, qualification_id: str):
    qset_id = qset.get("questionSetId")
    doc_ref = db.collection("questionSets").document(qset_id)
    created_at = now
    existing_data: dict | None = None
    try:
        existing_doc = doc_ref.get()
        if existing_doc.exists:
            existing_data = existing_doc.to_dict() or {}
            if "createdAt" in existing_data:
                created_at = existing_data["createdAt"]
    except Exception as e:
        print(f"Warning: failed to fetch existing questionSet {qset_id}: {e}")
        existing_data = None
    question_count = normalize_question_count(qset.get("questionCount", 0))
    doc_data = {
        "name": qset["name"],
        # "description": qset.get("description", ""),
        "isDeleted": resolve_question_set_is_deleted(qset),
        "isOfficial": True,
        "folderId": qset.get("folderId"),
        "qualificationId": qualification_id,
        "questionCount": question_count,
        "createdById": CREATED_BY_ID,
        "createdAt": created_at,
    }
    copy_reference_fields(doc_data, qset, QUESTION_SET_REFERENCE_FIELDS)
    # updatedAt は「差分が発生して変更される時のみ」更新する（ユーザー要望）
    if existing_data is not None:
        comparable_keys = (
            "name",
            "isDeleted",
            "isOfficial",
            "folderId",
            "questionCount",
            *(field_name for field_name in QUESTION_SET_REFERENCE_FIELDS if field_name in doc_data),
        )
        changed = any(existing_data.get(k) != doc_data.get(k) for k in comparable_keys)
        if not changed:
            print(f"Skip questionSet (no diff): {qset_id}")
            return

    doc_data["updatedById"] = UPDATED_BY_ID
    doc_data["updatedAt"] = now
    validate_question_set_doc(doc_data, doc_id=str(qset_id))
    doc_ref.set(doc_data, merge=True)
    print(f"Uploaded questionSet: {qset_id}")


def main():
    parser = argparse.ArgumentParser(description="category.jsonをFirestoreにアップロード/削除")
    parser.add_argument("category_json", type=str, help="category.jsonのパス")
    parser.add_argument("--source", type=str, default=None, help="問題データのJSONファイルまたはディレクトリ（questionCount集計対象）")
    parser.add_argument("--all-list-groups", action="store_true", help="資格配下の全list_group_idの最新upload_to_firestoreをまとめて集計")
    parser.add_argument("--questions-json-dir", type=str, default=None, help="questions_jsonディレクトリのパス（--all-list-groups時に使用）")
    parser.add_argument(
        "--licenseName",
        "-l",
        required=False,
        default=None,
        help="アップロードするフォルダに設定する licenseName（未指定時は category.json のパスから推定）",
    )
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

    # licenseName: 未指定なら category.json のパス（output/<qual>/category/category.json）から推定
    license_name = resolve_license_name(args.category_json, args.licenseName, data)
    qualification_id = infer_qualification_id_from_path(args.category_json)

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
        if args.upload and db is not None:
            upsert_folder(
                db,
                {**folder, "questionCount": question_count},
                now,
                license_name,
                qualification_id,
            )
        else:
            # dry-run でも repaso schema の必須キーを満たす doc_data を構築して検証する
            doc_data = {
                "name": folder.get("name", ""),
                "isDeleted": resolve_folder_is_deleted(folder),
                "isPublic": True,
                "isOfficial": True,
                "aggregatedQuestionTags": [],
                "licenseName": license_name,
                "qualificationId": qualification_id,
                "questionCount": int(question_count or 0),
                "createdById": CREATED_BY_ID,
                "createdAt": now,
                "updatedById": UPDATED_BY_ID,
                "updatedAt": now,
            }
            copy_reference_fields(doc_data, folder, FOLDER_REFERENCE_FIELDS)
            validate_folder_doc(doc_data, doc_id=str(folder_id))
            print(f"Folder {folder_id}: questionCount -> {question_count}")

    # 問題集登録
    for qset in data.get("questionSets", []):
        qset_id = qset.get("questionSetId")
        question_count = qset.get("questionCount", 0)
        if args.upload and db is not None:
            upsert_question_set(
                db,
                {**qset, "questionCount": question_count},
                now,
                qualification_id,
            )
        else:
            doc_data = {
                "name": qset.get("name", ""),
                "folderId": qset.get("folderId", ""),
                "qualificationId": qualification_id,
                "questionCount": int(question_count or 0),
                "isDeleted": resolve_question_set_is_deleted(qset),
                "isOfficial": True,
                "createdById": CREATED_BY_ID,
                "createdAt": now,
                "updatedById": UPDATED_BY_ID,
                "updatedAt": now,
            }
            copy_reference_fields(doc_data, qset, QUESTION_SET_REFERENCE_FIELDS)
            validate_question_set_doc(doc_data, doc_id=str(qset_id))
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
