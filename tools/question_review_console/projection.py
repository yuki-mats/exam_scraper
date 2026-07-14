from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.common.question_identity import review_question_id
from scripts.merge.merge_utils import select_latest_patch_files
from scripts.merge.patch_views import EXPLANATION_FIELDS, extract_patch_entries
from scripts.merge.question_issue_corrections import (
    apply_question_issue_correction_paths,
)


STAGE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("questionType", "10_questionType_fixed", "questionType_fixed"),
    ("questionIntent", "15_correctChoiceText_fixed", "correctChoiceText_fixed"),
    ("correctChoice", "23_correctChoiceText_fixed", "correctChoiceText_fixed"),
    ("lawContext", "18_law_context_prepared", "lawContext_prepared"),
    ("explanation", "21_explanationText_added", "explanationText_added"),
    ("questionSet", "22_questionSetId_linked", "questionSetId_linked"),
)

QUESTION_TYPE_FIELDS = (
    "questionType",
    "questionBodyText",
    "choiceTextList",
    "sourceUniqueKeys",
    "originalQuestionId",
    "original_question_id",
    "uploadOriginalQuestionId",
    "firestoreQuestionIds",
    "firestoreSourceQuestions",
    "sourceConflictReviewDecision",
    "sourceContentConflictPolicy",
)
QUESTION_INTENT_FIELDS = (
    "questionIntent",
    "correctChoiceText",
    "answer_result_text",
    "answer_result_inferred_correct_choice_numbers",
)
LAW_CONTEXT_FIELDS = (
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "lawReferences",
    "lawContextForExplanation",
)
QUESTION_SET_FIELDS = (
    "questionSetId",
    "choiceQuestionSetIds",
    "questionSetIds",
)
CORRECT_CHOICE_FIELDS = (
    "correctChoiceText",
    "questionIntent",
    "answer_result_text",
    "answer_result_inferred_correct_choice_numbers",
)
PROJECTED_COMPARE_FIELDS = tuple(
    dict.fromkeys(
        (
            *QUESTION_TYPE_FIELDS,
            *QUESTION_INTENT_FIELDS,
            *LAW_CONTEXT_FIELDS,
            *EXPLANATION_FIELDS,
            *QUESTION_SET_FIELDS,
            *CORRECT_CHOICE_FIELDS,
        )
    )
)


@dataclass(frozen=True)
class PatchEntry:
    path: Path
    entry: dict[str, Any]


@dataclass(frozen=True)
class ProjectionResult:
    record: dict[str, Any]
    applied_files: tuple[str, ...]
    errors: tuple[str, ...]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_verdict(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"正しい", "正解", "○", "〇", "true", "True"}:
        return "正しい"
    if text in {"間違い", "不正解", "誤り", "×", "false", "False"}:
        return "間違い"
    return text


def record_aliases(record: Mapping[str, Any]) -> set[str]:
    aliases = record_identity_aliases(record)
    stable = review_question_id(record)
    if stable:
        aliases.add(stable)
        if stable.startswith("firestore:"):
            aliases.update(value for value in stable.removeprefix("firestore:").split(",") if value)
    for field in ("question_url",):
        value = record.get(field)
        if value:
            aliases.add(str(value))
    return aliases


def record_identity_aliases(record: Mapping[str, Any]) -> set[str]:
    """Return only identifiers suitable for write-scope enforcement."""

    aliases: set[str] = set()
    for field in (
        "original_question_id",
        "public_question_id",
        "originalQuestionId",
        "questionId",
        "reviewQuestionId",
        "review_question_id",
        "sourceQuestionKey",
        "source_question_key",
        "uploadOriginalQuestionId",
    ):
        value = record.get(field)
        if value:
            aliases.add(str(value))
    firestore_ids = record.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        values = [str(value) for value in firestore_ids if value]
        aliases.update(values)
        if values:
            aliases.add("firestore:" + ",".join(values))
    return aliases


def extract_records(payload: Any) -> list[dict[str, Any]]:
    return [dict(record) for record in extract_patch_entries(payload)]


def selected_patch_paths(group_dir: Path, subdir: str, tag: str) -> list[Path]:
    patch_dir = group_dir / subdir
    if not patch_dir.is_dir():
        return []
    return select_latest_patch_files(sorted(patch_dir.glob("*.json")), tag)


