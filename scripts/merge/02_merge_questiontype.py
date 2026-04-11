"""
questionType パッチ（`*_questionType_fixed_YYYYMMDD_HHMM.json`）だけを本体 JSON にマージする専用スクリプト。
元ファイルは変更せず、`*_merged.json` を新規生成します。
"""

import argparse
import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from scripts.merge.merge_utils import (
    build_manual_output_path,
    is_patch_filename_for_tag,
    maybe_split_for_manual_output,
    select_latest_patch_files,
    source_stem_from_patch_filename,
)

PATCH_TAG = "questionType_fixed"
PATCH_TAGS_FOR_FILTER = (
    "questionType_fixed",
    "questionSetId_linked",
    "explanationText_added",
    "correctChoiceText_fixed",
)
SOURCE_SUBDIR_NAME = "00_source"
PATCH_SUBDIR_NAME = "10_questionType_fixed"
MERGED_DIR_NAME = "20_merged_1"


def load_json(filepath: str) -> Any:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, filepath: str) -> None:
    # emptyファイルは上書き禁止・常に新規ファイル名で保存
    import datetime
    path = Path(filepath)
    if "empty" in path.name:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        path = path.with_name(f"{path.stem}_{ts}{path.suffix}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_patch_entries(patch_data: Any) -> List[dict]:
    if isinstance(patch_data, list):
        return [e for e in patch_data if isinstance(e, dict)]
    if isinstance(patch_data, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = patch_data.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
    return []


def build_lookup(patched_questions: List[dict]) -> Dict[str, dict]:
    by_id: Dict[str, dict] = {}
    for entry in patched_questions:
        pid = entry.get("original_question_id")
        if pid:
            by_id[str(pid)] = entry
    return by_id


def merge_patch(original_data: dict, patch_data: Any) -> dict:
    patched_questions = extract_patch_entries(patch_data)
    if not patched_questions:
        print("[WARN] パッチ対象の配列が空です")
        return original_data

    lookup_by_id = build_lookup(patched_questions)
    updated = 0
    for q in original_data.get("question_bodies", []):
        # original_question_idがなければpublic_question_idをセット
        if not q.get("original_question_id") and q.get("public_question_id"):
            q["original_question_id"] = q["public_question_id"]
        patch_entry = None
        pid = q.get("original_question_id")
        if pid and str(pid) in lookup_by_id:
            patch_entry = lookup_by_id[str(pid)]
        if not patch_entry:
            continue
        if "questionType" in patch_entry and patch_entry["questionType"] is not None:
            q["questionType"] = patch_entry["questionType"]
            updated += 1
    print(f"[INFO] {updated} 件の questionType を更新しました")
    return original_data


def generate_output_filepath(source_filepath: str) -> str:
    path = Path(source_filepath)
    # If source is under 00_source/, place merged under sibling 20_merged/ at list_group_id level.
    list_group_dir = path.parent
    if list_group_dir.name == SOURCE_SUBDIR_NAME:
        list_group_dir = list_group_dir.parent
    merged_dir = list_group_dir / MERGED_DIR_NAME
    merged_dir.mkdir(parents=True, exist_ok=True)
    return str((merged_dir / f"{path.stem}_merged.json").resolve())


def get_base_filepath_for_merge(source_filepath: str) -> str:
    merged = generate_output_filepath(source_filepath)
    return merged if os.path.exists(merged) else source_filepath


def find_patch_files(directory: str) -> List[str]:
    paths = [Path(p) for p in glob.glob(os.path.join(directory, "*.json"))]
    patch_paths = [p for p in paths if is_patch_filename_for_tag(p.name, PATCH_TAG)]
    return [str(p) for p in select_latest_patch_files(patch_paths, PATCH_TAG)]


def is_patch_file(filepath: str) -> bool:
    basename = os.path.basename(filepath)
    return any(is_patch_filename_for_tag(basename, tag) for tag in PATCH_TAGS_FOR_FILTER)


def is_merged_file(filepath: str) -> bool:
    return os.path.basename(filepath).endswith("_merged.json")


def copy_base_files_as_merged(target_dir: str) -> None:
    """Generate *_merged.json for base files that don't have patches."""
    candidates = sorted(
        f
        for f in glob.glob(os.path.join(target_dir, "*.json"))
        if not is_patch_file(f)
        and not is_merged_file(f)
    )
    if not candidates:
        return

    for base in candidates:
        merged_path = generate_output_filepath(base)
        if os.path.exists(merged_path):
            continue
        data = load_json(base)
        merged_path_obj = Path(merged_path)
        valid_data, manual_data = maybe_split_for_manual_output(data, merged_path_obj)
        save_json(valid_data, merged_path)
        if manual_data:
            manual_path = build_manual_output_path(merged_path_obj)
            save_json(manual_data, str(manual_path))
            print(f"[WARN] choiceTextList 空のため外出し: {manual_path}")
        print(f"[INFO] パッチなしで複製しました: {merged_path}")


def process_patch_file(patch_filepath: str) -> None:
    print(f"[INFO] パッチファイルを処理中: {patch_filepath}")
    patch_data = load_json(patch_filepath)

    patched_questions = extract_patch_entries(patch_data)
    source_filepath = None
    if isinstance(patch_data, dict):
        source_filepath = patch_data.get("source_filepath")
    if not source_filepath and patched_questions:
        source_filepath = patched_questions[0].get("source_filepath")
    if not source_filepath:
        patch_path = Path(patch_filepath)
        list_group_dir = patch_path.parent.parent
        source_stem = source_stem_from_patch_filename(patch_path.name, PATCH_TAG)
        if source_stem:
            candidate = list_group_dir / SOURCE_SUBDIR_NAME / f"{source_stem}.json"
            if candidate.exists():
                source_filepath = str(candidate.resolve())
    if not source_filepath:
        print("[ERROR] source_filepath を特定できません")
        return
    if not os.path.exists(source_filepath):
        print(f"[ERROR] 元ファイルが見つかりません: {source_filepath}")
        return

    base = get_base_filepath_for_merge(source_filepath)
    if base != source_filepath:
        print(f"[INFO] 既存のマージファイルを読み込みます: {base}")

    original_data = load_json(base)
    merged = merge_patch(original_data, patch_data)
    out_path = generate_output_filepath(source_filepath)
    out_path_obj = Path(out_path)
    valid_data, manual_data = maybe_split_for_manual_output(merged, out_path_obj)
    save_json(valid_data, out_path)
    if manual_data:
        manual_path = build_manual_output_path(out_path_obj)
        save_json(manual_data, str(manual_path))
        print(f"[WARN] choiceTextList 空のため外出し: {manual_path}")
    print(f"[SUCCESS] 保存しました: {out_path}")


def process_directory(list_group_id: str, base_dir: str) -> None:
    patch_dir = os.path.join(base_dir, list_group_id, PATCH_SUBDIR_NAME)
    source_dir = os.path.join(base_dir, list_group_id, SOURCE_SUBDIR_NAME)
    if not os.path.exists(source_dir):
        print(f"[ERROR] ディレクトリが見つかりません: {source_dir}")
        return
    patches = find_patch_files(patch_dir if os.path.exists(patch_dir) else source_dir)
    if not patches:
        print("[INFO] 対象パッチがありません（未修正でも merged を生成します）")
    else:
        for p in patches:
            process_patch_file(p)
            print()
    copy_base_files_as_merged(source_dir)


def main():
    parser = argparse.ArgumentParser(description="questionType パッチ専用マージ")
    parser.add_argument("patch_files", nargs="*", help="個別パッチファイルを指定する場合")
    parser.add_argument(
        "--list-group-id",
        "-g",
        type=str,
        help="list_group_id を指定してディレクトリ内のパッチをまとめて適用",
    )
    parser.add_argument(
        "--base-dir",
        "-d",
        default="/Users/yuki/development/exam_scraper/output/2nd-class-kenchikushi/questions_json",
        help="list_group_id のベースディレクトリ",
    )
    args = parser.parse_args()

    # 追加: 何も指定されなければ既定で '85010' を使用する
    if not args.list_group_id and not args.patch_files:
        args.list_group_id = "85010"
        print("[INFO] デフォルト list_group_id '85010' を使用します")

    if args.list_group_id:
        process_directory(args.list_group_id, args.base_dir)
        return
    if not args.patch_files:
        parser.print_help()
        print("\n[ERROR] パッチファイルまたは --list-group-id を指定してください")
        return
    for p in args.patch_files:
        if not os.path.exists(p):
            print(f"[ERROR] 見つかりません: {p}")
            continue
        process_patch_file(p)


if __name__ == "__main__":
    main()
