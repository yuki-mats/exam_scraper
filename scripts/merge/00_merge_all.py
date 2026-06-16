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
import re

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
        apply_answer_result_overrides,
        apply_correct_choice,
        apply_explanation_fields,
        apply_question_intent,
        apply_question_set,
        apply_question_type,
        build_patch_map_from_paths,
        normalize_true_false_intent_and_correct_choice,
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
        apply_answer_result_overrides,
        apply_correct_choice,
        apply_explanation_fields,
        apply_question_intent,
        apply_question_set,
        apply_question_type,
        build_patch_map_from_paths,
        normalize_true_false_intent_and_correct_choice,
    )


SOURCE_SUBDIR = "00_source"
MERGED_QTYPE_VIEW_SUBDIR = "12_merged_questionType"
MERGED1_SUBDIR = "20_merged_1"
MERGED2_SUBDIR = "30_merged_2"

PATCH_DIR_QTYPE = "10_questionType_fixed"
PATCH_DIR_EXPLANATION = "21_explanationText_added"
PATCH_DIR_QSET = "22_questionSetId_linked"
PATCH_DIR_CORRECT = "23_correctChoiceText_fixed"
PATCH_DIR_INTENT_AND_CORRECT_FALLBACK = "15_correctChoiceText_fixed"

PATCH_TAGS = {
    "question_type": "questionType_fixed",
    "explanation": "explanationText_added",
    "question_set": "questionSetId_linked",
    "correct_choice": "correctChoiceText_fixed",
}


JAPANESE_ERA_START_YEAR = {
    "令和": 2019,
    "平成": 1989,
    "昭和": 1926,
    "大正": 1912,
    "明治": 1868,
}


FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_digit_text(value: str) -> str:
    return (value or "").translate(FULLWIDTH_DIGIT_TRANS)


def _parse_japanese_era_year(era_name: str, year_token: str) -> int | None:
    base_year = JAPANESE_ERA_START_YEAR.get(era_name)
    if base_year is None:
        return None

    normalized_year = _normalize_digit_text(year_token).strip()
    if normalized_year == "元":
        era_year = 1
    elif normalized_year.isdigit():
        era_year = int(normalized_year)
    else:
        return None

    if era_year <= 0:
        return None

    return base_year + era_year - 1


def infer_exam_year_from_label(exam_label: str) -> int | None:
    """
    examLabel から examYear(西暦)を推定する。
    - (2023年) のような括弧内西暦を優先
    - 2023年度/2023年 を次に見る
    - 令和5年度/平成25年度 などの和暦は西暦へ変換
    """
    if not exam_label:
        return None

    normalized_label = _normalize_digit_text(exam_label)

    western_year_match = re.search(r"[（(]\s*((?:19|20)\d{2})\s*年\s*[)）]", normalized_label)
    if western_year_match:
        return int(western_year_match.group(1))

    western_year_match = re.search(r"((?:19|20)\d{2})\s*年(?:度)?", normalized_label)
    if western_year_match:
        return int(western_year_match.group(1))

    japanese_year_match = re.search(
        r"(令和|平成|昭和|大正|明治)\s*(元|[0-9０-９]+)\s*年(?:度)?",
        normalized_label,
    )
    if japanese_year_match:
        return _parse_japanese_era_year(
            japanese_year_match.group(1),
            japanese_year_match.group(2),
        )

    return None


def backfill_exam_year(data: dict) -> int:
    """
    payload 内の question_bodies[] の examYear が欠けている場合に examLabel から推定して埋める。
    """
    bodies = data.get("question_bodies")
    if not isinstance(bodies, list):
        return 0

    updated = 0
    for body in bodies:
        if not isinstance(body, dict):
            continue
        if body.get("examYear") not in (None, ""):
            continue
        label = body.get("examLabel")
        if not isinstance(label, str) or not label.strip():
            continue
        inferred = infer_exam_year_from_label(label)
        if inferred is None:
            continue
        body["examYear"] = inferred
        updated += 1
    return updated


ANSWER_RESULT_RE = re.compile(r"正解は\s*([1-9０-９]+(?:\s*,\s*[1-9０-９]+)*)\s*です。")


def _parse_answer_numbers(answer_result_text: str) -> list[int]:
    if not answer_result_text:
        return []
    text = _normalize_digit_text(answer_result_text)
    match = ANSWER_RESULT_RE.search(text)
    if not match:
        return []
    numbers: list[int] = []
    for part in match.group(1).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        num = int(part)
        if num not in numbers:
            numbers.append(num)
    return numbers


