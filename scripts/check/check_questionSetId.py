#!/usr/bin/env python3
"""
Check that all "questionSetId" values in a fixed JSON file exist in the category.json.
Also supports comparing question counts between original and fixed files.

Usage examples:
  # Check questionSetId existence
  python scripts/check/check_questionSetId.py \
    --category output/2nd-class-kenchikushi/category/category.json \
    --fixed output/2nd-class-kenchikushi/questions_json/85010/22_questionSetId_linked/question_85010_2_questionSetId_linked_YYYYMMDD_HHMM.json

  # Compare question counts between original and fixed files
  python scripts/check/check_questionSetId.py \
    --original output/2nd-class-kenchikushi/questions_json/85010/00_source/question_85010_2.json \
    --fixed output/2nd-class-kenchikushi/questions_json/85010/22_questionSetId_linked/question_85010_2_questionSetId_linked_YYYYMMDD_HHMM.json \
    --compare-count

  # Full check (both questionSetId and count comparison)
  python scripts/check/check_questionSetId.py \
    --category output/2nd-class-kenchikushi/category/category.json \
    --original output/2nd-class-kenchikushi/questions_json/85010/00_source/question_85010_2.json \
    --fixed output/2nd-class-kenchikushi/questions_json/85010/22_questionSetId_linked/question_85010_2_questionSetId_linked_YYYYMMDD_HHMM.json \
    --compare-count

The script searches the fixed JSON recursively for keys named exactly `questionSetId` and
compares their values against the `id` values present in `category.json`'s `questionSets` and `folders`.
Exits with code 0 when all IDs are present and counts match; returns non-zero when issues are found.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

# `python scripts/check/...` でも `python -m scripts.check...` でも動くようにする。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check.question_set_validation import collect_category_ids, load_json
from scripts.common.question_identity import review_question_id



# 指定ファイルのquestionsまたはquestion_bodies配下からquestionSetIdを抽出
def extract_question_set_ids(obj: Any) -> List[Any]:
    if isinstance(obj, list):
        return [q.get("questionSetId") for q in obj if isinstance(q, dict)]
    if not isinstance(obj, dict):
        return []
    if "questions" in obj and isinstance(obj["questions"], list):
        return [q.get("questionSetId") for q in obj["questions"] if isinstance(q, dict)]
    if "question_bodies" in obj and isinstance(obj["question_bodies"], list):
        return [q.get("questionSetId") for q in obj["question_bodies"] if isinstance(q, dict)]
    return []


def extract_missing_question_set_ids(obj: Any) -> List[str]:
    """
    questionSetIdが空またはNoneのquestion_url/original_question_idをリストで返す
    """
    missing_ids = []
    if isinstance(obj, list):
        for q in obj:
            if isinstance(q, dict):
                qsid = q.get("questionSetId")
                if qsid is None or qsid == "":
                    key = q.get("original_question_id") or q.get("question_url")
                    if key:
                        missing_ids.append(str(key))
        return missing_ids
    if not isinstance(obj, dict):
        return missing_ids
    # question_bodies
    if "question_bodies" in obj and isinstance(obj["question_bodies"], list):
        for q in obj["question_bodies"]:
            if isinstance(q, dict):
                qsid = q.get("questionSetId")
                if qsid is None or qsid == "":
                    key = q.get("original_question_id") or q.get("question_url")
                    if key:
                        missing_ids.append(str(key))
    # questions
    if "questions" in obj and isinstance(obj["questions"], list):
        for q in obj["questions"]:
            if isinstance(q, dict):
                qsid = q.get("questionSetId")
                if qsid is None or qsid == "":
                    key = q.get("original_question_id") or q.get("question_url")
                    if key:
                        missing_ids.append(str(key))
    return missing_ids
def get_questions_list(obj: Any) -> List[Dict[str, Any]]:
    """question_bodies または questions のリストを取得"""
    if isinstance(obj, list):
        return [q for q in obj if isinstance(q, dict)]
    if not isinstance(obj, dict):
        return []
    if "question_bodies" in obj and isinstance(obj["question_bodies"], list):
        return obj["question_bodies"]
    if "questions" in obj and isinstance(obj["questions"], list):
        return obj["questions"]
    return []


def detect_question_key(questions: List[Dict[str, Any]]) -> str:
    for q in questions:
        if q.get("original_question_id"):
            return "original_question_id"
    for q in questions:
        if q.get("public_question_id"):
            return "public_question_id"
    for q in questions:
        if q.get("question_url"):
            return "question_url"
    return "original_question_id"


def get_normalized_question_id(question: Dict[str, Any]) -> str | None:
    value = review_question_id(question)
    return value or None


def get_question_ids_by_field(obj: Any, field: str) -> Set[str]:
    questions = get_questions_list(obj)
    if field == "__normalized_question_id__":
        return {
            normalized_id
            for q in questions
            if isinstance(q, dict)
            for normalized_id in [get_normalized_question_id(q)]
            if normalized_id
        }
    return {str(q.get(field)) for q in questions if isinstance(q, dict) and q.get(field)}


def compare_question_counts(original: Any, fixed: Any) -> Tuple[int, int, List[str], List[str], str]:
    """
    元ファイルと修正ファイルの問題数を比較
    Returns: (original_count, fixed_count, missing_in_fixed, extra_in_fixed, key_field)
    """
    key_field = "__normalized_question_id__"
    original_ids = get_question_ids_by_field(original, key_field)
    fixed_ids = get_question_ids_by_field(fixed, key_field)
    
    missing_in_fixed = sorted(original_ids - fixed_ids)
    extra_in_fixed = sorted(fixed_ids - original_ids)
    
    return len(original_ids), len(fixed_ids), missing_in_fixed, extra_in_fixed, key_field


def get_question_details(obj: Any, key_field: str, ids: List[str]) -> List[Dict[str, str]]:
    """指定されたIDの問題詳細を取得"""
    questions = get_questions_list(obj)
    details = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        question_id = (
            get_normalized_question_id(q)
            if key_field == "__normalized_question_id__"
            else (str(q.get(key_field)) if q.get(key_field) else None)
        )
        if question_id in ids:
            details.append({
                "question_id": question_id or "",
                "questionLabel": str(q.get("questionLabel", "")),
                "questionBodyText": str(q.get("questionBodyText", ""))[:80] + "..."
            })
    return details



def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check questionSetId values against category.json (questions/question_bodies両対応)"
    )
    parser.add_argument("--category", help="Path to category.json (required for questionSetId check)")
    parser.add_argument("--original", help="Path to original JSON file (for count comparison)")
    parser.add_argument("--fixed", required=True, help="Path to JSON file to check (Firestore/従来形式どちらも可)")
    parser.add_argument("--show-sample", type=int, default=20, help="Max sample missing IDs to show")
    parser.add_argument("--compare-count", action="store_true", help="Compare question counts between original and fixed files")
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="category.json の questionSets[].questionSetId のみを有効IDとして扱う",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    exit_code = 0

    # Load fixed file (always required)
    try:
        fixed = load_json(args.fixed)
    except Exception as e:
        print(f"Failed to load JSON '{args.fixed}': {e}", file=sys.stderr)
        return 2

    # Count comparison mode
    if args.compare_count:
        if not args.original:
            print("[ERROR] --original is required when using --compare-count", file=sys.stderr)
            return 2
        
        try:
            original = load_json(args.original)
        except Exception as e:
            print(f"Failed to load original JSON '{args.original}': {e}", file=sys.stderr)
            return 2
        
        orig_count, fixed_count, missing_in_fixed, extra_in_fixed, key_field = compare_question_counts(original, fixed)
        
        print(f"\n=== Question Count Comparison ===")
        print(f"Original file: {args.original}")
        print(f"Fixed file: {args.fixed}")
        print(f"Original count: {orig_count}")
        print(f"Fixed count: {fixed_count}")
        
        if orig_count == fixed_count and not missing_in_fixed and not extra_in_fixed:
            print("Question counts match ✅")
        else:
            exit_code = 1
            if missing_in_fixed:
                print(f"\n[ERROR] {len(missing_in_fixed)} question(s) missing in fixed file:")
                details = get_question_details(original, key_field, missing_in_fixed)
                for d in details[:args.show_sample]:
                    print(f"  - {d['questionLabel']}: {d['question_id']}")
                    print(f"    Text: {d['questionBodyText']}")
                if len(missing_in_fixed) > args.show_sample:
                    print(f"  ... and {len(missing_in_fixed) - args.show_sample} more")
            
            if extra_in_fixed:
                print(f"\n[WARNING] {len(extra_in_fixed)} extra question(s) in fixed file (not in original):")
                for pid in extra_in_fixed[:args.show_sample]:
                    print(f"  - {pid}")
                if len(extra_in_fixed) > args.show_sample:
                    print(f"  ... and {len(extra_in_fixed) - args.show_sample} more")

    # questionSetId check mode
    if args.category:
        try:
            category = load_json(args.category)
        except Exception as e:
            print(f"Failed to load category JSON '{args.category}': {e}", file=sys.stderr)
            return 2

        cat_ids = collect_category_ids(category, questionset_only=args.questionset_only)
        found_values = extract_question_set_ids(fixed)
        found_values_str = [str(v) for v in found_values if v is not None]
        counter = Counter(found_values_str)
        unique_found = set(found_values_str)
        missing = sorted([v for v in unique_found if v not in cat_ids])

        print(f"\n=== QuestionSetId Check ===")
        print(f"Category ids loaded: {len(cat_ids)}")
        if args.questionset_only:
            print("Category mode: questionSets[].questionSetId only")
        else:
            print("Category mode: questionSets[].questionSetId + folders[].folderId")
        print(f"Found {len(found_values_str)} questionSetId(s) ({len(unique_found)} unique)")

        # 追加: questionSetIdが空またはNoneのquestion_idを出力
        missing_qsid_public_ids = extract_missing_question_set_ids(fixed)
        if missing_qsid_public_ids:
            exit_code = 1
            print(f"\n[INFO] The following questions have no questionSetId assigned (file: {args.fixed}):")
            for pqid in missing_qsid_public_ids:
                print(f"  - question_id: {pqid}")

        if not found_values_str:
            print("[ERROR] 指定ファイルにquestions/question_bodiesが見つかりません。対応形式か確認してください。")
            return 2

        if not missing:
            print("All questionSetId values are present in category.json ✅")
        else:
            exit_code = 1
            print("Missing questionSetId values (not found in category.json):")
            for m in missing[: args.show_sample]:
                print(f" - {m}  (occurrences: {counter.get(m,0)})")
            if len(missing) > args.show_sample:
                print(f"  ... and {len(missing) - args.show_sample} more missing IDs")

            print("\nSuggestion: review the IDs above and update `category.json` or fix the question assignments.")

    # If neither category nor compare-count specified, show usage hint
    if not args.category and not args.compare_count:
        print("[INFO] Use --category to check questionSetId existence, or --compare-count with --original to compare question counts.")
        return 0

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
