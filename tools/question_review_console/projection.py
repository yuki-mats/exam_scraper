from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from scripts.common.question_identity import (
    IdentityCandidateIndex,
    SourceIdentityBinding,
    SourceRecordIdentity,
    review_question_id,
    resolve_identity_candidates,
    source_question_key,
    source_identity_aliases,
    workflow_identity_aliases,
)
from scripts.merge.merge_utils import select_latest_patch_files
from scripts.merge.patch_views import (
    EXPLANATION_FIELDS,
    PatchArtifactEntry,
    build_layered_patch_index_from_paths,
    extract_patch_entries,
)
from scripts.merge.record_projection import project_merge_record
from scripts.merge.question_issue_corrections import (
    PATCHABLE_FIELDS,
    QuestionIssueCorrectionEntry,
    apply_question_issue_correction_paths,
    build_question_issue_correction_index,
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
            *sorted(PATCHABLE_FIELDS),
            "examYear",
            "manualQuestionIntentOverride",
        )
    )
)


PatchEntry = PatchArtifactEntry


@dataclass(frozen=True)
class ProjectionResult:
    record: dict[str, Any]
    applied_files: tuple[str, ...]
    errors: tuple[str, ...]


class IdentityResolutionError(ValueError):
    pass


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
    """Compatibility union for existing record-scope callers.

    New code should choose ``source_identity_aliases`` or
    ``workflow_identity_aliases`` according to the artifact being checked.
    """

    return source_identity_aliases(record) | workflow_identity_aliases(record)


def build_identity_candidate_index(
    candidates: Iterable[Any],
    *,
    sources: Iterable[SourceRecordIdentity],
    record_of: Callable[[Any], Mapping[str, Any]],
    source_stem_of: Callable[[Any], str],
    label: str,
) -> IdentityCandidateIndex:
    return resolve_identity_candidates(
        candidates,
        sources=sources,
        record_of=record_of,
        aliases_of=record_aliases,
        source_stem_of=source_stem_of,
        label=label,
    )


def extract_records(payload: Any) -> list[dict[str, Any]]:
    return [dict(record) for record in extract_patch_entries(payload)]


def selected_patch_paths(group_dir: Path, subdir: str, tag: str) -> list[Path]:
    patch_dir = group_dir / subdir
    if not patch_dir.is_dir():
        return []
    return select_latest_patch_files(sorted(patch_dir.glob("*.json")), tag)


def build_stage_maps(
    group_dir: Path,
    sources: Iterable[SourceRecordIdentity],
) -> dict[str, IdentityCandidateIndex]:
    source_records = tuple(sources)
    return {
        stage: build_stage_map(
            group_dir,
            source_records,
            stage=stage,
            subdir=subdir,
            tag=tag,
        )
        for stage, subdir, tag in STAGE_SPECS
    }


def build_stage_map(
    group_dir: Path,
    sources: Iterable[SourceRecordIdentity],
    *,
    stage: str,
    subdir: str,
    tag: str,
) -> IdentityCandidateIndex:
    """Build one patch-layer index for a run-local logical projection."""

    return build_layered_patch_index_from_paths(
        selected_patch_paths(group_dir, subdir, tag),
        patch_tag=tag,
        sources=tuple(sources),
        label=f"{stage} patch record",
    )


def build_question_issue_index(
    paths: Iterable[Path],
    sources: Iterable[SourceRecordIdentity],
) -> IdentityCandidateIndex:
    """Resolve correction entries with the same source-binding contract."""
    return build_question_issue_correction_index(paths, sources)


