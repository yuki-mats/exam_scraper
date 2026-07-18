import json
import hashlib
import hmac
import os
import firebase_admin
from firebase_admin import firestore
from datetime import datetime
import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.upload.firebase_credentials import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)
from scripts.common.repaso_firestore_schema import validate_question_doc

PROJECT_ID = DEFAULT_PROJECT_ID
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
CREATED_BY_ID = UPDATED_BY_ID
BATCH_SIZE = 500  # Firestoreバッチ書き込みの上限
CONFIG_DOC_ID = "08zYvCuKUcvGTNYqehrm"
OFFICIAL_EXAM_YEARS_FIELD = "official_exam_years_by_qualification"


def init_firestore(credentials_json: Path | None = None):
    """Firestoreを初期化"""
    initialize_firebase_app(project_id=PROJECT_ID, credentials_json=credentials_json)
    return firestore.client()

def infer_qualification_id_from_json_path(json_file_path: str) -> str:
    """
    output/<qualification>/questions_json/... から qualificationId を推定する。
    推定できない場合は空文字を返す（strict validate で落ちる）。
    """
    path = Path(json_file_path).expanduser().resolve()
    parts = list(path.parts)
    for idx, part in enumerate(parts):
        if part == "output" and idx + 1 < len(parts):
            return str(parts[idx + 1])
    return ""


DOC_COMPARE_KEYS = (
    "questionSetId",
    "listGroupId",
    "originalQuestionId",
    "originalQuestionBodyText",
    "questionBodyText",
    "originalQuestionChoiceText",
    "originalQuestionChoiceImageUrls",
    "questionText",
    "questionType",
    "qualificationId",
    "correctChoiceText",
    "explanationText",
    "knowledgeText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "examYear",
    "examSource",
    "questionTags",
    "questionImageUrls",
    "importKey",
    "isOfficial",
    "isDeleted",
    "isChoiceOnly",
    "isGroupable",
)
EXISTING_DOC_FIELD_PATHS = tuple(dict.fromkeys((*DOC_COMPARE_KEYS, "createdAt", "createdById")))

_TRUTHY_CORRECT = {"正しい", "正解", "○", "〇", "true", "True", "TRUE"}
_TRUTHY_INCORRECT = {"間違い", "不正解", "誤り", "×", "false", "False", "FALSE"}


def _normalize_correct_choice_text(value: str) -> str:
    text = (value or "").strip()
    if text in _TRUTHY_CORRECT:
        return "正しい"
    if text in _TRUTHY_INCORRECT:
        return "間違い"
    return text


def validate_required_question_fields(questions: list[dict], source_label: str) -> None:
    """
    upload 前に最低限の整合性チェックを行う（例外時は ValueError）。

    - originalQuestionBodyText は必須（空白のみ不可）
    - true_false の grouped candidate（isChoiceOnly=false）では、
      originalQuestionChoiceText または originalQuestionChoiceImageUrls のいずれかが必須
    - correctChoiceText は "正解/不正解" 等を "正しい/間違い" に正規化
    - 同一 originalQuestionId の true_false grouped candidate が複数ある場合は isGroupable=true を付与
    """
    if not isinstance(questions, list):
        raise ValueError(f"questions is not a list: {source_label}")

    grouped_candidates_by_original: dict[str, list[dict]] = {}

    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("questionId") or "").strip()
        if not qid:
            raise ValueError(f"questionId is required: {source_label}")

        body = str(q.get("originalQuestionBodyText") or "")
        if not body.strip():
            raise ValueError(f"originalQuestionBodyText is required: {qid}")

        qset_id = str(q.get("questionSetId") or "").strip()
        if not qset_id:
            raise ValueError(f"questionSetId is required: {qid}")

        qtext = str(q.get("questionText") or "")
        if not qtext.strip():
            raise ValueError(f"questionText is required: {qid}")

        qtype = str(q.get("questionType") or "").strip()
        if not qtype:
            raise ValueError(f"questionType is required: {qid}")

        qual_id = q.get("qualificationId")
        if not isinstance(qual_id, str) or not qual_id.strip():
            raise ValueError(f"qualificationId is required: {qid}")

        tags = q.get("questionTags")
        if tags is None:
            q["questionTags"] = []
        elif not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise ValueError(f"questionTags must be list[str]: {qid}")

        if "correctChoiceText" in q:
            q["correctChoiceText"] = _normalize_correct_choice_text(str(q.get("correctChoiceText") or ""))

        question_type = str(q.get("questionType") or "")
        is_choice_only = bool(q.get("isChoiceOnly", False))
        if question_type == "true_false" and not is_choice_only:
            original_id = str(q.get("originalQuestionId") or "").strip()
            if original_id:
                grouped_candidates_by_original.setdefault(original_id, []).append(q)

            choice_text = str(q.get("originalQuestionChoiceText") or "")
            choice_images = q.get("originalQuestionChoiceImageUrls")
            has_images = isinstance(choice_images, list) and any(str(u).strip() for u in choice_images)
            if not choice_text.strip() and not has_images:
                raise ValueError(
                    f"originalQuestionChoiceText or originalQuestionChoiceImageUrls is required: {qid}"
                )

    for _, group in grouped_candidates_by_original.items():
        should_group = len(group) >= 2
        for q in group:
            q["isGroupable"] = should_group