def backfill_correct_choice_text_from_answer_result(data: dict) -> int:
    """
    correctChoiceText に None が含まれる場合に、
    answer_result_text（優先）/ answer_result_inferred_correct_choice_numbers と questionIntent を使って補完する。

    ルール（絶対）:
      - select_correct   → 正解番号の位置が「正しい」
      - select_incorrect → 正解番号の位置が「間違い」
    """
    bodies = data.get("question_bodies")
    if not isinstance(bodies, list):
        return 0

    updated = 0
    for body in bodies:
        if not isinstance(body, dict):
            continue
        cct = body.get("correctChoiceText")
        if not (isinstance(cct, list) and any(x is None for x in cct)):
            continue

        choice_list = body.get("choiceTextList")
        if not isinstance(choice_list, list) or not choice_list:
            continue
        choice_count = len(choice_list)

        answer_numbers = _parse_answer_numbers(str(body.get("answer_result_text") or ""))
        if not answer_numbers:
            inferred = body.get("answer_result_inferred_correct_choice_numbers")
            if isinstance(inferred, list) and inferred:
                answer_numbers = []
                for v in inferred:
                    if isinstance(v, int):
                        answer_numbers.append(v)
                    elif str(v).isdigit():
                        answer_numbers.append(int(str(v)))
                # 重複除外
                normalized: list[int] = []
                for num in answer_numbers:
                    if num not in normalized:
                        normalized.append(num)
                answer_numbers = normalized
        if not answer_numbers:
            continue
        if any(num < 1 or num > choice_count for num in answer_numbers):
            continue

        question_intent = str(body.get("questionIntent") or "").strip()
        answer_indexes = {num - 1 for num in answer_numbers}

        if question_intent == "select_incorrect":
            labels = ["正しい"] * choice_count
            for idx in answer_indexes:
                labels[idx] = "間違い"
        elif question_intent == "select_correct":
            labels = ["間違い"] * choice_count
            for idx in answer_indexes:
                labels[idx] = "正しい"
        else:
            continue

        body["correctChoiceText"] = labels
        updated += 1

    return updated


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


def ensure_answer_result_text_present(*, data: dict, source_path: Path) -> None:
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"question_bodies が見つかりません: {source_path}")
    for idx, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        value = question.get("answer_result_text")
        if isinstance(value, str) and value.strip():
            continue
        raise RuntimeError(
            "answer_result_text が欠けています。"
            f" file={source_path} question_index={idx} question_url={question.get('question_url')}\n"
            "先に backfill を実行して 00_source を補完してください:\n"
            "  python -u scripts/fix/backfill_answer_result_text_00_source.py --apply"
        )


