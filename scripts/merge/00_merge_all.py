#!/usr/bin/env python3
"""
list_group_id 配下のパッチをまとめて適用し、20_merged_1 と 30_merged_2 を生成する統合スクリプト。
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

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
        build_layered_patch_index_from_paths,
        ensure_identity_candidate_index_valid,
    )
    from scripts.merge.question_issue_corrections import (
        build_question_issue_correction_index,
        ensure_all_question_issue_corrections_applied,
        selected_question_issue_correction_paths,
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
        build_layered_patch_index_from_paths,
        ensure_identity_candidate_index_valid,
    )
    from .question_issue_corrections import (
        build_question_issue_correction_index,
        ensure_all_question_issue_corrections_applied,
        selected_question_issue_correction_paths,
    )

from scripts.common.question_identity import (
    IdentityCandidateIndex,
    SourceIdentityBinding,
    load_source_record_inventory,
    source_json_paths,
)
from scripts.merge.record_projection import (
    backfill_correct_choice_text_from_answer_result,
    backfill_exam_year,
    infer_exam_year_from_label,
    project_merge_record,
)


SOURCE_SUBDIR = "00_source"
MERGED_QTYPE_VIEW_SUBDIR = "12_merged_questionType"
MERGED1_SUBDIR = "20_merged_1"
MERGED2_SUBDIR = "30_merged_2"

PATCH_DIR_QTYPE = "10_questionType_fixed"
PATCH_DIR_ORIGINALIZED = "05_originalized"
PATCH_DIR_LAW_CONTEXT = "18_law_context_prepared"
PATCH_DIR_EXPLANATION = "21_explanationText_added"
PATCH_DIR_QSET = "22_questionSetId_linked"
PATCH_DIR_CORRECT = "23_correctChoiceText_fixed"
PATCH_DIR_QUESTION_ISSUES = "24_questionIssueCorrections"
PATCH_DIR_INTENT_AND_CORRECT_FALLBACK = "15_correctChoiceText_fixed"

PATCH_TAGS = {
    "originalized": "originalized",
    "question_type": "questionType_fixed",
    "law_context": "lawContext_prepared",
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


def iter_base_files(directory: Path) -> list[Path]:
    return source_json_paths(directory)


def resolve_base_dir(list_group_id: str, base_dir: str | None) -> Path:
    return resolve_list_group_base_dir(list_group_id, base_dir, repo_root=ROOT_DIR)


def output_filename_for_base(path: Path, force_new: bool = False) -> str:
    stem = path.stem
    name = stem if stem.endswith("_merged") else f"{stem}_merged"
    if force_new:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        return f"{name}_{ts}.json"
    return f"{name}.json"


def archive_existing_json_files(
    output_dir: Path,
    *,
    moved_files: list[tuple[Path, Path]] | None = None,
) -> int:
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
            suffix = 1
            while target_path.exists():
                target_path = old_dir / (
                    f"{file_path.stem}_{timestamp}_{suffix:02d}{file_path.suffix}"
                )
                suffix += 1
        shutil.move(str(file_path), str(target_path))
        if moved_files is not None:
            moved_files.append((file_path, target_path))
        moved_count += 1
    return moved_count


def commit_prepared_outputs(
    outputs_by_dir: Mapping[Path, Iterable[tuple[Path, dict]]],
) -> dict[Path, int]:
    """Commit generated artifacts together and restore originals on I/O failure."""

    prepared = {
        directory: tuple(outputs)
        for directory, outputs in outputs_by_dir.items()
    }
    moved_files: list[tuple[Path, Path]] = []
    written_paths: list[Path] = []
    archived_counts: dict[Path, int] = {}
    try:
        for directory in prepared:
            directory.mkdir(parents=True, exist_ok=True)
            archived_counts[directory] = archive_existing_json_files(
                directory,
                moved_files=moved_files,
            )
        for outputs in prepared.values():
            for path, data in outputs:
                written_paths.append(path)
                save_json(data, path)
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(written_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"生成物削除失敗 {path}: {rollback_exc}")
        for original_path, archived_path in reversed(moved_files):
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archived_path), str(original_path))
            except OSError as rollback_exc:
                rollback_errors.append(
                    f"既存成果物復元失敗 {original_path}: {rollback_exc}"
                )
        if rollback_errors:
            raise RuntimeError(
                f"成果物commit失敗: {exc}; rollback失敗: "
                + " / ".join(rollback_errors)
            ) from exc
        raise
    return archived_counts


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


def merge_all(
    list_group_id: str,
    base_dir: Path,
    *,
    require_answer_result_text: bool = True,
) -> None:
    list_group_dir = base_dir / list_group_id
    source_dir = list_group_dir / SOURCE_SUBDIR
    merged_qtype_view_dir = list_group_dir / MERGED_QTYPE_VIEW_SUBDIR
    merged1_dir = list_group_dir / MERGED1_SUBDIR
    merged2_dir = list_group_dir / MERGED2_SUBDIR

    if not source_dir.exists():
        raise FileNotFoundError(f"ソースディレクトリが見つかりません: {source_dir}")

    base_files = iter_base_files(source_dir)
    if not base_files:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {source_dir}")

    inventory = load_source_record_inventory(
        source_dir,
        qualification=base_dir.parent.name,
        list_group_id=list_group_id,
    )
    inventory_paths = {entry.path.resolve() for entry in inventory}
    base_paths = {path.resolve() for path in base_files}
    if inventory_paths != base_paths:
        unexpected = sorted(str(path) for path in inventory_paths - base_paths)
        missing = sorted(str(path) for path in base_paths - inventory_paths)
        raise RuntimeError(
            "source inventoryとMerge入力が一致しません: "
            f"unexpected={unexpected} missing={missing}"
        )
    source_identities = tuple(entry.identity for entry in inventory)
    source_bindings_by_stem_lists: dict[str, list[SourceIdentityBinding]] = {}
    for entry in inventory:
        source_bindings_by_stem_lists.setdefault(
            entry.path.stem,
            [],
        ).append(entry.identity.binding)
    source_bindings_by_stem = {
        stem: tuple(bindings)
        for stem, bindings in source_bindings_by_stem_lists.items()
    }

    def selected_paths(patch_dir_name: str, patch_tag: str) -> list[Path]:
        patch_dir = list_group_dir / patch_dir_name
        if not patch_dir.exists():
            return []
        return select_latest_patch_files(
            sorted(patch_dir.glob("*.json")),
            patch_tag,
        )

    originalized_paths = selected_paths(
        PATCH_DIR_ORIGINALIZED,
        PATCH_TAGS["originalized"],
    )
    qtype_paths = selected_paths(
        PATCH_DIR_QTYPE,
        PATCH_TAGS["question_type"],
    )
    intent_paths = selected_paths(
        PATCH_DIR_INTENT_AND_CORRECT_FALLBACK,
        PATCH_TAGS["correct_choice"],
    )
    correct_paths = selected_paths(
        PATCH_DIR_CORRECT,
        PATCH_TAGS["correct_choice"],
    )
    law_context_paths = selected_paths(
        PATCH_DIR_LAW_CONTEXT,
        PATCH_TAGS["law_context"],
    )
    expl_paths = selected_paths(
        PATCH_DIR_EXPLANATION,
        PATCH_TAGS["explanation"],
    )
    qset_paths = selected_paths(
        PATCH_DIR_QSET,
        PATCH_TAGS["question_set"],
    )
    question_issue_dir = list_group_dir / PATCH_DIR_QUESTION_ISSUES
    question_issue_paths = selected_question_issue_correction_paths(
        question_issue_dir
    )

    def build_stage_index(
        paths: Iterable[Path],
        patch_tag: str,
        label: str,
    ) -> IdentityCandidateIndex:
        return build_layered_patch_index_from_paths(
            paths,
            patch_tag=patch_tag,
            sources=source_identities,
            label=label,
        )

    originalized_index = build_stage_index(
        originalized_paths,
        PATCH_TAGS["originalized"],
        "originalized patch",
    )
    qtype_index = build_stage_index(
        qtype_paths,
        PATCH_TAGS["question_type"],
        "questionType patch",
    )
    intent_index = build_stage_index(
        intent_paths,
        PATCH_TAGS["correct_choice"],
        "questionIntent patch",
    )
    strict_correct_index = build_stage_index(
        correct_paths,
        PATCH_TAGS["correct_choice"],
        "correctChoice patch",
    )
    law_context_index = build_stage_index(
        law_context_paths,
        PATCH_TAGS["law_context"],
        "lawContext patch",
    )
    explanation_index = build_stage_index(
        expl_paths,
        PATCH_TAGS["explanation"],
        "explanation patch",
    )
    question_set_index = build_stage_index(
        qset_paths,
        PATCH_TAGS["question_set"],
        "questionSet patch",
    )
    question_issue_index = build_question_issue_correction_index(
        question_issue_paths,
        source_identities,
    )
    for label, index in (
        ("originalized patch", originalized_index),
        ("questionType patch", qtype_index),
        ("questionIntent patch", intent_index),
        ("correctChoice patch", strict_correct_index),
        ("lawContext patch", law_context_index),
        ("explanation patch", explanation_index),
        ("questionSet patch", question_set_index),
        ("question issue correction", question_issue_index),
    ):
        ensure_identity_candidate_index_valid(index, label=label)

    update_counts: dict[str, int] = {}
    applied_question_issue_targets: set[str] = set()
    prepared_records: list[tuple[Path, dict, dict, Path, Path]] = []
    for base_path in base_files:
        source_data = load_json(base_path)
        source_questions = source_data.get("question_bodies")
        if not isinstance(source_questions, list):
            raise ValueError(f"question_bodies が見つかりません: {base_path}")
        source_bindings = source_bindings_by_stem.get(base_path.stem)
        if source_bindings is None:
            raise RuntimeError(f"source bindingがありません: {base_path}")
        if len(source_questions) != len(source_bindings):
            raise RuntimeError(
                "source binding count mismatch: "
                f"questions={len(source_questions)} bindings={len(source_bindings)}"
            )
        merged1_questions: list[dict[str, Any]] = []
        merged2_questions: list[dict[str, Any]] = []
        for source_record, binding in zip(source_questions, source_bindings):
            if not isinstance(source_record, Mapping):
                raise ValueError(f"question record形式が不正です: {base_path}")
            projection = project_merge_record(
                source_record,
                originalized=originalized_index.by_binding.get(binding, ()),
                question_type=qtype_index.by_binding.get(binding, ()),
                intent_fallback=intent_index.by_binding.get(binding, ()),
                strict_correct=strict_correct_index.by_binding.get(binding, ()),
                law_context=law_context_index.by_binding.get(binding, ()),
                explanation=explanation_index.by_binding.get(binding, ()),
                question_set=question_set_index.by_binding.get(binding, ()),
                question_issues=question_issue_index.by_binding.get(binding, ()),
            )
            if projection.errors:
                raise RuntimeError(" ".join(projection.errors))
            merged1_questions.append(projection.merged1)
            merged2_questions.append(projection.merged2)
            for key, value in projection.update_counts.items():
                update_counts[key] = update_counts.get(key, 0) + int(value)
            applied_question_issue_targets.update(
                projection.applied_question_issue_targets
            )
        merged1_data = copy.deepcopy(source_data)
        merged1_data["question_bodies"] = merged1_questions
        merged2_data = copy.deepcopy(source_data)
        merged2_data["question_bodies"] = merged2_questions
        if require_answer_result_text:
            ensure_answer_result_text_present(
                data=merged1_data,
                source_path=base_path,
            )

        qtype_view_path = merged_qtype_view_dir / output_filename_for_base(base_path)
        merged1_path = merged1_dir / output_filename_for_base(base_path)
        prepared_records.append(
            (
                base_path,
                merged1_data,
                merged2_data,
                qtype_view_path,
                merged1_path,
            )
        )

    prepared_merged2: list[tuple[Path, dict]] = []
    manual_paths: list[Path] = []
    for _base_path, _merged1_data, merged2_data, _qtype_path, merged1_path in prepared_records:
        # 30_merged_2 は実行時刻付きで新規出力する
        out_path = merged2_dir / output_filename_for_base(
            merged1_path,
            force_new=True,
        )
        valid_data, manual_data = maybe_split_for_manual_output(
            merged2_data,
            out_path,
        )
        if require_answer_result_text:
            ensure_answer_result_text_present(data=valid_data, source_path=out_path)
        prepared_merged2.append((out_path, valid_data))
        if manual_data:
            manual_path = build_manual_output_path(out_path)
            prepared_merged2.append((manual_path, manual_data))
            manual_paths.append(manual_path)

    ensure_all_question_issue_corrections_applied(
        question_issue_paths,
        applied_question_issue_targets,
    )

    archived_counts = commit_prepared_outputs(
        {
            merged_qtype_view_dir: (
                (qtype_view_path, merged1_data)
                for (
                    _base_path,
                    merged1_data,
                    _merged2_data,
                    qtype_view_path,
                    _merged1_path,
                ) in prepared_records
            ),
            merged1_dir: (
                (merged1_path, merged1_data)
                for (
                    _base_path,
                    merged1_data,
                    _merged2_data,
                    _qtype_view_path,
                    merged1_path,
                ) in prepared_records
            ),
            merged2_dir: prepared_merged2,
        }
    )
    for directory, label in (
        (merged_qtype_view_dir, "12_merged_questionType"),
        (merged1_dir, "20_merged_1"),
        (merged2_dir, "30_merged_2"),
    ):
        archived_count = archived_counts.get(directory, 0)
        if archived_count:
            print(
                f"[INFO] {label} old 退避件数: {archived_count} -> "
                f"{directory / 'old'}"
            )
    for manual_path in manual_paths:
        print(f"[WARN] choiceTextList 空のため外出し: {manual_path}")

    originalized_updates = update_counts.get("originalized", 0)
    qtype_updates = update_counts.get("question_type", 0)
    intent_updates = update_counts.get("question_intent", 0)
    true_false_intent_updates = update_counts.get("true_false_intent", 0)
    true_false_correct_choice_updates = update_counts.get(
        "true_false_correct_choice", 0
    )
    exam_year_backfills = update_counts.get("exam_year", 0)
    correct_choice_backfills = update_counts.get("correct_choice_backfill", 0)
    answer_result_override_updates = update_counts.get(
        "answer_result_override", 0
    )
    strict_answer_result_override_updates = update_counts.get(
        "strict_answer_result_override", 0
    )
    strict_correct_updates = update_counts.get("strict_correct_choice", 0)
    law_context_updates = update_counts.get("law_context", 0)
    expl_updates = update_counts.get("explanation", 0)
    qset_updates = update_counts.get("question_set", 0)
    correct_updates = update_counts.get("correct_choice", 0)
    answer_result_updates = update_counts.get("answer_result", 0)
    intent_updates_merged2 = update_counts.get("question_intent_merged2", 0)
    true_false_intent_updates_merged2 = update_counts.get(
        "true_false_intent_merged2", 0
    )
    true_false_correct_choice_updates_merged2 = update_counts.get(
        "true_false_correct_choice_merged2", 0
    )
    exam_year_backfills_merged2 = update_counts.get("exam_year_merged2", 0)
    correct_choice_backfills_merged2 = update_counts.get(
        "correct_choice_backfill_merged2", 0
    )
    question_issue_updates = update_counts.get("question_issue", 0)

    print(f"[INFO] 20_merged_1 生成完了: {merged1_dir}")
    if originalized_updates:
        print(f"[INFO] 05_originalized 更新件数: {originalized_updates}")
    print(f"[INFO] questionType 更新件数: {qtype_updates}")
    print(f"[INFO] questionIntent 更新件数: {intent_updates}")
    print(f"[INFO] true_false questionIntent 正規化件数: {true_false_intent_updates}")
    print(f"[INFO] true_false correctChoiceText 正規化件数: {true_false_correct_choice_updates}")
    if answer_result_override_updates:
        print(f"[INFO] answer_result override 更新件数: {answer_result_override_updates}")
    if strict_answer_result_override_updates:
        print(
            "[INFO] 02a answer_result override 更新件数: "
            f"{strict_answer_result_override_updates}"
        )
    if strict_correct_updates:
        print(f"[INFO] 02a strict correctChoiceText 更新件数: {strict_correct_updates}")
    if law_context_updates:
        print(f"[INFO] law context 更新件数: {law_context_updates}")
    if exam_year_backfills:
        print(f"[INFO] examYear 推定補完件数: {exam_year_backfills}")
    if correct_choice_backfills:
        print(f"[INFO] correctChoiceText(None) 自動補完件数: {correct_choice_backfills}")
    print(f"[INFO] 12_merged_questionType 生成完了: {merged_qtype_view_dir}")

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
    if question_issue_updates:
        print(f"[INFO] 問題報告 correction 更新件数: {question_issue_updates}")


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
    parser.add_argument(
        "--allow-missing-answer-result",
        action="store_true",
        help="Firestore snapshot 由来など、answer_result_text がない既存正誤保持データの merge を許可する",
    )
    args = parser.parse_args()

    try:
        base_dir = resolve_base_dir(args.list_group_id, args.base_dir)
        merge_all(
            args.list_group_id,
            base_dir,
            require_answer_result_text=not args.allow_missing_answer_result,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
