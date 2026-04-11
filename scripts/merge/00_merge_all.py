#!/usr/bin/env python3
"""
list_group_id 配下のパッチをまとめて適用し、20_merged_1 と 30_merged_2 を生成する統合スクリプト。
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from scripts.common.questions_json_paths import resolve_list_group_base_dir
    from scripts.merge.merge_utils import (
        build_manual_output_path,
        is_patch_filename_for_tag,
        maybe_split_for_manual_output,
        select_latest_patch_files,
    )
    from scripts.merge.patch_views import (
        apply_correct_choice,
        apply_explanation_fields,
        apply_question_set,
        apply_question_type,
        build_patch_map_from_paths,
    )
else:
    from scripts.common.questions_json_paths import resolve_list_group_base_dir
    from .merge_utils import (
        build_manual_output_path,
        is_patch_filename_for_tag,
        maybe_split_for_manual_output,
        select_latest_patch_files,
    )
    from .patch_views import (
        apply_correct_choice,
        apply_explanation_fields,
        apply_question_set,
        apply_question_type,
        build_patch_map_from_paths,
    )


SOURCE_SUBDIR = "00_source"
MERGED1_SUBDIR = "20_merged_1"
MERGED2_SUBDIR = "30_merged_2"

PATCH_DIR_QTYPE = "10_questionType_fixed"
PATCH_DIR_EXPLANATION = "21_explanationText_added"
PATCH_DIR_QSET = "22_questionSetId_linked"
PATCH_DIR_CORRECT = "23_correctChoiceText_fixed"

PATCH_TAGS = {
    "question_type": "questionType_fixed",
    "explanation": "explanationText_added",
    "question_set": "questionSetId_linked",
    "correct_choice": "correctChoiceText_fixed",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_patch_filename(name: str) -> bool:
    return any(is_patch_filename_for_tag(name, tag) for tag in PATCH_TAGS.values())


def iter_base_files(directory: Path) -> List[Path]:
    files = []
    for path in sorted(directory.glob("*.json")):
        name = path.name
        if name.endswith("_merged.json"):
            continue
        if is_patch_filename(name):
            continue
        files.append(path)
    return files


def iter_merged_files(directory: Path) -> List[Path]:
    files = []
    for path in sorted(directory.glob("*.json")):
        name = path.name
        if is_patch_filename(name):
            continue
        files.append(path)
    return files


def resolve_base_dir(list_group_id: str, base_dir: str | None) -> Path:
    return resolve_list_group_base_dir(list_group_id, base_dir, repo_root=ROOT_DIR)


def output_filename_for_base(path: Path, force_new: bool = False) -> str:
    stem = path.stem
    name = stem if stem.endswith("_merged") else f"{stem}_merged"
    if force_new:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        return f"{name}_{ts}.json"
    return f"{name}.json"


def archive_existing_json_files(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0
    json_files = sorted(path for path in output_dir.glob("*.json") if path.is_file())
    if not json_files:
        return 0
    old_dir = output_dir / "old"
    old_dir.mkdir(parents=True, exist_ok=True)
    moved_count = 0
    for file_path in json_files:
        target_path = old_dir / file_path.name
        if target_path.exists():
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = old_dir / f"{file_path.stem}_{timestamp}{file_path.suffix}"
        shutil.move(str(file_path), str(target_path))
        moved_count += 1
    return moved_count


def merge_all(list_group_id: str, base_dir: Path) -> None:
    list_group_dir = base_dir / list_group_id
    source_dir = list_group_dir / SOURCE_SUBDIR
    merged1_dir = list_group_dir / MERGED1_SUBDIR
    merged2_dir = list_group_dir / MERGED2_SUBDIR

    if not source_dir.exists():
        raise FileNotFoundError(f"ソースディレクトリが見つかりません: {source_dir}")

    patch_qtype_dir = list_group_dir / PATCH_DIR_QTYPE
    qtype_paths = (
        select_latest_patch_files(
            sorted(patch_qtype_dir.glob("*.json")),
            PATCH_TAGS["question_type"],
        )
        if patch_qtype_dir.exists()
        else []
    )
    qtype_map_by_id = build_patch_map_from_paths(
        qtype_paths,
        value_key="questionType",
        key_fields=("original_question_id",),
    )

    base_files = iter_base_files(source_dir)
    if not base_files:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {source_dir}")

    merged1_dir.mkdir(parents=True, exist_ok=True)
    archived_merged1 = archive_existing_json_files(merged1_dir)
    if archived_merged1:
        print(f"[INFO] 20_merged_1 old 退避件数: {archived_merged1} -> {merged1_dir / 'old'}")
    qtype_updates = 0
    for base_path in base_files:
        data = load_json(base_path)
        qtype_updates += apply_question_type(data, qtype_map_by_id)
        out_path = merged1_dir / output_filename_for_base(base_path)
        save_json(data, out_path)
    print(f"[INFO] 20_merged_1 生成完了: {merged1_dir}")
    print(f"[INFO] questionType 更新件数: {qtype_updates}")

    merged_files = iter_merged_files(merged1_dir)
    if not merged_files:
        raise FileNotFoundError(f"20_merged_1 にファイルがありません: {merged1_dir}")

    patch_expl_dir = list_group_dir / PATCH_DIR_EXPLANATION
    patch_qset_dir = list_group_dir / PATCH_DIR_QSET
    patch_correct_dir = list_group_dir / PATCH_DIR_CORRECT

    expl_paths = (
        select_latest_patch_files(
            sorted(patch_expl_dir.glob("*.json")),
            PATCH_TAGS["explanation"],
        )
        if patch_expl_dir.exists()
        else []
    )
    qset_paths = (
        select_latest_patch_files(
            sorted(patch_qset_dir.glob("*.json")),
            PATCH_TAGS["question_set"],
        )
        if patch_qset_dir.exists()
        else []
    )
    correct_paths = (
        select_latest_patch_files(
            sorted(patch_correct_dir.glob("*.json")),
            PATCH_TAGS["correct_choice"],
        )
        if patch_correct_dir.exists()
        else []
    )

    expl_map = build_patch_map_from_paths(
        expl_paths,
        key_fields=("original_question_id",),
    )
    qset_map = build_patch_map_from_paths(
        qset_paths,
        value_key="questionSetId",
        key_fields=("original_question_id",),
    )
    correct_map = build_patch_map_from_paths(
        correct_paths,
        value_key="correctChoiceText",
        key_fields=("original_question_id",),
    )

    expl_updates = 0
    qset_updates = 0
    correct_updates = 0

    merged2_dir.mkdir(parents=True, exist_ok=True)
    archived_merged2 = archive_existing_json_files(merged2_dir)
    if archived_merged2:
        print(f"[INFO] 30_merged_2 old 退避件数: {archived_merged2} -> {merged2_dir / 'old'}")
    for merged_path in merged_files:
        data = load_json(merged_path)
        expl_updates += apply_explanation_fields(data, expl_map)
        qset_updates += apply_question_set(data, qset_map)
        correct_updates += apply_correct_choice(data, correct_map)
        # 30_merged_2 は実行時刻付きで新規出力する
        out_path = merged2_dir / output_filename_for_base(merged_path, force_new=True)
        valid_data, manual_data = maybe_split_for_manual_output(data, out_path)
        save_json(valid_data, out_path)
        if manual_data:
            manual_path = build_manual_output_path(out_path)
            save_json(manual_data, manual_path)
            print(f"[WARN] choiceTextList 空のため外出し: {manual_path}")

    print(f"[INFO] 30_merged_2 生成完了: {merged2_dir}")
    print(f"[INFO] explanationText 更新件数: {expl_updates}")
    print(f"[INFO] questionSetId 更新件数: {qset_updates}")
    print(f"[INFO] correctChoiceText 更新件数: {correct_updates}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="パッチを統合適用し、20_merged_1 と 30_merged_2 を生成します。"
    )
    parser.add_argument("list_group_id", type=str, help="list_group_id (例: 85010)")
    parser.add_argument(
        "--base-dir",
        "-d",
        type=str,
        default=None,
        help="list_group_id を含む questions_json のルート (例: output/2nd-class-kenchikushi/questions_json)",
    )
    args = parser.parse_args()

    try:
        base_dir = resolve_base_dir(args.list_group_id, args.base_dir)
        merge_all(args.list_group_id, base_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