def build_doc_data_base(question: dict) -> dict:
    """
    問題データからFirestoreドキュメントデータを構築（updatedAt/updatedByIdは除外）。
    """
    doc_data = {
        "questionSetId": question.get("questionSetId", ""),
        "listGroupId": question.get("listGroupId", ""),
        "originalQuestionId": question.get("originalQuestionId", ""),
        "originalQuestionBodyText": question.get("originalQuestionBodyText", ""),
        "questionBodyText": question.get("questionBodyText", ""),
        "originalQuestionChoiceText": question.get("originalQuestionChoiceText", ""),
        "questionText": question.get("questionText", ""),
        "questionType": question.get("questionType", ""),
        "qualificationId": question.get("qualificationId", ""),
        "correctChoiceText": str(question.get("correctChoiceText", "")),
        "explanationText": question.get("explanationText", ""),
        "examSource": question.get("examSource", ""),
        "questionTags": question.get("questionTags", []),
        "isOfficial": question.get("isOfficial", False),
        "isDeleted": question.get("isDeleted", False),
        "isChoiceOnly": question.get("isChoiceOnly", False),
        "isGroupable": question.get("isGroupable", False),
    }
    if question.get("examYear") not in (None, ""):
        doc_data["examYear"] = question["examYear"]
    # オプションフィールド
    for opt_key in (
        "knowledgeText",
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "lawReferences",
        "lawRevisionFacts",
        "isLawRelated",
        "lawGroundedExplanationNotNeeded",
        "questionImageUrls",
        "importKey",
        "originalQuestionChoiceImageUrls",
    ):
        if opt_key in question:
            doc_data[opt_key] = question[opt_key]
    return doc_data


def build_doc_data(question: dict, now: datetime) -> dict:
    """
    互換API: テスト/呼び出し側が期待する build_doc_data を残す。
    """
    doc_data = build_doc_data_base(question)
    doc_data.setdefault("createdAt", now)
    doc_data.setdefault("createdById", CREATED_BY_ID)
    doc_data["updatedAt"] = now
    doc_data["updatedById"] = UPDATED_BY_ID
    return doc_data


def top_level_merge_fields(doc_data: dict) -> list[str]:
    """指定フィールドの map 値を丸ごと置換し、未指定の既存フィールドは保持する。"""
    return list(doc_data.keys())


def fetch_existing_question_snapshots(db, doc_refs: list):
    get_all = getattr(db, "get_all", None)
    if callable(get_all):
        return list(get_all(doc_refs, field_paths=EXISTING_DOC_FIELD_PATHS))
    return [ref.get(field_paths=EXISTING_DOC_FIELD_PATHS) for ref in doc_refs]


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_json"):
        return _json_safe(value.to_json())
    return value