def build_stage_maps(group_dir: Path) -> dict[str, dict[str, PatchEntry]]:
    maps: dict[str, dict[str, PatchEntry]] = {}
    for stage, subdir, tag in STAGE_SPECS:
        mapping: dict[str, PatchEntry] = {}
        for path in selected_patch_paths(group_dir, subdir, tag):
            for entry in extract_records(load_json(path)):
                wrapped = PatchEntry(path=path, entry=entry)
                for alias in record_aliases(entry):
                    mapping[alias] = wrapped
        maps[stage] = mapping
    return maps


def find_patch_entry(
    mapping: Mapping[str, PatchEntry], aliases: Iterable[str]
) -> PatchEntry | None:
    matches = {
        (str(mapping[alias].path), id(mapping[alias].entry)): mapping[alias]
        for alias in aliases
        if alias in mapping
    }
    if not matches:
        return None
    return sorted(matches.values(), key=lambda value: str(value.path))[-1]


def _copy_fields(target: dict[str, Any], source: Mapping[str, Any], fields: Iterable[str]) -> None:
    for field in fields:
        if field in source and source[field] is not None:
            target[field] = copy.deepcopy(source[field])


def project_record(
    base_record: Mapping[str, Any],
    aliases: set[str],
    stage_maps: Mapping[str, Mapping[str, PatchEntry]],
    question_issue_paths: Iterable[Path],
) -> ProjectionResult:
    record = copy.deepcopy(dict(base_record))
    applied: list[str] = []
    errors: list[str] = []
    field_sets = {
        "questionType": QUESTION_TYPE_FIELDS,
        "questionIntent": QUESTION_INTENT_FIELDS,
        "lawContext": LAW_CONTEXT_FIELDS,
        "explanation": EXPLANATION_FIELDS,
        "questionSet": QUESTION_SET_FIELDS,
        "correctChoice": CORRECT_CHOICE_FIELDS,
    }
    for stage, _, _ in STAGE_SPECS:
        patch = find_patch_entry(stage_maps.get(stage, {}), aliases)
        if patch is None:
            continue
        _copy_fields(record, patch.entry, field_sets[stage])
        applied.append(str(patch.path))

    wrapper = {"question_bodies": [record]}
    for path in sorted(question_issue_paths):
        try:
            updated = apply_question_issue_correction_paths(wrapper, [path])
        except (RuntimeError, ValueError) as exc:
            if aliases & _question_issue_aliases(path):
                errors.append(str(exc))
            continue
        if updated:
            applied.append(str(path))

    return ProjectionResult(
        record=record,
        applied_files=tuple(dict.fromkeys(applied)),
        errors=tuple(errors),
    )


def _question_issue_aliases(path: Path) -> set[str]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return set()
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return set()
    return {
        str(entry.get("original_question_id"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("original_question_id")
    }


def record_diff(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
    fields: Iterable[str] = PROJECTED_COMPARE_FIELDS,
) -> list[str]:
    if left is None or right is None:
        return []
    return [field for field in fields if left.get(field) != right.get(field)]


def explanation_prefix_matches(verdict: Any, explanation: Any) -> bool:
    normalized = normalize_verdict(verdict)
    text = str(explanation or "").strip()
    match = re.match(
        r"^(?:(?:この)?(?:選択肢(?:\d+)?|記述)(?:の記述)?は\s*)?[「『]?"
        r"(正しい|正解|間違い|誤り|不正解)",
        text,
    )
    if normalized in {"正しい", "間違い"}:
        return bool(match and normalize_verdict(match.group(1)) == normalized)
    return True


def source_question_key(
    qualification: str,
    list_group_id: str,
    record: Mapping[str, Any],
) -> str:
    existing = str(record.get("sourceQuestionKey") or "").strip()
    if existing:
        return existing
    question_label = str(record.get("questionLabel") or "").strip()
    number_match = re.search(r"(\d+)", question_label)
    number = int(number_match.group(1)) if number_match else 0
    exam_label = str(record.get("examLabel") or "")
    if qualification in {"gas-shunin-kou", "gas-shunin-otsu"} and number:
        grade = "kou" if qualification.endswith("-kou") else "otsu"
        section = "law" if "法令" in exam_label else "question"
        return f"gas-shunin:{grade}:{list_group_id}:{section}:q{number:02d}"
    label = question_label or str(record.get("original_question_id") or "question")
    return f"{qualification}:{list_group_id}:{label}"


def review_key(
    qualification: str,
    list_group_id: str,
    source_stem: str,
    record: Mapping[str, Any],
) -> str:
    original_id = review_question_id(record) or sha256_json(record)[:16]
    return f"{qualification}:{list_group_id}:{source_stem}:{original_id}"


def api_question_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
