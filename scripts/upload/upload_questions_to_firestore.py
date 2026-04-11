import json
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

PROJECT_ID = DEFAULT_PROJECT_ID
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
BATCH_SIZE = 500  # Firestoreバッチ書き込みの上限


def init_firestore(credentials_json: Path | None = None):
    """Firestoreを初期化"""
    initialize_firebase_app(project_id=PROJECT_ID, credentials_json=credentials_json)
    return firestore.client()


def build_doc_data(question: dict, now: datetime) -> dict:
    """問題データからFirestoreドキュメントデータを構築"""
    doc_data = {
        "questionSetId": question.get("questionSetId", ""),
        "listGroupId": question.get("listGroupId", ""),
        "originalQuestionId": question.get("originalQuestionId", ""),
        "originalQuestionBodyText": question.get("originalQuestionBodyText", ""),
        "questionBodyText": question.get("questionBodyText", ""),
        "originalQuestionChoiceText": question.get("originalQuestionChoiceText", ""),
        "questionText": question.get("questionText", ""),
        "questionType": question.get("questionType", ""),
        "correctChoiceText": str(question.get("correctChoiceText", "")),
        "explanationText": question.get("explanationText", ""),
        "examYear": question.get("examYear", ""),
        "examSource": question.get("examSource", ""),
        "isOfficial": question.get("isOfficial", False),
        "isDeleted": question.get("isDeleted", False),
        "isChoiceOnly": question.get("isChoiceOnly", False),
        "isGroupable": question.get("isGroupable", False),
        "updatedAt": now,
        "updatedById": UPDATED_BY_ID,
    }
    # オプションフィールド
    for opt_key in ("knowledgeText", "questionTags", "questionImageUrls", "importKey"):
        if opt_key in question:
            doc_data[opt_key] = question[opt_key]
    return doc_data


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

    print(f"合計 {len(questions)} 件の質問をアップロードします...")

    if dry_run:
        print("[DRY RUN] 実際のアップロードは行いません。")
        for q in questions[:5]:
            print(f"  - {q.get('questionId')}: {q.get('questionText', '')[:50]}...")
        return

    # Firestore初期化
    db = init_firestore(credentials_json)
    now = datetime.now()

    success_count = 0
    error_count = 0
    batch_num = 0

    # BATCH_SIZE 件ずつバッチ書き込み
    for chunk_start in range(0, len(questions), BATCH_SIZE):
        chunk = questions[chunk_start:chunk_start + BATCH_SIZE]
        batch = db.batch()
        chunk_valid = 0

        for question in chunk:
            question_id = question.get("questionId")
            if not question_id:
                print(f"Error: questionId が見つかりません: {question}")
                error_count += 1
                continue

            doc_ref = db.collection("questions").document(question_id)
            doc_data = build_doc_data(question, now)
            # merge=True: 既存フィールド（createdAt等）を上書きしない
            batch.set(doc_ref, doc_data, merge=True)
            chunk_valid += 1

        try:
            batch.commit()
            batch_num += 1
            success_count += chunk_valid
            end_idx = min(chunk_start + BATCH_SIZE, len(questions))
            print(f"バッチ {batch_num} 完了: {end_idx}/{len(questions)} 件")
        except Exception as e:
            print(f"Error: バッチ {batch_num + 1} のコミット失敗: {e}")
            error_count += chunk_valid

    print(f"\n完了: 成功 {success_count} 件, エラー {error_count} 件")


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
    main()