def find_patch_entry(
    mapping: IdentityCandidateIndex | Mapping[
        str, PatchEntry | Iterable[PatchEntry]
    ],
    aliases: Iterable[str],
    source_binding: SourceIdentityBinding | None = None,
) -> PatchEntry | None:
    if isinstance(mapping, IdentityCandidateIndex):
        if source_binding is None:
            raise IdentityResolutionError("patch source bindingがありません。")
        errors = mapping.errors_by_binding.get(source_binding, ())
        if errors:
            raise IdentityResolutionError(" ".join(errors))
        candidates = mapping.by_binding.get(source_binding, ())
        if not candidates:
            return None
        effective_entry: dict[str, Any] = {}
        for candidate in candidates:
            effective_entry.update(
                {
                    field: copy.deepcopy(value)
                    for field, value in candidate.entry.items()
                    if value is not None
                }
            )
        final_candidate = candidates[len(candidates) - 1]
        return PatchEntry(
            path=final_candidate.path,
            entry=effective_entry,
            source_stem=final_candidate.source_stem,
        )

    alias_set = set(aliases)
    matches = {
        (str(candidate.path), id(candidate.entry)): candidate
        for alias in alias_set
        if alias in mapping
        for candidate in (
            (mapping[alias],)
            if isinstance(mapping[alias], PatchEntry)
            else mapping[alias]
        )
    }
    if len(matches) == 1:
        return next(iter(matches.values()))
    if matches:
        raise IdentityResolutionError("patch recordを一意に選べません。")
    return None


def find_patch_entries(
    mapping: IdentityCandidateIndex | Mapping[
        str, PatchEntry | Iterable[PatchEntry]
    ],
    aliases: Iterable[str],
    source_binding: SourceIdentityBinding | None = None,
) -> tuple[PatchEntry, ...]:
    if isinstance(mapping, IdentityCandidateIndex):
        if source_binding is None:
            raise IdentityResolutionError("patch source bindingがありません。")
        errors = mapping.errors_by_binding.get(source_binding, ())
        if errors:
            raise IdentityResolutionError(" ".join(errors))
        return tuple(mapping.by_binding.get(source_binding, ()))
    patch = find_patch_entry(mapping, aliases, source_binding)
    return (patch,) if patch is not None else ()


def project_record(
    base_record: Mapping[str, Any],
    aliases: set[str],
    stage_maps: Mapping[
        str,
        IdentityCandidateIndex
        | Mapping[str, PatchEntry | Iterable[PatchEntry]],
    ],
    question_issue_patches: IdentityCandidateIndex | Iterable[Path],
    *,
    source_binding: SourceIdentityBinding | None = None,
    initial_errors: Iterable[str] = (),
) -> ProjectionResult:
    errors = list(initial_errors)
    resolved: dict[str, tuple[PatchEntry, ...]] = {}
    for stage, _, _ in STAGE_SPECS:
        try:
            resolved[stage] = find_patch_entries(
                stage_maps.get(stage, {}),
                aliases,
                source_binding,
            )
        except IdentityResolutionError as exc:
            errors.append(f"{stage}: {exc}")
            resolved[stage] = ()

    issue_candidates: tuple[QuestionIssueCorrectionEntry, ...] = ()
    if isinstance(question_issue_patches, IdentityCandidateIndex):
        if source_binding is None:
            errors.append("question issue correction: source bindingがありません。")
        else:
            issue_resolution_errors = (
                question_issue_patches.errors_by_binding.get(source_binding, ())
            )
            errors.extend(
                f"question issue correction: {error}"
                for error in issue_resolution_errors
            )
            if not issue_resolution_errors:
                issue_candidates = tuple(
                    question_issue_patches.by_binding.get(source_binding, ())
                )

    try:
        projection = project_merge_record(
            base_record,
            question_type=resolved.get("questionType", ()),
            intent_fallback=resolved.get("questionIntent", ()),
            strict_correct=resolved.get("correctChoice", ()),
            law_context=resolved.get("lawContext", ()),
            explanation=resolved.get("explanation", ()),
            question_set=resolved.get("questionSet", ()),
            question_issues=issue_candidates,
        )
        record = projection.merged2
        applied = [str(path) for path in projection.applied_paths]
        errors.extend(projection.errors)
    except (RuntimeError, ValueError) as exc:
        record = copy.deepcopy(dict(base_record))
        applied = []
        errors.append(str(exc))

    if not isinstance(question_issue_patches, IdentityCandidateIndex):
        # Compatibility for direct callers that do not own a source inventory.
        wrapper = {"question_bodies": [record]}
        for path in sorted(question_issue_patches):
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
    if normalized in {"正しい", "間違い"}:
        return text.startswith(f"{normalized}。")
    return True


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