def firestore_live_fingerprint(document_ids: list[str], live_documents: dict) -> str:
    records = []
    for question_id in document_ids:
        document = live_documents.get(question_id)
        filtered = None
        if isinstance(document, dict):
            filtered = {
                field: _json_safe(document[field])
                for field in DOC_COMPARE_KEYS
                if field in document
            }
        records.append(
            {
                "questionId": question_id,
                "exists": document is not None,
                "document": filtered,
            }
        )
    value = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_expected_live_fingerprint(
    document_ids: list[str], snapshots: list, expected: str
) -> None:
    snapshots_by_id = {
        str(getattr(snapshot, "id", "")): snapshot
        for snapshot in snapshots
        if getattr(snapshot, "id", None)
    }
    if set(snapshots_by_id) != set(document_ids):
        raise RuntimeError("Firestore documentの確認結果が対象範囲と一致しません。")
    live_documents = {
        question_id: (snapshots_by_id[question_id].to_dict() or {})
        for question_id in document_ids
        if getattr(snapshots_by_id[question_id], "exists", False)
    }
    current = firestore_live_fingerprint(document_ids, live_documents)
    if not expected or not hmac.compare_digest(current, expected):
        raise RuntimeError("確認後にFirestore documentが更新されたため反映を停止しました。")


def add_guarded_question_write(batch, doc_ref, doc_data: dict, snapshot) -> None:
    """read後の同時更新を上書きしないFirestore writeをbatchへ追加する。"""
    if getattr(snapshot, "exists", False):
        update_time = getattr(snapshot, "update_time", None)
        if update_time is None:
            raise RuntimeError("既存documentのupdate_timeを取得できません。")
        batch.update(
            doc_ref,
            doc_data,
            option=firestore.LastUpdateOption(update_time),
        )
        return
    batch.create(doc_ref, doc_data)


def normalize_exam_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, float):
        year = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            year = int(text)
        except ValueError:
            return None
    else:
        return None
    if 1900 <= year <= 2100:
        return year
    return None


def collect_exam_years_by_qualification(questions: list[dict]) -> dict[str, list[int]]:
    years_by_qualification: dict[str, set[int]] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        qualification_id = str(question.get("qualificationId") or "").strip()
        if not qualification_id:
            continue
        year = normalize_exam_year(question.get("examYear"))
        if year is None:
            continue
        years_by_qualification.setdefault(qualification_id, set()).add(year)
    return {
        qualification_id: sorted(years, reverse=True)
        for qualification_id, years in sorted(years_by_qualification.items())
    }


def merge_official_exam_years_map(
    current: object,
    additions: dict[str, list[int]],
) -> dict[str, list[int]]:
    merged: dict[str, list[int]] = {}
    if isinstance(current, dict):
        for qualification_id, raw_years in current.items():
            if not isinstance(qualification_id, str) or not qualification_id.strip():
                continue
            years: set[int] = set()
            if isinstance(raw_years, list):
                for raw_year in raw_years:
                    year = normalize_exam_year(raw_year)
                    if year is not None:
                        years.add(year)
            if years:
                merged[qualification_id.strip()] = sorted(years, reverse=True)

    for qualification_id, years in additions.items():
        existing = set(merged.get(qualification_id, []))
        existing.update(years)
        if existing:
            merged[qualification_id] = sorted(existing, reverse=True)
    return dict(sorted(merged.items()))


def upsert_official_exam_years_manifest(db, additions: dict[str, list[int]]) -> None:
    if not additions:
        print("[SKIP] official exam years manifest: examYear が見つかりません")
        return
    doc_ref = db.collection("config").document(CONFIG_DOC_ID)
    existing_data: dict = {}
    try:
        snapshot = doc_ref.get()
        if getattr(snapshot, "exists", False):
            existing_data = snapshot.to_dict() or {}
    except Exception as exc:
        raise RuntimeError(f"config/{CONFIG_DOC_ID} の取得に失敗しました: {exc}") from exc

    current = existing_data.get(OFFICIAL_EXAM_YEARS_FIELD)
    merged = merge_official_exam_years_map(current, additions)
    if current == merged:
        print("[SKIP] official exam years manifest: 差分なし")
        return

    doc_ref.set({OFFICIAL_EXAM_YEARS_FIELD: merged}, merge=True)
    print(
        "[CONFIG] official exam years manifest updated: "
        + ", ".join(
            f"{qualification_id}={years}"
            for qualification_id, years in additions.items()
        )
    )


