from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.common.question_identity import (
    IdentityCandidateIndex,
    SOURCE_IDENTITY_BINDING_FIELDS,
    SourceIdentityBinding,
    SourceRecordIdentity,
    resolve_identity_candidates,
    review_question_id,
    source_identity_aliases,
    workflow_identity_aliases,
)


PATCH_SCHEMA_VERSION = "question-issue-correction/v1"
PATCH_ORIGIN = "user_problem_report"
DEFAULT_CATEGORY_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "question_issue_reports.json"
)
SOURCE_IDENTITY_ENTRY_FIELDS = frozenset(SOURCE_IDENTITY_BINDING_FIELDS)
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
        "questionLearningPatternId",
        "questionSetId",
        "choiceQuestionSetIds",
        "questionSetIds",
        "questionImageStorageUrls",
        "originalQuestionChoiceImageUrls",
        "explanationImageUrls",
    }
)
IDENTITY_HASH_FIELDS = frozenset(
    {
        "original_question_id",
        "public_question_id",
        "question_url",
        "list_group_id",
        "qualificationId",
        "examYear",
        "examLabel",
    }
)
HASH_FIELDS = tuple(sorted(PATCHABLE_FIELDS | IDENTITY_HASH_FIELDS))


def selected_question_issue_correction_paths(directory: Path) -> list[Path]:
    """Return correction inputs shared by physical and logical projection."""

    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob("*.json")
        if path.is_file() and not path.name.endswith("_invalid.json")
    )


@dataclass(frozen=True)
class QuestionIssueCorrectionEntry:
    path: Path
    entry: dict[str, Any]
    expected_hash_fields: tuple[str, ...] = HASH_FIELDS
    allows_current_value_certification: bool = False


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def question_record_hash(
    record: Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
) -> str:
    selected_fields = HASH_FIELDS if fields is None else tuple(fields)
    stable_record = {
        field: record.get(field)
        for field in selected_fields
        if field in record
    }
    return sha256_json(stable_record)


def expected_before_hash_fields(
    category_config: Mapping[str, Any],
) -> tuple[str, ...]:
    configured = category_config.get("expectedBeforeHashFields")
    if (
        not isinstance(configured, list)
        or not configured
        or any(
            not isinstance(field, str) or not field.strip()
            for field in configured
        )
    ):
        raise ValueError(
            "question issue category expectedBeforeHashFields must be a non-empty list"
        )
    allowed_changes = {
        str(field)
        for field in category_config.get("allowedChangeFields") or []
        if str(field)
    }
    selected = {str(field) for field in configured}
    missing_change_fields = sorted(allowed_changes - selected)
    if missing_change_fields:
        raise ValueError(
            "question issue category hash must include every allowed change field: "
            + ", ".join(missing_change_fields)
        )
    return tuple(sorted(selected | IDENTITY_HASH_FIELDS))


