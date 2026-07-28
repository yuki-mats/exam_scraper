#!/usr/bin/env python3
"""
Validate explanationText patch coverage and format against source questions.
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
from scripts.check.patch_entry_matching import (
    bind_source_records,
    match_patch_entries,
)
from scripts.common.repaso_firestore_schema import _is_law_revision_facts
from scripts.merge.patch_views import (
    apply_correct_choice,
    apply_question_intent,
    apply_question_type,
)
from scripts.common.suggested_question_contract import (
    public_choice_indexes,
    validation_errors as suggested_question_validation_errors,
)
from scripts.common.explanation_contract import (
    explanation_shape_errors,
    uses_question_level_explanation,
)
from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
    has_non_empty_law_references,
    validate_law_evidence_utilization,
)
from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)


REQUIRED_FIELDS = [
    "explanationText",
    "suggestedQuestionDetailsByChoice",
    "original_question_id",
    "question_url",
]

ALLOWED_LAW_REFERENCE_ROLES = {"current_basis", "exam_time_basis"}
ALLOWED_LAW_REFERENCE_SCOPES = {"question", "choice"}
ALLOWED_LAW_REFERENCE_VERIFICATION_STATUS = {"verified", "candidate", "unverified"}
ALLOWED_LAW_REFERENCE_COMPARISON_STATUS = {
    "same_as_current",
    "differs_from_current",
    "not_checked",
}
LAW_REFERENCE_PLACEHOLDERS = {"", "不明", "未確認", "TODO", "TBD", "N/A", "null", "None"}
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


def get_question_identity(question: Dict[str, Any]) -> Any:
    return review_question_id(question)


def validate_suggested_question_details(
    *,
    suggested_question_details_by_choice: Any,
    question_type: Any,
    question_intent: Any,
    correct_choices: Any,
    choice_count: int,
    index: int,
    errors: List[str],
) -> None:
    errors.extend(
        f"index {index}: {issue}"
        for issue in suggested_question_validation_errors(
            suggested_question_details_by_choice,
            choice_count=choice_count,
            allowed_choice_indexes=public_choice_indexes(
                question_type,
                correct_choices,
                choice_count,
                question_intent,
            ),
        )
    )


def validate_law_references_shape(
    *,
    law_references: Any,
    choice_count: int,
    index: int,
    errors: List[str],
) -> None:
    if not isinstance(law_references, list):
        errors.append(f"index {index}: lawReferences must be a list when present")
        return
    if len(law_references) != choice_count:
        errors.append(
            "index {}: lawReferences length mismatch (source={} patch={})".format(
                index,
                choice_count,
                len(law_references),
            )
        )
        return

    for choice_index, choice_refs in enumerate(law_references):
        if not isinstance(choice_refs, list):
            errors.append(
                f"index {index}: lawReferences[{choice_index}] must be list[object]"
            )
            continue
        for ref_index, reference in enumerate(choice_refs):
            if not isinstance(reference, dict):
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}] must be object"
                )
                continue

            role = reference.get("role")
            if role not in ALLOWED_LAW_REFERENCE_ROLES:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].role is invalid"
                )

            scope = reference.get("scope")
            if scope not in ALLOWED_LAW_REFERENCE_SCOPES:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].scope is invalid"
                )
            elif scope == "choice" and reference.get("choiceIndex") != choice_index:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].choiceIndex must equal outer index"
                )

            verification_status = reference.get("verificationStatus")
            if verification_status not in ALLOWED_LAW_REFERENCE_VERIFICATION_STATUS:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].verificationStatus is invalid"
                )

            comparison_status = reference.get("comparisonStatus")
            if comparison_status is not None and comparison_status not in ALLOWED_LAW_REFERENCE_COMPARISON_STATUS:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].comparisonStatus is invalid"
                )

            for required_key in ("lawTitle", "referenceDate"):
                value = reference.get(required_key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"index {index}: lawReferences[{choice_index}][{ref_index}].{required_key} must be non-empty string"
                    )

            for optional_key in (
                "lawId",
                "lawRevisionId",
                "article",
                "paragraph",
                "item",
                "lawAlias",
                "reason",
            ):
                value = reference.get(optional_key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    errors.append(
                        f"index {index}: lawReferences[{choice_index}][{ref_index}].{optional_key} must be non-empty string when present"
                    )

            if verification_status == "verified":
                for verified_key in ("lawId", "article"):
                    value = reference.get(verified_key)
                    if not isinstance(value, str) or value.strip() in LAW_REFERENCE_PLACEHOLDERS:
                        errors.append(
                            f"index {index}: lawReferences[{choice_index}][{ref_index}].{verified_key} is required for verified lawReferences"
            )


def validate_law_revision_facts_shape(
    *,
    law_revision_facts: Any,
    choice_count: int,
    index: int,
    errors: List[str],
) -> None:
    if isinstance(law_revision_facts, dict):
        normalized_facts = dict(law_revision_facts)
        for snapshot_key in ("examTime", "current"):
            snapshot = law_revision_facts.get(snapshot_key)
            if not isinstance(snapshot, dict):
                continue
            verdicts = snapshot.get("correctChoiceText")
            if not isinstance(verdicts, list):
                continue
            if (
                (choice_count and len(verdicts) != choice_count)
                or not verdicts
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in verdicts
                )
            ):
                errors.append(
                    f"index {index}: lawRevisionFacts.{snapshot_key}.correctChoiceText "
                    "must match the source choice count"
                )
                return
            normalized_snapshot = dict(snapshot)
            normalized_snapshot["correctChoiceText"] = verdicts[0]
            normalized_facts[snapshot_key] = normalized_snapshot
        if not _is_law_revision_facts(normalized_facts):
            errors.append(f"index {index}: lawRevisionFacts must be a valid object")
        return
    if isinstance(law_revision_facts, list):
        if choice_count and len(law_revision_facts) != choice_count:
            errors.append(
                "index {}: lawRevisionFacts length mismatch (source={} patch={})".format(
                    index, choice_count, len(law_revision_facts)
                )
            )
        for choice_index, facts in enumerate(law_revision_facts):
            if not isinstance(facts, dict):
                errors.append(
                    f"index {index}: lawRevisionFacts[{choice_index}] must be object"
                )
            elif not _is_law_revision_facts(facts):
                errors.append(
                    f"index {index}: lawRevisionFacts[{choice_index}] must be a valid object"
                )
        return
    errors.append(f"index {index}: lawRevisionFacts must be object or list[object]")


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
    *,
    require_law_grounded_flag: bool = False,
    require_is_law_related: bool = False,
    require_law_revision_facts: bool = False,
    require_law_evidence_utilization: bool = False,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    matches, identity_errors, identity_warnings = match_patch_entries(
        source_questions,
        patch_entries,
    )
    errors.extend(identity_errors)
    warnings.extend(identity_warnings)

    for match in matches:
        idx = match.patch_index + 1
        src = match.source
        patch = match.patch
        missing_fields = [k for k in REQUIRED_FIELDS if k not in patch]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

        if patch.get("question_url") != src.get("question_url"):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, src.get("question_url"), patch.get("question_url")
                )
            )

        explanations = patch.get("explanationText")
        choices = src.get("choiceTextList") or []
        if not isinstance(explanations, list):
            errors.append(f"index {idx}: explanationText must be a list")
        else:
            source_question_type = src.get("questionType")
            if (
                isinstance(choices, list)
                and len(choices) == 0
                and source_question_type in {"fill_in_blank", "free_text"}
            ):
                if not explanations or any(
                    not isinstance(explanation, str) or not explanation.strip()
                    for explanation in explanations
                ):
                    errors.append(
                        f"index {idx}: fill_in_blank explanationText must be non-empty list[str]"
                    )
            elif isinstance(choices, list):
                errors.extend(
                    f"index {idx}: {error}"
                    for error in explanation_shape_errors(
                        explanations,
                        question_type=source_question_type,
                        choice_count=len(choices),
                    )
                )
            require_verdict_prefix = not uses_question_level_explanation(
                source_question_type
            ) and not (
                isinstance(choices, list)
                and not choices
                and source_question_type in {"fill_in_blank", "free_text"}
            )
            for issue in explanation_style_issues(
                explanations,
                src.get("correctChoiceText"),
                choice_texts=choices,
                require_verdict_prefix=require_verdict_prefix,
                question_type=source_question_type,
            ):
                errors.append(f"index {idx}: {issue}")

        validate_suggested_question_details(
            suggested_question_details_by_choice=patch.get(
                "suggestedQuestionDetailsByChoice"
            ),
            question_type=source_question_type,
            question_intent=src.get("questionIntent"),
            correct_choices=src.get("correctChoiceText"),
            choice_count=len(choices) if isinstance(choices, list) else 0,
            index=idx,
            errors=errors,
        )

        is_law_related: bool | None = None
        if require_is_law_related and "isLawRelated" not in patch:
            errors.append(f"index {idx}: missing isLawRelated")
        elif "isLawRelated" in patch:
            value = patch.get("isLawRelated")
            if not isinstance(value, bool):
                errors.append(f"index {idx}: isLawRelated must be bool when present")
            else:
                is_law_related = value

        has_law_references = has_non_empty_law_references(patch.get("lawReferences"))
        if "lawReferences" in patch:
            validate_law_references_shape(
                law_references=patch.get("lawReferences"),
                choice_count=len(choices) if isinstance(choices, list) else 0,
                index=idx,
                errors=errors,
            )
        # Non-law questions may retain the internal
        # not_law_related/secondary_verified audit memo. Its public shape is
        # normalized by conversion and validated on merged/Firestore output.
        if "lawRevisionFacts" in patch and is_law_related is not False:
            validate_law_revision_facts_shape(
                law_revision_facts=patch.get("lawRevisionFacts"),
                choice_count=len(choices) if isinstance(choices, list) else 0,
                index=idx,
                errors=errors,
            )

        if require_law_grounded_flag and "lawGroundedExplanationNotNeeded" not in patch:
            errors.append(
                f"index {idx}: missing lawGroundedExplanationNotNeeded"
            )
        elif "lawGroundedExplanationNotNeeded" in patch:
            flag = patch.get("lawGroundedExplanationNotNeeded")
            if not isinstance(flag, bool):
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded must be bool when present"
                )
            elif flag and has_law_references:
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded cannot be true when lawReferences is non-empty"
                )
            elif is_law_related is not None and flag == is_law_related:
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded must be the inverse of isLawRelated"
                )

        if is_law_related is False and has_law_references:
            errors.append(
                f"index {idx}: isLawRelated cannot be false when lawReferences is non-empty"
            )
        if (
            require_law_revision_facts
            and is_law_related is True
            and "lawRevisionFacts" not in patch
        ):
            errors.append(
                f"index {idx}: missing lawRevisionFacts for law-related question"
            )
        elif require_law_revision_facts and is_law_related is True:
            # Stage 03 does not own correctChoiceText. Legacy explanation
            # patches may retain a copied value, so validate against the
            # effective predecessor projection.
            effective_correctness = src.get("correctChoiceText")
            errors.extend(
                f"index {idx}: {issue['detail']}"
                for issue in law_revision_current_verdict_issues(
                    correct_choice_text=effective_correctness,
                    law_revision_facts=patch.get("lawRevisionFacts"),
                )
            )
        if require_law_evidence_utilization:
            validate_law_evidence_utilization(
                patch=patch,
                index=idx,
                has_law_references=has_law_references,
                errors=errors,
            )

    return errors, warnings


def check_pair(
    source_path: Path,
    patch_path: Path,
    *,
    question_type_patch_path: Path | None = None,
    question_intent_patch_path: Path | None = None,
    correct_choice_patch_path: Path | None = None,
    require_law_grounded_flag: bool = False,
    require_is_law_related: bool = False,
    require_law_revision_facts: bool = False,
    require_law_evidence_utilization: bool = False,
) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_data = load_json(source_path)
    if not isinstance(source_data, dict):
        print(f"[ERROR] source JSON must be an object: {source_path}")
        return 2

    def load_predecessor(path: Path | None, label: str) -> List[Dict[str, Any]]:
        if path is None:
            return []
        if not path.exists():
            raise FileNotFoundError(f"{label} patch not found: {path}")
        return get_patch_entries(load_json(path))

    try:
        question_type_entries = load_predecessor(
            question_type_patch_path,
            "questionType",
        )
        question_intent_entries = load_predecessor(
            question_intent_patch_path,
            "questionIntent",
        )
        correct_choice_entries = load_predecessor(
            correct_choice_patch_path,
            "correctChoiceText",
        )
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2

    bound_sources = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )

    def predecessor_map(
        entries: List[Dict[str, Any]],
        label: str,
    ) -> Dict[str, Dict[str, Any]] | None:
        matches, identity_errors, _ = match_patch_entries(
            bound_sources,
            entries,
        )
        if identity_errors:
            for error in identity_errors:
                print(f"[ERROR] {label} patch: {error}")
            return None
        return {
            str(review_question_id(match.source)): match.patch
            for match in matches
            if review_question_id(match.source)
        }

    if question_type_entries:
        question_type_map = predecessor_map(
            question_type_entries,
            "questionType",
        )
        if question_type_map is None:
            return 1
        apply_question_type(
            source_data,
            question_type_map,
        )
    if question_intent_entries:
        question_intent_map = predecessor_map(
            question_intent_entries,
            "questionIntent",
        )
        if question_intent_map is None:
            return 1
        apply_question_intent(
            source_data,
            {
                question_id: entry.get("questionIntent")
                for question_id, entry in question_intent_map.items()
                if entry.get("questionIntent") is not None
            },
        )
    if correct_choice_entries:
        correct_choice_map = predecessor_map(
            correct_choice_entries,
            "correctChoiceText",
        )
        if correct_choice_map is None:
            return 1
        apply_correct_choice(
            source_data,
            {
                question_id: entry.get("correctChoiceText")
                for question_id, entry in correct_choice_map.items()
                if entry.get("correctChoiceText") is not None
            },
        )
    patch_data = load_json(patch_path)

    source_questions = bind_source_records(
        get_source_questions(source_data),
        source_path,
    )
    patch_entries = get_patch_entries(patch_data)

    errors, warnings = compare_entries(
        source_questions,
        patch_entries,
        require_law_grounded_flag=require_law_grounded_flag,
        require_is_law_related=require_is_law_related,
        require_law_revision_facts=require_law_revision_facts,
        require_law_evidence_utilization=require_law_evidence_utilization,
    )
    for warn in warnings:
        print(f"[WARN] {warn}")
    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1

    print("[OK] coverage check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate explanationText patch coverage and format."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_explanationText_added_YYYYMMDD_HHMM.json (旧形式 *_explanationText_added.json も可)",
    )
    parser.add_argument(
        "--question-type-patch",
        help="Path to the effective 01 questionType patch for this source file.",
    )
    parser.add_argument(
        "--question-intent-patch",
        help="Path to the effective 02 questionIntent patch for this source file.",
    )
    parser.add_argument(
        "--correct-choice-patch",
        help="Path to the effective 02a correctChoiceText patch for this source file.",
    )
    parser.add_argument(
        "--require-law-grounded-flag",
        action="store_true",
        help="Require lawGroundedExplanationNotNeeded on every patch entry.",
    )
    parser.add_argument(
        "--require-is-law-related",
        action="store_true",
        help="Require isLawRelated on every patch entry.",
    )
    parser.add_argument(
        "--require-law-revision-facts",
        action="store_true",
        help="Require lawRevisionFacts when isLawRelated=true.",
    )
    parser.add_argument(
        "--require-law-evidence-utilization",
        action="store_true",
        help="Require law-related explanationText/suggestedQuestions/suggestedQuestionDetails to reflect existing law evidence.",
    )
    args = parser.parse_args()
    return check_pair(
        Path(args.source),
        Path(args.patch),
        question_type_patch_path=(
            Path(args.question_type_patch) if args.question_type_patch else None
        ),
        question_intent_patch_path=(
            Path(args.question_intent_patch) if args.question_intent_patch else None
        ),
        correct_choice_patch_path=(
            Path(args.correct_choice_patch) if args.correct_choice_patch else None
        ),
        require_law_grounded_flag=args.require_law_grounded_flag,
        require_is_law_related=args.require_is_law_related,
        require_law_revision_facts=args.require_law_revision_facts,
        require_law_evidence_utilization=args.require_law_evidence_utilization,
    )


if __name__ == "__main__":
    raise SystemExit(main())
