from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.common.question_identity import review_question_id


PATCH_SCHEMA_VERSION = "question-issue-correction/v1"
PATCH_ORIGIN = "user_problem_report"
PATCHABLE_FIELDS = frozenset(
    {
        "questionBodyText",
        "choiceTextList",
        "questionType",
        "questionIntent",
        "correctChoiceText",
        "answer_result_text",
        "answer_result_inferred_correct_choice_numbers",
        "explanationText",
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "lawReferences",
        "lawRevisionFacts",
        "isLawRelated",
        "lawGroundedExplanationNotNeeded",
        "questionSetId",
        "choiceQuestionSetIds",
        "questionSetIds",
        "questionImageStorageUrls",
        "originalQuestionChoiceImageUrls",
        "explanationImageUrls",
    }
)
HASH_FIELDS = tuple(
    sorted(
        PATCHABLE_FIELDS
        | {
            "original_question_id",
            "public_question_id",
            "question_url",
            "list_group_id",
            "qualificationId",
            "examYear",
            "examLabel",
        }
    )
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def question_record_hash(record: Mapping[str, Any]) -> str:
    stable_record = {
        field: record.get(field)
        for field in HASH_FIELDS
        if field in record
    }
    return sha256_json(stable_record)


def load_correction_patch(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"correction patch must be an object: {path}")
    if payload.get("schemaVersion") != PATCH_SCHEMA_VERSION:
        raise ValueError(f"unsupported correction patch schema: {path}")
    if payload.get("origin") != PATCH_ORIGIN:
        raise ValueError(f"invalid correction patch origin: {path}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"correction patch entries must be non-empty: {path}")
    return payload


def _entries_by_id(payload: Mapping[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    entries_by_id: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(payload.get("entries", []), start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {index} must be an object: {path}")
        original_id = str(entry.get("original_question_id") or "").strip()
        if not original_id:
            raise ValueError(f"entry {index} missing original_question_id: {path}")
        if original_id in entries_by_id:
            raise ValueError(f"duplicate original_question_id={original_id}: {path}")
        entries_by_id[original_id] = entry
    return entries_by_id


def apply_question_issue_correction_patch(
    data: dict[str, Any],
    patch_path: Path,
    *,
    applied_targets: set[str] | None = None,
) -> int:
    payload = load_correction_patch(patch_path)
    entries_by_id = _entries_by_id(payload, patch_path)
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"question_bodies not found while applying {patch_path}")

    update_count = 0
    for question in questions:
        if not isinstance(question, dict):
            continue
        original_id = review_question_id(question)
        if not original_id:
            continue
        entry = entries_by_id.get(str(original_id))
        if entry is None:
            continue
        expected_hash = str(entry.get("expectedBeforeHash") or "").strip()
        actual_hash = question_record_hash(question)
        if expected_hash != actual_hash:
            raise RuntimeError(
                "question issue correction input hash mismatch: "
                f"question={original_id} expected={expected_hash} actual={actual_hash} "
                f"patch={patch_path}"
            )
        changes = entry.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError(f"changes must be non-empty: question={original_id} patch={patch_path}")
        unknown_fields = sorted(set(changes) - PATCHABLE_FIELDS)
        if unknown_fields:
            raise ValueError(
                f"unsupported correction fields {unknown_fields}: question={original_id} patch={patch_path}"
            )
        changed = False
        for field, value in changes.items():
            if question.get(field) == value:
                continue
            question[field] = value
            changed = True
        if changed:
            update_count += 1
        if applied_targets is not None:
            applied_targets.add(f"{patch_path.resolve()}::{original_id}")
    return update_count


def apply_question_issue_correction_paths(
    data: dict[str, Any],
    patch_paths: Iterable[Path],
    *,
    applied_targets: set[str] | None = None,
) -> int:
    updates = 0
    for patch_path in sorted(patch_paths, key=lambda path: path.name):
        updates += apply_question_issue_correction_patch(
            data,
            patch_path,
            applied_targets=applied_targets,
        )
    return updates


def ensure_all_question_issue_corrections_applied(
    patch_paths: Iterable[Path],
    applied_targets: set[str],
) -> None:
    required_targets: set[str] = set()
    for patch_path in patch_paths:
        payload = load_correction_patch(patch_path)
        for original_id in _entries_by_id(payload, patch_path):
            required_targets.add(f"{patch_path.resolve()}::{original_id}")
    missing = sorted(required_targets - applied_targets)
    if missing:
        raise RuntimeError(
            "question issue correction targets not found in merged inputs: "
            + ", ".join(missing)
        )