def merge_all(list_group_id: str, base_dir: Path) -> None:
    list_group_dir = base_dir / list_group_id
    source_dir = list_group_dir / SOURCE_SUBDIR
    merged_qtype_view_dir = list_group_dir / MERGED_QTYPE_VIEW_SUBDIR
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
        key_fields=("original_question_id",),
    )

    patch_intent_dir = list_group_dir / PATCH_DIR_INTENT_AND_CORRECT_FALLBACK
    intent_paths = (
        select_latest_patch_files(
            sorted(patch_intent_dir.glob("*.json")),
            PATCH_TAGS["correct_choice"],
        )
        if patch_intent_dir.exists()
        else []
    )
    intent_map = build_patch_map_from_paths(
        intent_paths,
        value_key="questionIntent",
        key_fields=("original_question_id",),
    )
    intent_entry_map = build_patch_map_from_paths(
        intent_paths,
        key_fields=("original_question_id",),
    )

    base_files = iter_base_files(source_dir)
    if not base_files:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {source_dir}")

    merged_qtype_view_dir.mkdir(parents=True, exist_ok=True)
    archived_qtype_view = archive_existing_json_files(merged_qtype_view_dir)
    if archived_qtype_view:
        print(
            f"[INFO] 12_merged_questionType old 退避件数: {archived_qtype_view} -> {merged_qtype_view_dir / 'old'}"
        )

    merged1_dir.mkdir(parents=True, exist_ok=True)
    archived_merged1 = archive_existing_json_files(merged1_dir)
    if archived_merged1:
        print(f"[INFO] 20_merged_1 old 退避件数: {archived_merged1} -> {merged1_dir / 'old'}")
    qtype_updates = 0
    intent_updates = 0
    true_false_intent_updates = 0
    true_false_correct_choice_updates = 0
    exam_year_backfills = 0
    correct_choice_backfills = 0
    answer_result_override_updates = 0
    for base_path in base_files:
        data = load_json(base_path)
        qtype_updates += apply_question_type(data, qtype_map_by_id)
        answer_result_override_updates += apply_answer_result_overrides(data, intent_entry_map)
        intent_updates += apply_question_intent(data, intent_map)
        u_intent, u_choice = normalize_true_false_intent_and_correct_choice(data)
        true_false_intent_updates += u_intent
        true_false_correct_choice_updates += u_choice
        exam_year_backfills += backfill_exam_year(data)
        correct_choice_backfills += backfill_correct_choice_text_from_answer_result(data)
        ensure_answer_result_text_present(data=data, source_path=base_path)

        qtype_view_path = merged_qtype_view_dir / output_filename_for_base(base_path)
        save_json(data, qtype_view_path)

        out_path = merged1_dir / output_filename_for_base(base_path)
        save_json(data, out_path)
    print(f"[INFO] 20_merged_1 生成完了: {merged1_dir}")
    print(f"[INFO] questionType 更新件数: {qtype_updates}")
    print(f"[INFO] questionIntent 更新件数: {intent_updates}")
    print(f"[INFO] true_false questionIntent 正規化件数: {true_false_intent_updates}")
    print(f"[INFO] true_false correctChoiceText 正規化件数: {true_false_correct_choice_updates}")
    if answer_result_override_updates:
        print(f"[INFO] answer_result override 更新件数: {answer_result_override_updates}")
    if exam_year_backfills:
        print(f"[INFO] examYear 推定補完件数: {exam_year_backfills}")
    if correct_choice_backfills:
        print(f"[INFO] correctChoiceText(None) 自動補完件数: {correct_choice_backfills}")
    print(f"[INFO] 12_merged_questionType 生成完了: {merged_qtype_view_dir}")

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
    correct_entry_map = build_patch_map_from_paths(
        correct_paths,
        key_fields=("original_question_id",),
    )
    correct_entry_map_fallback = build_patch_map_from_paths(
        intent_paths,
        key_fields=("original_question_id",),
    )
    for key, value in correct_entry_map_fallback.items():
        correct_entry_map.setdefault(key, value)
    correct_map_fallback = build_patch_map_from_paths(
        intent_paths,
        value_key="correctChoiceText",
        key_fields=("original_question_id",),
    )
    for key, value in correct_map_fallback.items():
        if value is None:
            continue
        correct_map[key] = value

    expl_updates = 0
    qset_updates = 0
    correct_updates = 0
    answer_result_updates = 0
    intent_updates_merged2 = 0
    true_false_intent_updates_merged2 = 0
    true_false_correct_choice_updates_merged2 = 0
    exam_year_backfills_merged2 = 0
    correct_choice_backfills_merged2 = 0

    merged2_dir.mkdir(parents=True, exist_ok=True)
    archived_merged2 = archive_existing_json_files(merged2_dir)
    if archived_merged2:
        print(f"[INFO] 30_merged_2 old 退避件数: {archived_merged2} -> {merged2_dir / 'old'}")
    for merged_path in merged_files:
        data = load_json(merged_path)
        expl_updates += apply_explanation_fields(data, expl_map)
        qset_updates += apply_question_set(data, qset_map)
        correct_updates += apply_correct_choice(data, correct_map)
        answer_result_updates += apply_answer_result_overrides(data, correct_entry_map)
        intent_updates_merged2 += apply_question_intent(data, intent_map)
        u_intent, u_choice = normalize_true_false_intent_and_correct_choice(data)
        true_false_intent_updates_merged2 += u_intent
        true_false_correct_choice_updates_merged2 += u_choice
        exam_year_backfills_merged2 += backfill_exam_year(data)
        correct_choice_backfills_merged2 += backfill_correct_choice_text_from_answer_result(data)
        # 30_merged_2 は実行時刻付きで新規出力する
        out_path = merged2_dir / output_filename_for_base(merged_path, force_new=True)
        valid_data, manual_data = maybe_split_for_manual_output(data, out_path)
        save_json(valid_data, out_path)
        ensure_answer_result_text_present(data=valid_data, source_path=out_path)
        if manual_data:
            manual_path = build_manual_output_path(out_path)
            save_json(manual_data, manual_path)
            print(f"[WARN] choiceTextList 空のため外出し: {manual_path}")

    print(f"[INFO] 30_merged_2 生成完了: {merged2_dir}")
    print(f"[INFO] explanationText 更新件数: {expl_updates}")
    print(f"[INFO] questionSetId 更新件数: {qset_updates}")
    print(f"[INFO] correctChoiceText 更新件数: {correct_updates}")
    if answer_result_updates:
        print(f"[INFO] answer_result 更新件数: {answer_result_updates}")
    print(f"[INFO] questionIntent 更新件数: {intent_updates_merged2}")
    print(f"[INFO] true_false questionIntent 正規化件数: {true_false_intent_updates_merged2}")
    print(f"[INFO] true_false correctChoiceText 正規化件数: {true_false_correct_choice_updates_merged2}")
    if exam_year_backfills_merged2:
        print(f"[INFO] examYear 推定補完件数: {exam_year_backfills_merged2}")
    if correct_choice_backfills_merged2:
        print(f"[INFO] correctChoiceText(None) 自動補完件数: {correct_choice_backfills_merged2}")


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