def upload_questions(
    json_file_path: str,
    dry_run: bool = False,
    credentials_json: Path | None = None,
):
    """JSONファイルからFirestoreに質問データをバッチアップロード"""

    # JSONファイルを読み込み
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = data.get("questions", [])
    if not questions:
        print("アップロードする質問データがありません。")
        return

    qualification_id = infer_qualification_id_from_json_path(json_file_path)
    for q in questions:
        if isinstance(q, dict):
            if not isinstance(q.get("qualificationId"), str) or not str(q.get("qualificationId") or "").strip():
                q["qualificationId"] = qualification_id
            if q.get("questionTags") is None:
                q["questionTags"] = []

    validate_required_question_fields(questions, str(json_file_path))
    exam_years_by_qualification = collect_exam_years_by_qualification(questions)

    print(f"合計 {len(questions)} 件の質問をアップロードします...")

    if dry_run:
        print("[DRY RUN] 実際のアップロードは行いません。")
        if exam_years_by_qualification:
            print(
                "[DRY RUN] official exam years manifest: "
                + ", ".join(
                    f"{qualification_id}={years}"
                    for qualification_id, years in exam_years_by_qualification.items()
                )
            )
        now = datetime.now()
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("questionId") or "").strip() or "unknown"
            base = build_doc_data_base(q)
            doc_data = dict(base)
            doc_data["createdAt"] = now
            doc_data["createdById"] = CREATED_BY_ID
            doc_data["updatedAt"] = now
            doc_data["updatedById"] = UPDATED_BY_ID
            validate_question_doc(doc_data, doc_id=qid)
        for q in questions[:5]:
            print(f"  - {q.get('questionId')}: {q.get('questionText', '')[:50]}...")
        return

    # Firestore初期化
    db = init_firestore(credentials_json)
    now = datetime.now()

    expected_live_hash = os.environ.get("QUESTION_PUBLISH_EXPECTED_LIVE_HASH", "").strip()
    guarded_snapshots_by_id = None
    if expected_live_hash:
        guarded_ids = [str(question.get("questionId") or "").strip() for question in questions]
        if not all(guarded_ids) or len(set(guarded_ids)) != len(guarded_ids):
            raise RuntimeError("公開対象のquestionIdが空又は重複しています。")
        guarded_refs = [
            db.collection("questions").document(question_id)
            for question_id in guarded_ids
        ]
        guarded_snapshots = fetch_existing_question_snapshots(db, guarded_refs)
        require_expected_live_fingerprint(
            guarded_ids,
            guarded_snapshots,
            expected_live_hash,
        )
        guarded_snapshots_by_id = {
            str(snapshot.id): snapshot for snapshot in guarded_snapshots
        }

    success_count = 0
    error_count = 0
    batch_num = 0
    skipped_count = 0

    # BATCH_SIZE 件ずつバッチ書き込み
    for chunk_start in range(0, len(questions), BATCH_SIZE):
        chunk = questions[chunk_start:chunk_start + BATCH_SIZE]
        batch = db.batch()
        chunk_valid = 0

        doc_refs = []
        doc_ref_by_id: dict[str, Any] = {}
        base_by_id: dict[str, dict] = {}

        for question in chunk:
            question_id = question.get("questionId")
            if not question_id:
                print(f"Error: questionId が見つかりません: {question}")
                error_count += 1
                continue

            doc_ref = db.collection("questions").document(question_id)
            doc_refs.append(doc_ref)
            doc_ref_by_id[question_id] = doc_ref
            base_by_id[question_id] = build_doc_data_base(question)

        # 既存ドキュメントをまとめて取得し、差分があるものだけ書き込む（updatedAtは差分がある時のみ更新）
        try:
            snapshots = (
                [guarded_snapshots_by_id[question_id] for question_id in base_by_id]
                if guarded_snapshots_by_id is not None
                else fetch_existing_question_snapshots(db, doc_refs)
            )
        except Exception as exc:
            # 「差分がある時のみ updatedAt 更新」を守るため、既存取得に失敗したら中断する
            raise RuntimeError(f"既存ドキュメントの取得に失敗しました: {exc}") from exc

        if doc_refs and not snapshots:
            raise RuntimeError("既存ドキュメントの取得結果が空です（想定外）")

        for snap in snapshots:
            qid = getattr(snap, "id", None)
            if not qid or qid not in base_by_id:
                continue
            new_base = base_by_id[qid]
            doc_ref = doc_ref_by_id[qid]

            exists = getattr(snap, "exists", False)
            if exists:
                existing = snap.to_dict() or {}
                changed = any(existing.get(k) != new_base.get(k) for k in DOC_COMPARE_KEYS if k in new_base)
                if not changed:
                    skipped_count += 1
                    continue
                created_at = existing.get("createdAt") or now
                created_by_id = existing.get("createdById") or CREATED_BY_ID
            else:
                created_at = now
                created_by_id = CREATED_BY_ID

            doc_data = dict(new_base)
            doc_data["createdAt"] = created_at
            doc_data["createdById"] = created_by_id
            doc_data["updatedAt"] = now
            doc_data["updatedById"] = UPDATED_BY_ID
            validate_question_doc(doc_data, doc_id=str(qid))
            add_guarded_question_write(batch, doc_ref, doc_data, snap)
            chunk_valid += 1

        try:
            if chunk_valid == 0:
                end_idx = min(chunk_start + BATCH_SIZE, len(questions))
                print(f"バッチ skip（差分なし）: {end_idx}/{len(questions)} 件")
            else:
                batch.commit()
                batch_num += 1
                success_count += chunk_valid
                end_idx = min(chunk_start + BATCH_SIZE, len(questions))
                print(f"バッチ {batch_num} 完了: {end_idx}/{len(questions)} 件 (updated={chunk_valid}, skipped_total={skipped_count})")
        except Exception as e:
            print(f"Error: バッチ {batch_num + 1} のコミット失敗: {e}")
            error_count += chunk_valid

    if error_count == 0:
        upsert_official_exam_years_manifest(db, exam_years_by_qualification)
    else:
        print("[SKIP] official exam years manifest: question upload error があるため更新しません")

    print(f"\n完了: 更新 {success_count} 件, スキップ {skipped_count} 件, エラー {error_count} 件")


def resolve_json_file_path(path_or_dir: str) -> Path:
    path = Path(path_or_dir)
    if path.is_file():
        return path
    if path.is_dir():
        candidates = sorted(path.glob("*_firestore*.json"))
        if not candidates:
            raise FileNotFoundError(f"Firestore JSONが見つかりません: {path}")
        return candidates[-1]
    raise FileNotFoundError(f"指定パスが見つかりません: {path}")


def main():
    parser = argparse.ArgumentParser(description="FirestoreにJSONデータをアップロード")
    parser.add_argument(
        "json_file",
        nargs="?",
        default="output/2nd-class-kenchikushi/questions_json/upload_to_firestore",
        help="アップロードするJSONファイルのパス（またはディレクトリ。ディレクトリ指定時は最新1件を使用）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際のアップロードを行わずに確認のみ"
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス。未指定時は GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    
    args = parser.parse_args()
    resolved_json_file = resolve_json_file_path(args.json_file)
    print(f"使用ファイル: {resolved_json_file}")
    upload_questions(str(resolved_json_file), args.dry_run, args.credentials_json)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # pragma: no cover
        raise SystemExit(0)