def load_category_configs(
    config_path: Path = DEFAULT_CATEGORY_CONFIG_PATH,
) -> dict[str, Mapping[str, Any]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    categories = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(categories, dict):
        raise ValueError(f"question issue category config is invalid: {config_path}")
    return {
        str(category): config
        for category, config in categories.items()
        if isinstance(config, Mapping)
    }


def question_issue_record_hash(
    record: Mapping[str, Any],
    category_config: Mapping[str, Any],
) -> str:
    return question_record_hash(
        record,
        fields=expected_before_hash_fields(category_config),
    )


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


def build_question_issue_correction_index(
    paths: Iterable[Path],
    sources: Iterable[SourceRecordIdentity],
    *,
    config_path: Path = DEFAULT_CATEGORY_CONFIG_PATH,
) -> IdentityCandidateIndex:
    source_records = tuple(sources)
    category_configs = load_category_configs(config_path)
    candidates: list[QuestionIssueCorrectionEntry] = []
    invalid_messages: list[str] = []
    for path in sorted(paths, key=lambda value: value.name):
        try:
            payload = load_correction_patch(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            invalid_messages.append(str(exc))
            continue
        category = str(payload.get("category") or "")
        category_config = category_configs.get(category)
        if category_config is None:
            invalid_messages.append(f"unsupported question issue category: {category}")
            continue
        try:
            hash_fields = expected_before_hash_fields(category_config)
        except ValueError as exc:
            invalid_messages.append(str(exc))
            continue
        for position, entry in enumerate(payload["entries"], start=1):
            if not isinstance(entry, dict):
                invalid_messages.append(
                    f"question issue correction entry {position} must be an object: {path}"
                )
                continue
            identity_fields = set(entry) & SOURCE_IDENTITY_ENTRY_FIELDS
            if identity_fields and identity_fields != SOURCE_IDENTITY_ENTRY_FIELDS:
                invalid_messages.append(
                    "question issue correction source identity fields must "
                    f"contain all three fields: entry={position} path={path}"
                )
                continue
            candidates.append(
                QuestionIssueCorrectionEntry(
                    path=path,
                    entry=dict(entry),
                    expected_hash_fields=hash_fields,
                    allows_current_value_certification=(
                        category == "correct_answer"
                    ),
                )
            )

    index = resolve_identity_candidates(
        candidates,
        sources=source_records,
        record_of=lambda candidate: candidate.entry,
        aliases_of=lambda record: (
            source_identity_aliases(record)
            | workflow_identity_aliases(record)
        ),
        source_stem_of=lambda _candidate: "",
        label="question issue correction",
    )
    if not invalid_messages:
        return index
    return IdentityCandidateIndex(
        by_binding=index.by_binding,
        errors_by_binding={
            source.binding: tuple(
                dict.fromkeys(
                    [
                        *index.errors_by_binding.get(source.binding, ()),
                        *invalid_messages,
                    ]
                )
            )
            for source in source_records
        },
        unmatched_count=index.unmatched_count,
        unmatched_candidates=index.unmatched_candidates,
    )


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


def question_issue_correction_target(
    path: Path,
    entry: Mapping[str, Any],
) -> str:
    binding = SourceIdentityBinding.from_mapping(entry)
    identity = (
        "|".join(binding.as_tuple())
        if binding.is_complete()
        else str(entry.get("original_question_id") or "").strip()
    )
    return f"{path.resolve()}::{identity}"


def is_stale_current_value_certification(
    question: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    expected_hash_fields: Iterable[str] | None = None,
    allow_current_value_certification: bool = False,
) -> bool:
    """Return whether a certification is bound to an older question input.

    A current-value certification never changes question content.  When an
    upstream maintenance layer changes its hash-bound input, the certification
    must stop approving that newer question, but it must not prevent the normal
    maintenance flow from producing the newer question.  Ordinary correction
    patches remain fail-closed through ``apply_question_issue_correction_entry``.
    """

    if entry.get("certifiesCurrentValues") is not True:
        return False
    changes = entry.get("changes")
    if (
        not allow_current_value_certification
        or not isinstance(changes, Mapping)
        or set(changes) != {"correctChoiceText"}
    ):
        return False
    expected_hash = str(entry.get("expectedBeforeHash") or "").strip()
    actual_hash = question_record_hash(
        question,
        fields=expected_hash_fields,
    )
    return expected_hash != actual_hash


def apply_question_issue_correction_entry(
    question: dict[str, Any],
    entry: Mapping[str, Any],
    patch_path: Path,
    *,
    expected_hash_fields: Iterable[str] | None = None,
    allow_current_value_certification: bool = False,
) -> bool:
    original_id = str(entry.get("original_question_id") or "").strip()
    if not original_id:
        raise ValueError(f"entry missing original_question_id: {patch_path}")
    expected_hash = str(entry.get("expectedBeforeHash") or "").strip()
    actual_hash = question_record_hash(
        question,
        fields=expected_hash_fields,
    )
    if expected_hash != actual_hash:
        raise RuntimeError(
            "question issue correction input hash mismatch: "
            f"question={original_id} expected={expected_hash} actual={actual_hash} "
            f"patch={patch_path}"
        )
    changes = entry.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError(
            f"changes must be non-empty: question={original_id} patch={patch_path}"
        )
    unknown_fields = sorted(set(changes) - PATCHABLE_FIELDS)
    if unknown_fields:
        raise ValueError(
            f"unsupported correction fields {unknown_fields}: "
            f"question={original_id} patch={patch_path}"
        )
    if entry.get("certifiesCurrentValues") is True:
        if (
            not allow_current_value_certification
            or set(changes) != {"correctChoiceText"}
        ):
            raise ValueError(
                "current-value certification is limited to "
                f"correct_answer.correctChoiceText: question={original_id} "
                f"patch={patch_path}"
            )
        if question.get("correctChoiceText") != changes["correctChoiceText"]:
            raise RuntimeError(
                "certified correctChoiceText does not match current value: "
                f"question={original_id} patch={patch_path}"
            )
        return True
    changed = False
    for field, value in changes.items():
        if question.get(field) == value:
            continue
        question[field] = value
        changed = True
    return changed


def apply_question_issue_correction_patch(
    data: dict[str, Any],
    patch_path: Path,
    *,
    applied_targets: set[str] | None = None,
    config_path: Path = DEFAULT_CATEGORY_CONFIG_PATH,
) -> int:
    payload = load_correction_patch(patch_path)
    category_configs = load_category_configs(config_path)
    category = str(payload.get("category") or "")
    category_config = category_configs.get(category)
    if category_config is None:
        raise ValueError(f"unsupported question issue category: {category}")
    hash_fields = expected_before_hash_fields(category_config)
    entries_by_id = _entries_by_id(payload, patch_path)
    if any(
        SourceIdentityBinding.from_mapping(entry).is_complete()
        and SOURCE_IDENTITY_ENTRY_FIELDS.issubset(entry)
        for entry in entries_by_id.values()
    ):
        raise ValueError(
            "exact source identity correction requires source inventory index: "
            f"{patch_path}"
        )
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
        if apply_question_issue_correction_entry(
            question,
            entry,
            patch_path,
            expected_hash_fields=hash_fields,
            allow_current_value_certification=(category == "correct_answer"),
        ):
            update_count += 1
        if applied_targets is not None:
            applied_targets.add(
                question_issue_correction_target(patch_path, entry)
            )
    return update_count


def apply_question_issue_correction_index(
    data: dict[str, Any],
    index: IdentityCandidateIndex,
    source_bindings: Iterable[SourceIdentityBinding],
    *,
    applied_targets: set[str] | None = None,
) -> int:
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies not found while applying correction index")
    bindings = tuple(source_bindings)
    if len(questions) != len(bindings):
        raise ValueError(
            "question issue correction source binding count mismatch: "
            f"questions={len(questions)} bindings={len(bindings)}"
        )

    update_count = 0
    for question, binding in zip(questions, bindings):
        if not isinstance(question, dict):
            continue
        errors = index.errors_by_binding.get(binding, ())
        if errors:
            raise RuntimeError(" ".join(errors))
        for candidate in index.by_binding.get(binding, ()):
            if apply_question_issue_correction_entry(
                question,
                candidate.entry,
                candidate.path,
                expected_hash_fields=candidate.expected_hash_fields,
                allow_current_value_certification=(
                    candidate.allows_current_value_certification
                ),
            ):
                update_count += 1
            if applied_targets is not None:
                applied_targets.add(
                    question_issue_correction_target(
                        candidate.path,
                        candidate.entry,
                    )
                )
    return update_count


def apply_question_issue_correction_paths(
    data: dict[str, Any],
    patch_paths: Iterable[Path],
    *,
    applied_targets: set[str] | None = None,
    config_path: Path = DEFAULT_CATEGORY_CONFIG_PATH,
) -> int:
    updates = 0
    for patch_path in sorted(patch_paths, key=lambda path: path.name):
        updates += apply_question_issue_correction_patch(
            data,
            patch_path,
            applied_targets=applied_targets,
            config_path=config_path,
        )
    return updates


def ensure_all_question_issue_corrections_applied(
    patch_paths: Iterable[Path],
    applied_targets: set[str],
    stale_current_value_certification_targets: Iterable[str] = (),
) -> None:
    required_targets: set[str] = set()
    for patch_path in patch_paths:
        payload = load_correction_patch(patch_path)
        for entry in payload["entries"]:
            if not isinstance(entry, dict):
                raise ValueError(f"correction patch entry must be an object: {patch_path}")
            required_targets.add(
                question_issue_correction_target(patch_path, entry)
            )
    stale_targets = set(stale_current_value_certification_targets)
    missing = sorted(required_targets - applied_targets - stale_targets)
    if missing:
        raise RuntimeError(
            "question issue correction targets not found in merged inputs: "
            + ", ".join(missing)
        )
