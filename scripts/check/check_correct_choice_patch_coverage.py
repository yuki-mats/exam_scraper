#!/usr/bin/env python3
"""
Validate correctChoiceText patch coverage and format against source questions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id
from scripts.common.question_answer_contract import (
    CORRECT_LABELS,
    INCORRECT_LABELS,
    choice_text_content_alignment_issue,
    official_answer_alignment_issue,
)
from scripts.merge.patch_views import PatchArtifactEntry, apply_question_type
from scripts.merge.record_projection import project_merge_record
from scripts.check.patch_entry_matching import (
    bind_source_records,
    match_patch_entries,
)
from scripts.check import verified_feedback_contract as verified


REQUIRED_FIELDS = [
    "correctChoiceText",
    "original_question_id",
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_source_questions(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [q for q in questions if isinstance(q, dict)]


def get_patch_entries(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError("patch JSON must be an array")
    return [q for q in data if isinstance(q, dict)]


def normalize_snippet_list(source_snippets: Any) -> List[List[str]] | None:
    if not isinstance(source_snippets, list):
        return None
    normalized: List[List[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list) and entry:
            first = entry[0]
            normalized.append([first] if isinstance(first, str) else [])
        elif isinstance(entry, str):
            normalized.append([entry] if entry else [])
        else:
            normalized.append([])
    return normalized


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
    require_full: bool,
    require_snippets: bool,
    require_change_meta: bool,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not patch_entries:
        if require_full:
            errors.append("patch is empty but full output is required")
        else:
            warnings.append("patch is empty")
        return errors, warnings

    matches, identity_errors, identity_warnings = match_patch_entries(
        source_questions,
        patch_entries,
        require_full=require_full,
    )
    errors.extend(identity_errors)
    warnings.extend(identity_warnings)

    for match in matches:
        idx = match.patch_index + 1
        entry = match.patch
        source = match.source
        missing_fields = [k for k in REQUIRED_FIELDS if k not in entry]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

        pid = entry.get("original_question_id")
        if not pid:
            errors.append(f"index {idx}: original_question_id is missing")
            continue

        if (
            "question_url" in entry
            and entry.get("question_url") != source.get("question_url")
        ):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, source.get("question_url"), entry.get("question_url")
                )
            )

        if require_change_meta:
            changed_flag = entry.get("correctChoiceText_changed")
            detail = entry.get("correctChoiceText_change_detail")
            reason = entry.get("correctChoiceText_change_reason")

            if "correctChoiceText_changed" not in entry:
                errors.append(f"index {idx}: correctChoiceText_changed is missing")
            elif not isinstance(changed_flag, bool):
                errors.append(f"index {idx}: correctChoiceText_changed must be a bool")

            if "correctChoiceText_change_detail" not in entry:
                errors.append(f"index {idx}: correctChoiceText_change_detail is missing")
            elif not isinstance(detail, str):
                errors.append(
                    f"index {idx}: correctChoiceText_change_detail must be a string"
                )

            if "correctChoiceText_change_reason" not in entry:
                errors.append(f"index {idx}: correctChoiceText_change_reason is missing")
            elif not isinstance(reason, str):
                errors.append(
                    f"index {idx}: correctChoiceText_change_reason must be a string"
                )

        if require_snippets:
            if "explanation_choice_snippets" not in entry:
                errors.append(f"index {idx}: explanation_choice_snippets is missing")
            else:
                patch_snippets = entry.get("explanation_choice_snippets")
                source_snippets = source.get("explanation_choice_snippets")
                if not isinstance(patch_snippets, list):
                    errors.append(
                        f"index {idx}: explanation_choice_snippets must be a list"
                    )
                else:
                    normalized_source = normalize_snippet_list(source_snippets)
                    if normalized_source is None:
                        errors.append(
                            f"index {idx}: source explanation_choice_snippets is missing"
                        )
                    elif len(patch_snippets) != len(normalized_source):
                        errors.append(
                            "index {}: explanation_choice_snippets length mismatch "
                            "(source={} patch={})".format(
                                idx, len(normalized_source), len(patch_snippets)
                            )
                        )
                    elif patch_snippets != normalized_source:
                        errors.append(
                            f"index {idx}: explanation_choice_snippets mismatch"
                        )

        correct_choices = entry.get("correctChoiceText")
        # choiceTextListとのテキスト一致バリデーション（別問題誤混入防止）
        combined_record = {
            **source,
            **entry,
        }
        text_alignment_issue = choice_text_content_alignment_issue(combined_record)
        if text_alignment_issue:
            errors.append(f"index {idx}: {text_alignment_issue}")

        if isinstance(correct_choices, list):
            if correct_choices and all(
                isinstance(value, str) and value in CORRECT_LABELS | INCORRECT_LABELS
                for value in correct_choices
            ) and any(value not in {"正しい", "間違い"} for value in correct_choices):
                errors.append(
                    f"index {idx}: correctChoiceText must contain only 正しい/間違い"
                )
            if all(isinstance(v, str) and v in {"正しい", "間違い"} for v in correct_choices):
                source_choices = source.get("choiceTextList") or []
                if isinstance(source_choices, list) and len(correct_choices) != len(source_choices):
                    errors.append(
                        "index {}: correctChoiceText length mismatch "
                        "(source={} patch={})".format(
                            idx, len(source_choices), len(correct_choices)
                        )
                    )
        elif not isinstance(correct_choices, str):
            errors.append(f"index {idx}: correctChoiceText must be a list or string")

            source_cct = source.get("correctChoiceText")
            if require_change_meta and isinstance(source_cct, list):
                changed_flag = entry.get("correctChoiceText_changed")
                detail = entry.get("correctChoiceText_change_detail")
                reason = entry.get("correctChoiceText_change_reason")
                if changed_flag is True and correct_choices == source_cct:
                    errors.append(
                        f"index {idx}: correctChoiceText_changed is true but no diff"
                    )
                if changed_flag is False and correct_choices != source_cct:
                    errors.append(
                        f"index {idx}: correctChoiceText_changed is false but differs"
                    )
                if changed_flag is True:
                    if isinstance(detail, str) and not detail.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_detail is empty"
                        )
                    if isinstance(reason, str) and not reason.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_reason is empty"
                        )
                if changed_flag is False:
                    if isinstance(detail, str) and detail.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_detail must be empty"
                        )
                    if isinstance(reason, str) and reason.strip():
                        errors.append(
                            f"index {idx}: correctChoiceText_change_reason must be empty"
                        )
            if (
                not require_full
                and isinstance(source_cct, list)
                and correct_choices == source_cct
            ):
                errors.append(f"index {idx}: correctChoiceText is unchanged from source")

    return errors, warnings


def is_true_false_response_pair(choices: Any) -> bool:
    """Recognize two response labels, not two substantive answer choices."""
    if not isinstance(choices, list) or len(choices) != 2:
        return False
    if not all(isinstance(choice, str) for choice in choices):
        return False
    first, second = (choice.strip() for choice in choices)
    # The common sets define persisted verdicts; source screens can use
    # additional, display-only labels and symbols for the same operation.
    families = (
        (CORRECT_LABELS, INCORRECT_LABELS | {"誤っている", "正しくない"}),
        (frozenset({"適切", "適"}), frozenset({"不適切", "不適"})),
        (frozenset({"○", "◯"}), frozenset({"×", "✕"})),
    )
    return any((first in yes and second in no) or (first in no and second in yes)
               for yes, no in families)


def check_originalized_projection(
    source_questions: list[dict[str, Any]],
    patch_path: Path,
    originalized_path: Path,
    question_type_path: Path | None,
    require_full: bool,
    require_snippets: bool,
    require_change_meta: bool,
) -> int:
    """Validate 23 against the canonical 05 -> 01 projection of each source."""
    def matched(path: Path, label: str, full: bool = False):
        if not path.exists():
            raise FileNotFoundError(f"{label} patch not found: {path}")
        entries = get_patch_entries(load_json(path))
        matches, errors, warnings = match_patch_entries(source_questions, entries, require_full=full)
        for warning in warnings:
            print(f"[WARN] {label}: {warning}")
        if errors:
            raise ValueError("; ".join(f"{label}: {error}" for error in errors))
        for match in matches:
            source, entry = match.source, match.patch
            binding_groups = (
                ("sourceQuestionKey", "source_question_key"),
                ("reviewQuestionId", "review_question_id"),
                ("sourceRecordRef", "source_record_ref"),
            )
            # An entirely legacy entry may still use its unambiguous old ID.
            # A partly supplied binding must never fall back to a single alias.
            supplied = [any(key in entry for key in group) for group in binding_groups]
            if any(supplied) and not all(supplied):
                raise ValueError(f"{label}: incomplete source identity binding")
            for group in binding_groups:
                expected = next((source[key] for key in group if source.get(key)), None)
                for key in group:
                    if key in entry and (not entry[key] or expected is None or entry[key] != expected):
                        raise ValueError(f"{label}: {key} differs from source binding")
            for key in ("original_question_id", "public_question_id", "source_public_question_id", "originalQuestionId", "uploadOriginalQuestionId"):
                if key not in entry:
                    continue
                expected = source.get(key)
                if expected is None and key in {"original_question_id", "source_public_question_id"}:
                    expected = source.get("public_question_id")
                if expected is None or not entry[key] or entry[key] != expected:
                    raise ValueError(f"{label}: {key} differs from source identity")
            if "question_url" in entry and (not source.get("question_url")
                    or entry["question_url"] != source["question_url"]):
                raise ValueError(f"{label}: question_url differs from source identity")
            if label == "05":
                source_choices = source.get("choiceTextList")
                target_choices = entry.get("choiceTextList")
                labels_only = is_true_false_response_pair(source_choices)
                if (isinstance(source_choices, list) and source_choices
                        and isinstance(target_choices, list)
                        and len(source_choices) != len(target_choices)
                        and not (labels_only and len(target_choices) == 1)):
                    raise ValueError("05: choice count differs from source without a true/false label projection")
            if label == "05" and "sourceUniqueKeys" in entry:
                actual = entry["sourceUniqueKeys"]
                source_keys = source.get("sourceUniqueKeys")
                public_id = source.get("public_question_id") or source.get("original_question_id")
                choices = entry.get("choiceTextList")
                derived = ([f"{public_id}:choice:{index + 1}" for index in range(len(choices))]
                           if public_id and isinstance(choices, list) else None)
                if (not isinstance(actual, list) or not actual
                        or not all(isinstance(key, str) and key for key in actual)
                        or len(set(actual)) != len(actual)):
                    raise ValueError("05: invalid sourceUniqueKeys")
                if actual != source_keys and actual != derived:
                    raise ValueError("05: sourceUniqueKeys differ from source order or canonical projection")
        return {match.source_index: PatchArtifactEntry(path=path, entry=match.patch) for match in matches}

    try:
        originals = matched(originalized_path, "05")
        types = matched(question_type_path, "10") if question_type_path is not None else {}
        strict = matched(patch_path, "23", require_full)
        projected = []
        for index, source in enumerate(source_questions):
            if index in strict and index not in originals:
                raise ValueError(f"05: missing originalized record for source index {index + 1}")
            orig_entries = (originals[index],) if index in originals else ()
            type_entries = (types[index],) if index in types else ()
            # Compare against the pre-23 view, preserving change-metadata semantics.
            prior = project_merge_record(source, originalized=orig_entries, question_type=type_entries)
            if prior.errors:
                raise ValueError("; ".join(prior.errors))
            projected.append(prior.merged1)
            for label, candidate in (("10", types.get(index)), ("23", strict.get(index))):
                if candidate is None or "sourceUniqueKeys" not in candidate.entry:
                    continue
                keys = candidate.entry["sourceUniqueKeys"]
                if (not isinstance(keys, list) or not keys
                        or not all(isinstance(key, str) and key for key in keys)
                        or (keys != source.get("sourceUniqueKeys")
                            and keys != prior.merged1.get("sourceUniqueKeys"))):
                    raise ValueError(f"{label}: sourceUniqueKeys differ from source or canonical projection")
            if index in strict:
                final = project_merge_record(source, originalized=orig_entries,
                    question_type=type_entries, strict_correct=(strict[index],))
                if final.errors:
                    raise ValueError("; ".join(final.errors))
                if final.update_counts.get("strict_correct_choice") != 1:
                    raise ValueError(f"23: patch did not apply for source index {index + 1}")
                record = final.merged1
                before = prior.merged1.get("correctChoiceText")
                after = record.get("correctChoiceText")
                if (isinstance(before, list) and isinstance(after, list)
                        and all(value in {"正しい", "間違い"} for value in before)
                        and all(value in {"正しい", "間違い"} for value in after)
                        and before != after):
                    raise ValueError(f"source index {index + 1}: 05 and 23 correctChoiceText disagree")
                issue = choice_text_content_alignment_issue(record) or official_answer_alignment_issue(record)
                if issue:
                    raise ValueError(f"source index {index + 1}: {issue}")
        errors, warnings = compare_entries(projected, get_patch_entries(load_json(patch_path)),
            require_full, require_snippets, require_change_meta)
    except (FileNotFoundError, ValueError, TypeError, KeyError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    for warning in warnings:
        print(f"[WARN] {warning}")
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("[OK] coverage check passed")
    return 0


def check_pair(
    source_path: Path,
    patch_path: Path,
    require_full: bool,
    require_snippets: bool,
    require_change_meta: bool,
    question_type_patch_path: Path | None = None,
    originalized_patch_path: Path | None = None,
) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_data = load_json(source_path)
    source_questions = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )
    if originalized_patch_path is not None:
        for index, raw in enumerate(get_source_questions(source_data)):
            expected_ref = f"{source_path.name}#{index}"
            for field in ("sourceRecordRef", "source_record_ref"):
                if field in raw and raw[field] != expected_ref:
                    print(f"[ERROR] source: {field} does not identify the source record")
                    return 1
            keys = raw.get("sourceUniqueKeys")
            if keys is not None and (not isinstance(keys, list) or not keys
                    or not all(isinstance(key, str) and key for key in keys)
                    or len(keys) != len(set(keys))):
                print("[ERROR] source: invalid sourceUniqueKeys")
                return 1
        return check_originalized_projection(
            source_questions, patch_path, originalized_patch_path,
            question_type_patch_path, require_full, require_snippets,
            require_change_meta,
        )
    if question_type_patch_path is not None:
        if not question_type_patch_path.exists():
            print(f"[ERROR] questionType patch not found: {question_type_patch_path}")
            return 2
        question_type_entries = get_patch_entries(load_json(question_type_patch_path))
        question_type_matches, identity_errors, _ = match_patch_entries(
            source_questions,
            question_type_entries,
            require_full=False,
        )
        if identity_errors:
            for error in identity_errors:
                print(f"[ERROR] questionType patch: {error}")
            return 1
        question_type_map = {
            str(review_question_id(match.source)): match.patch
            for match in question_type_matches
            if review_question_id(match.source)
        }
        apply_question_type(source_data, question_type_map)
    patch_data = load_json(patch_path)

    source_questions = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )
    patch_entries = get_patch_entries(patch_data)

    errors, warnings = compare_entries(
        source_questions,
        patch_entries,
        require_full,
        require_snippets,
        require_change_meta,
    )
    for warn in warnings:
        print(f"[WARN] {warn}")
    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1

    print("[OK] coverage check passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate correctChoiceText patch coverage and format."
    )
    parser.add_argument("--source", help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        help="Path to *_correctChoiceText_fixed_YYYYMMDD_HHMM.json (旧形式 *_correctChoiceText_fixed.json も可)",
    )
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="Require patch to include all questions in source order.",
    )
    parser.add_argument(
        "--require-snippets",
        action="store_true",
        help="Require explanation_choice_snippets to match the source.",
    )
    parser.add_argument(
        "--require-change-meta",
        action="store_true",
        help="Require correctChoiceText change metadata to be present and consistent.",
    )
    parser.add_argument(
        "--question-type-patch",
        help="Apply the corresponding 10_questionType_fixed patch before validating choice counts.",
    )
    parser.add_argument(
        "--originalized-patch",
        help="Apply 05 and 10 via canonical source-bound merge projection before validating 23.",
    )
    verified.add_arguments(parser)
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    if verified.selected_mode(parser, args, argv, legacy_options={
            '--source', '--patch', '--require-full', '--require-snippets',
            '--require-change-meta', '--question-type-patch', '--originalized-patch'}):
        return verified.run_cli('coverage', args)
    if args.source is None or args.patch is None:
        parser.error('--source and --patch are required in legacy mode')
    return check_pair(
        Path(args.source),
        Path(args.patch),
        args.require_full,
        args.require_snippets,
        args.require_change_meta,
        Path(args.question_type_patch) if args.question_type_patch else None,
        Path(args.originalized_patch) if args.originalized_patch else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
