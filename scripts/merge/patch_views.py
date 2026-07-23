from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, TypeVar

from scripts.common.question_identity import (
    IdentityCandidateIndex,
    SourceIdentityBinding,
    SourceRecordIdentity,
    resolve_identity_candidates,
    review_question_id,
    source_identity_aliases,
    workflow_identity_aliases,
)
from scripts.common.aggregate_answer_decomposition import (
    derived_source_unique_keys,
    extract_source_statements,
    is_approved_target,
    normalize_decomposition,
)
from scripts.common.independent_question_images import (
    INDEPENDENT_IMAGE_REQUIRED_FIELD,
    validate_originalized_image_entry,
)
from scripts.merge.merge_utils import (
    build_manual_output_path,
    maybe_split_for_manual_output,
    source_stem_from_patch_filename,
)


EXPLANATION_FIELDS = [
    "explanationText",
    "suggestedQuestionDetailsByChoice",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "explanation_common_prefix",
    "explanation_common_prefix_inferred_correct_choice",
    "explanation_common_summary",
    "explanation_choice_snippets",
    "explanation_choice_correctness",
]
ORIGINALIZED_FIELDS = [
    "questionBodyText",
    "choiceTextList",
    "correctChoiceText",
    "questionIntent",
    "answer_result_text",
    "questionImageStorageUrls",
    "originalQuestionChoiceImageUrls",
]
ORIGINALIZED_REQUIRED_FIELDS = (
    "questionBodyText",
    "choiceTextList",
    "correctChoiceText",
    "questionIntent",
    "answer_result_text",
)
INDEPENDENT_QUESTION_EXAM_SOURCE = "独自問題"
SOURCE_EXPLANATION_FIELDS = tuple(
    dict.fromkeys(
        (
            *EXPLANATION_FIELDS,
            "knowledgeText",
            "explanationImageStorageUrls",
        )
    )
)
LAW_CONTEXT_FIELDS = [
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "lawReferences",
    "lawContextForExplanation",
]
QUESTION_SOURCE_PRESERVATION_FIELDS = [
    "originalQuestionId",
    "original_question_id",
    "uploadOriginalQuestionId",
    "firestoreQuestionIds",
    "firestoreSourceQuestions",
    "sourceConflictReviewDecision",
    "sourceContentConflictPolicy",
]
AGGREGATE_DERIVATIVE_STALE_FIELDS = (
    "correctChoiceText",
    "explanationText",
    "suggestedQuestionDetailsByChoice",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "explanation_common_prefix",
    "explanation_common_prefix_inferred_correct_choice",
    "explanation_common_summary",
    "explanation_choice_snippets",
    "explanation_choice_correctness",
    "answer_result_inferred_correct_choice_numbers",
    "choiceQuestionSetIds",
    "questionSetIds",
    "originalQuestionChoiceImageUrls",
)
AGGREGATE_DERIVATIVE_OLD_ID_FIELDS = (
    "firestoreQuestionIds",
    "firestoreSourceQuestions",
)
NEGATIVE_PROMPT_PHRASES = (
    "最も不適当なもの",
    "最も不適当",
    "不適当なもの",
    "不適当なの",
    "不適切なもの",
    "不適切なの",
    "適さない",
    "適切でない",
    "適切でないもの",
    "適切でないの",
    "適当でない",
    "適当でないもの",
    "適当でないの",
    "誤っている",
    "誤っているもの",
    "誤っているの",
    "誤っている記述",
    "誤っている組合せ",
    "誤っている組み合わせ",
    "誤っている配列",
    "誤ったもの",
    "誤った組合せ",
    "誤った組み合わせ",
    "誤った配列",
    "誤りのあるもの",
    "誤りのある記述",
    "誤りはどれか",
    "正しくないもの",
    "正しくないの",
    "してはならない",
    "してはいけない",
    "行ってはならない",
    "行ってはいけない",
    "考えられない",
    "考えにくい",
    "認められない",
    "診られない",
    "共通しない",
    "属さない",
    "栄養されない",
    "通らない",
    "存在しない",
    "増えない",
    "原因とならない",
    "原因でない",
    "指標とならない",
    "特徴としない",
    "合併しない",
    "関与しない",
    "受けていない",
    "によらない",
    "行われない",
    "みられない",
    "認めにくい",
    "できない",
    "きたさない",
    "起こらない",
    "起こしにくい",
    "起こりにくい",
    "減弱しない",
    "関係ない",
    "関係のない",
    "関連の低い",
    "使用しない",
    "含まれない",
    "含まれないもの",
    "含まれないの",
    "でないのは",
    "有用でない",
    "ない経穴",
    "診断できない",
)


@dataclass(frozen=True)
class PatchArtifactEntry:
    """One patch record together with its artifact scope."""

    path: Path
    entry: dict[str, Any]
    source_stem: str = ""


PatchCandidate = TypeVar("PatchCandidate")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_patch_entries(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    return []


def normalize_question_ids(data: dict) -> None:
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        return
    for question in questions:
        if not isinstance(question, dict):
            continue
        if not question.get("original_question_id") and question.get("public_question_id"):
            question["original_question_id"] = question["public_question_id"]


def patch_target_id(question: Mapping[str, Any]) -> str:
    return review_question_id(question)


def _patch_record_aliases(record: Mapping[str, Any]) -> set[str]:
    return source_identity_aliases(record) | workflow_identity_aliases(record)


def _canonical_patch_record(record: Mapping[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_layered_patch_candidate_index(
    candidates: Iterable[PatchCandidate],
    *,
    sources: Iterable[SourceRecordIdentity],
    record_of: Callable[[PatchCandidate], Mapping[str, Any]],
    source_stem_of: Callable[[PatchCandidate], str],
    path_of: Callable[[PatchCandidate], Path],
    label: str,
) -> IdentityCandidateIndex:
    """Resolve patch records and validate their explicit overlay layers.

    A patch whose filename stem belongs to its resolved source record is
    applied first.  Records stored outside that source artifact are aggregate
    overlays and are applied second.  Within one layer there is deliberately
    no filename-based precedence: identical duplicates collapse to one record
    and competing records fail closed.
    """

    source_records = tuple(sources)
    values = tuple(candidates)
    source_by_binding = {
        source.binding: source
        for source in source_records
    }
    def candidate_layer(
        candidate: PatchCandidate,
        binding: SourceIdentityBinding,
    ) -> str:
        expected_stem = source_by_binding[binding].source_stem
        return (
            "per-source"
            if source_stem_of(candidate)
            in {expected_stem, f"{expected_stem}_merged"}
            else "aggregate"
        )
    resolved = resolve_identity_candidates(
        values,
        sources=source_records,
        record_of=record_of,
        aliases_of=_patch_record_aliases,
        source_stem_of=source_stem_of,
        label=label,
    )

    errors: dict[SourceIdentityBinding, list[str]] = {
        binding: list(messages)
        for binding, messages in resolved.errors_by_binding.items()
    }
    validated: dict[SourceIdentityBinding, tuple[PatchCandidate, ...]] = {}
    for binding, binding_candidates in resolved.by_binding.items():
        layered: list[PatchCandidate] = []
        binding_has_conflict = False
        for layer in ("per-source", "aggregate"):
            layer_candidates = [
                candidate
                for candidate in binding_candidates
                if candidate_layer(candidate, binding) == layer
            ]
            if not layer_candidates:
                continue

            by_artifact: dict[str, list[PatchCandidate]] = {}
            for candidate in layer_candidates:
                by_artifact.setdefault(str(path_of(candidate).resolve()), []).append(
                    candidate
                )

            deduplicated: list[PatchCandidate] = []
            for artifact_path, artifact_candidates in sorted(by_artifact.items()):
                records = {
                    _canonical_patch_record(record_of(candidate))
                    for candidate in artifact_candidates
                }
                if len(records) > 1:
                    errors.setdefault(binding, []).append(
                        f"{label}の同一artifact内で同じsource bindingが競合しています: "
                        f"{artifact_path}"
                    )
                    binding_has_conflict = True
                    continue
                deduplicated.append(artifact_candidates[0])

            distinct_records = {
                _canonical_patch_record(record_of(candidate))
                for candidate in deduplicated
            }
            if len(distinct_records) > 1:
                paths = ", ".join(
                    sorted(str(path_of(candidate)) for candidate in deduplicated)
                )
                errors.setdefault(binding, []).append(
                    f"{label}の{layer} layerで同じsource bindingが競合しています: "
                    f"{paths}"
                )
                binding_has_conflict = True
                continue
            if deduplicated:
                layered.append(
                    min(deduplicated, key=lambda candidate: str(path_of(candidate)))
                )

        if not binding_has_conflict:
            validated[binding] = tuple(layered)

    return IdentityCandidateIndex(
        by_binding=validated,
        errors_by_binding={
            binding: tuple(dict.fromkeys(messages))
            for binding, messages in errors.items()
        },
        unmatched_count=resolved.unmatched_count,
        unmatched_candidates=resolved.unmatched_candidates,
    )


def build_layered_patch_index_from_paths(
    patch_paths: Iterable[Path],
    *,
    patch_tag: str,
    sources: Iterable[SourceRecordIdentity],
    label: str,
) -> IdentityCandidateIndex:
    candidates: list[PatchArtifactEntry] = []
    for patch_path in patch_paths:
        source_stem = source_stem_from_patch_filename(patch_path.name, patch_tag)
        if source_stem is None:
            continue
        for entry in extract_patch_entries(load_json(patch_path)):
            candidates.append(
                PatchArtifactEntry(
                    path=patch_path,
                    entry=dict(entry),
                    source_stem=source_stem,
                )
            )
    return build_layered_patch_candidate_index(
        candidates,
        sources=sources,
        record_of=lambda candidate: candidate.entry,
        source_stem_of=lambda candidate: candidate.source_stem,
        path_of=lambda candidate: candidate.path,
        label=label,
    )


def ensure_identity_candidate_index_valid(
    index: IdentityCandidateIndex,
    *,
    label: str,
) -> None:
    """Reject unresolved patch artifacts before publication output is mutated."""

    messages = list(
        dict.fromkeys(
            message
            for binding_messages in index.errors_by_binding.values()
            for message in binding_messages
        )
    )
    if index.unmatched_count:
        paths = sorted(
            {
                str(getattr(candidate, "path", "(artifact path unavailable)"))
                for candidate in index.unmatched_candidates
            }
        )
        messages.append(
            f"source recordへ対応できない{label}が{index.unmatched_count}件あります: "
            + ", ".join(paths)
        )
    if messages:
        raise RuntimeError(" ".join(messages))


def apply_question_type(
    data: dict,
    qtype_map: Mapping[str, Any],
    *,
    validate_aggregate_target: bool = True,
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        patch_entry = qtype_map.get(str(question_id))
        if isinstance(patch_entry, dict):
            changed = False
            approved_aggregate_target = False
            decomposition = patch_entry.get("aggregateAnswerDecomposition")
            if decomposition is not None:
                source_text = question.get("questionBodyText")
                if not isinstance(source_text, str):
                    raise ValueError("questionBodyText must be string")
                normalized_decomposition = normalize_decomposition(
                    decomposition,
                    source_text,
                )
                approved_aggregate_target = is_approved_target(
                    normalized_decomposition,
                    source_text,
                )
                if approved_aggregate_target:
                    extracted_choices = extract_source_statements(
                        source_text,
                        normalized_decomposition,
                    )
                    expected_keys = derived_source_unique_keys(
                        question,
                        normalized_decomposition,
                    )
                    if (
                        validate_aggregate_target
                        and patch_entry.get("choiceTextList") != extracted_choices
                    ):
                        raise ValueError(
                            "aggregate answer choiceTextList must be exact source spans"
                        )
                    if (
                        validate_aggregate_target
                        and patch_entry.get("sourceUniqueKeys") != expected_keys
                    ):
                        raise ValueError(
                            "aggregate answer sourceUniqueKeys do not match source spans"
                        )
                    if (
                        validate_aggregate_target
                        and patch_entry.get("questionType") != "true_false"
                    ):
                        raise ValueError(
                            "approved aggregate answer target must use true_false"
                        )
                    if question.get("choiceTextList") != extracted_choices:
                        question["choiceTextList"] = extracted_choices
                        changed = True
                    if question.get("sourceUniqueKeys") != expected_keys:
                        question["sourceUniqueKeys"] = expected_keys
                        changed = True
                    for field in (
                        *AGGREGATE_DERIVATIVE_STALE_FIELDS,
                        *AGGREGATE_DERIVATIVE_OLD_ID_FIELDS,
                    ):
                        if field in question:
                            question.pop(field, None)
                            changed = True
                if question.get("aggregateAnswerDecomposition") != normalized_decomposition:
                    question["aggregateAnswerDecomposition"] = normalized_decomposition
                    changed = True
            new_type = patch_entry.get("questionType")
            if new_type is not None and question.get("questionType") != new_type:
                question["questionType"] = new_type
                changed = True
            if "isCalculationQuestion" in patch_entry:
                new_calculation_flag = patch_entry.get("isCalculationQuestion")
                if not isinstance(new_calculation_flag, bool):
                    raise ValueError("isCalculationQuestion must be boolean")
                if question.get("isCalculationQuestion") != new_calculation_flag:
                    question["isCalculationQuestion"] = new_calculation_flag
                    changed = True
            new_body = patch_entry.get("questionBodyText")
            if new_body is not None and question.get("questionBodyText") != new_body:
                question["questionBodyText"] = new_body
                changed = True
            new_choices = patch_entry.get("choiceTextList")
            if (
                not approved_aggregate_target
                and new_choices is not None
                and question.get("choiceTextList") != new_choices
            ):
                question["choiceTextList"] = new_choices
                changed = True
            new_source_unique_keys = patch_entry.get("sourceUniqueKeys")
            if (
                not approved_aggregate_target
                and
                isinstance(new_source_unique_keys, list)
                and question.get("sourceUniqueKeys") != new_source_unique_keys
            ):
                question["sourceUniqueKeys"] = new_source_unique_keys
                changed = True
            for field in QUESTION_SOURCE_PRESERVATION_FIELDS:
                if approved_aggregate_target and field in AGGREGATE_DERIVATIVE_OLD_ID_FIELDS:
                    continue
                if field in patch_entry and patch_entry[field] is not None and question.get(field) != patch_entry[field]:
                    question[field] = patch_entry[field]
                    changed = True
            if changed:
                updated += 1
            continue
        new_type = patch_entry
        if new_type is not None:
            question["questionType"] = new_type
            updated += 1
            continue

        # 追加: choiceTextListが全て空欄ならgroup_choiceにする
        choice_list = question.get("choiceTextList")
        if isinstance(choice_list, list) and all((c is None or str(c).strip() == "") for c in choice_list):
            question["questionType"] = "group_choice"
            updated += 1
            continue
    return updated


def _normalized_originalized_text(value: Any) -> str:
    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(value or "")),
    )


def _normalized_explanation_fragments(value: Any) -> set[str]:
    if isinstance(value, str):
        normalized = _normalized_originalized_text(value)
        return {normalized} if normalized else set()
    if isinstance(value, Mapping):
        fragments: set[str] = set()
        for nested in value.values():
            fragments.update(_normalized_explanation_fragments(nested))
        return fragments
    if isinstance(value, (list, tuple)):
        fragments = set()
        for nested in value:
            fragments.update(_normalized_explanation_fragments(nested))
        return fragments
    return set()


def ensure_originalized_explanation_is_distinct(
    source: Mapping[str, Any],
    explanation_candidates: Iterable[PatchArtifactEntry],
) -> None:
    """Reject verbatim source explanations without copying them into merged data."""

    source_fragments: set[str] = set()
    for field in (
        "explanationText",
        "explanation_common_prefix",
        "explanation_common_summary",
        "explanation_choice_snippets",
    ):
        source_fragments.update(_normalized_explanation_fragments(source.get(field)))
    if not source_fragments:
        return

    for candidate in explanation_candidates:
        published_fragments = _normalized_explanation_fragments(
            candidate.entry.get("explanationText")
        )
        if source_fragments & published_fragments:
            raise ValueError(
                "03の解説が00_sourceの解説原文と完全一致しています: "
                f"{candidate.path}"
            )


def normalize_correct_choice_label(value: Any) -> str:
    text = str(value or "").strip()
    return {
        "正解": "正しい",
        "不正解": "間違い",
        "誤り": "間違い",
    }.get(text, text)


def validate_originalized_entry(
    source: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> bool:
    missing = [
        field
        for field in ORIGINALIZED_REQUIRED_FIELDS
        if field not in entry or entry.get(field) in (None, "", [])
    ]
    if missing:
        raise ValueError(
            "05_originalizedの必須fieldが不足しています: "
            + ", ".join(missing)
        )

    body = entry.get("questionBodyText")
    choices = entry.get("choiceTextList")
    verdicts = entry.get("correctChoiceText")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("05_originalized.questionBodyTextは空でない文字列が必要です。")
    if not isinstance(choices, list) or not choices or any(
        not isinstance(choice, str) or not choice.strip() for choice in choices
    ):
        raise ValueError("05_originalized.choiceTextListは空でない文字列配列が必要です。")
    if not isinstance(verdicts, list) or len(verdicts) != len(choices):
        raise ValueError(
            "05_originalized.correctChoiceTextはchoiceTextListと同じ件数の配列が必要です。"
        )
    if any(normalize_correct_choice_label(value) not in {"正しい", "間違い"} for value in verdicts):
        raise ValueError(
            "05_originalized.correctChoiceTextは「正しい」「間違い」のみを使います。"
        )
    if str(entry.get("questionIntent") or "").strip() not in {
        "select_correct",
        "select_incorrect",
    }:
        raise ValueError(
            "05_originalized.questionIntentはselect_correctまたはselect_incorrectが必要です。"
        )

    source_body = source.get("questionBodyText") or source.get(
        "originalQuestionBodyText"
    )
    if _normalized_originalized_text(body) == _normalized_originalized_text(source_body):
        raise ValueError(
            "05_originalizedの問題文全体が00_sourceと完全一致しています。"
        )
    return validate_originalized_image_entry(source, entry)


def apply_originalized_fields(
    data: dict,
    originalized_map: Mapping[str, dict],
) -> int:
    """Apply the publication-safe independent-question base before stage 01."""

    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = originalized_map.get(str(question_id))
        if not isinstance(entry, Mapping):
            continue
        image_required = validate_originalized_entry(question, entry)

        for field in ORIGINALIZED_FIELDS:
            if field in entry and entry[field] is not None:
                question[field] = entry[field]
        question["correctChoiceText"] = [
            normalize_correct_choice_label(value)
            for value in question["correctChoiceText"]
        ]
        question["examSource"] = INDEPENDENT_QUESTION_EXAM_SOURCE
        question.pop("examYear", None)
        question["originalQuestionBodyText"] = question["questionBodyText"]
        question.pop("original_question_body_text", None)
        question.pop("originalQuestionChoiceText", None)
        public_id = str(
            question.get("public_question_id")
            or question.get("original_question_id")
            or ""
        ).strip()
        if not public_id:
            raise ValueError(
                "05_originalizedの公開用IDを生成できません。"
            )
        question["sourceUniqueKeys"] = [
            f"{public_id}:choice:{index + 1}"
            for index in range(len(question["choiceTextList"]))
        ]
        question[INDEPENDENT_IMAGE_REQUIRED_FIELD] = image_required

        # 取得元の画像や解説を公開系路へ暗黙に流さない。
        if "questionImageStorageUrls" not in entry:
            question.pop("questionImageStorageUrls", None)
        if "originalQuestionChoiceImageUrls" not in entry:
            question.pop("originalQuestionChoiceImageUrls", None)
        for field in SOURCE_EXPLANATION_FIELDS:
            question.pop(field, None)
        updated += 1
    return updated


def apply_explanation_fields(
    data: dict,
    explanation_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = explanation_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in EXPLANATION_FIELDS:
            if field in entry and entry[field] is not None:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_law_context_fields(
    data: dict,
    law_context_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = law_context_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in LAW_CONTEXT_FIELDS:
            if field in entry and entry[field] is not None:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_question_set(
    data: dict,
    question_set_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        patch_entry = question_set_map.get(str(question_id))
        if patch_entry is None:
            continue
        changed = False
        if isinstance(patch_entry, Mapping):
            if "questionSetId" in patch_entry and patch_entry.get("questionSetId") is not None:
                new_value = patch_entry.get("questionSetId")
                if question.get("questionSetId") != new_value:
                    question["questionSetId"] = new_value
                    changed = True
            for field in ("choiceQuestionSetIds", "questionSetIds"):
                if field not in patch_entry or patch_entry.get(field) is None:
                    continue
                new_value = patch_entry.get(field)
                if question.get(field) != new_value:
                    question[field] = new_value
                    changed = True
        else:
            if question.get("questionSetId") != patch_entry:
                question["questionSetId"] = patch_entry
                changed = True
        if changed:
            updated += 1
    return updated


def apply_correct_choice(
    data: dict,
    correct_choice_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        new_value = correct_choice_map.get(str(question_id))
        if new_value is None:
            continue
        question["correctChoiceText"] = new_value
        updated += 1
    return updated


def apply_answer_result_overrides(
    data: dict,
    override_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = override_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in (
            "answer_result_text",
            "answer_result_inferred_correct_choice_numbers",
            "manualQuestionIntentOverride",
        ):
            if field in entry and entry[field] is not None and question.get(field) != entry[field]:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_question_intent(
    data: dict,
    question_intent_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        new_value = question_intent_map.get(str(question_id))
        if new_value is None:
            continue
        question["questionIntent"] = new_value
        updated += 1
    return updated


FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
ANSWER_RESULT_RE = re.compile(r"正解は\s*([0-9０-９]+(?:\s*,\s*[0-9０-９]+)*)\s*です。")


def parse_answer_numbers(answer_result_text: Any) -> list[int]:
    text = str(answer_result_text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    match = ANSWER_RESULT_RE.search(text)
    if not match:
        return []
    numbers: list[int] = []
    for part in match.group(1).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        n = int(part)
        if n not in numbers:
            numbers.append(n)
    return numbers


def infer_question_intent_from_text(question_body_text: Any) -> str | None:
    text = str(question_body_text or "").strip()
    if not text:
        return None

    positive_select_keywords = (
        "使用しない機材",
    )
    positive_required_keywords = (
        "見落としてはならない",
        "見逃してはならない",
        "しなければならない",
        "行わなければならない",
        "伝えなければならない",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    intent_text = text[-240:]
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if "どれか" not in line and "選べ" not in line:
            continue
        focus_end = max(line.rfind("どれか"), line.rfind("選べ"))
        if focus_end >= 0:
            focus_start = max(0, focus_end - 48)
            focus_text = line[focus_start : focus_end + 3]
        else:
            focus_text = line
        if any(phrase in focus_text for phrase in NEGATIVE_PROMPT_PHRASES):
            intent_text = focus_text
            break
        if not line.startswith(("のは", "は")) and len(line) > 8:
            intent_text = focus_text
            break
        previous = lines[index - 1] if index > 0 else ""
        intent_text = f"{previous}\n{focus_text}".strip() or focus_text
        break

    if any(keyword in intent_text for keyword in positive_required_keywords):
        return "select_correct"
    if any(keyword in intent_text for keyword in positive_select_keywords):
        return "select_correct"
    if any(phrase in intent_text for phrase in NEGATIVE_PROMPT_PHRASES):
        return "select_incorrect"
    return "select_correct"


def normalize_true_false_intent_and_correct_choice(
    data: dict,
) -> tuple[int, int]:
    """
    questionIntent / correctChoiceText を一次情報から整合する。

    correctChoiceText は選択肢そのものの正誤を保持する。
    既に 00_source やレビューで正誤ラベルが埋まっている場合は、
    answer_result_text と questionIntent から再計算して上書きしない。

    - questionIntent は questionBodyText（無ければ originalQuestionBodyText）から推定して上書き（推定できる場合のみ）
    - correctChoiceText は欠損時のみ、位置選択型の answer_numbers から補完する
    """
    normalize_question_ids(data)
    intent_updates = 0
    correct_choice_updates = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        return (0, 0)

    for question in questions:
        if not isinstance(question, dict):
            continue
        question_type = question.get("questionType")
        if question_type == "fill_in_blank":
            continue
        if question_type != "true_false":
            continue

        source_text = str(
            question.get("questionBodyText")
            or question.get("originalQuestionBodyText")
            or ""
        )
        decomposition = question.get("aggregateAnswerDecomposition")
        if decomposition is not None and is_approved_target(
            decomposition,
            source_text,
        ):
            continue

        manual_intent_override = question.get("manualQuestionIntentOverride") is True
        current_intent = question.get("questionIntent")
        if manual_intent_override or current_intent in {"select_correct", "select_incorrect"}:
            inferred_intent = None
        else:
            inferred_intent = infer_question_intent_from_text(
                question.get("questionBodyText") or question.get("originalQuestionBodyText") or ""
            )
            if inferred_intent and question.get("questionIntent") != inferred_intent:
                question["questionIntent"] = inferred_intent
                intent_updates += 1

        intent = question.get("questionIntent")
        if intent not in {"select_correct", "select_incorrect"}:
            continue

        current_correct_choice = question.get("correctChoiceText")
        if (
            isinstance(current_correct_choice, list)
            and current_correct_choice
            and all(
                str(value).strip() in {"正しい", "間違い"}
                for value in current_correct_choice
            )
        ):
            if manual_intent_override:
                question.pop("manualQuestionIntentOverride", None)
            continue

        body_text = str(question.get("questionBodyText") or question.get("originalQuestionBodyText") or "")
        if "いくつ" in body_text:
            if manual_intent_override:
                question.pop("manualQuestionIntentOverride", None)
            continue

        answer_numbers = parse_answer_numbers(question.get("answer_result_text"))
        if not answer_numbers:
            inferred_numbers = question.get("answer_result_inferred_correct_choice_numbers")
            if isinstance(inferred_numbers, list) and inferred_numbers:
                answer_numbers = []
                for v in inferred_numbers:
                    if isinstance(v, int):
                        answer_numbers.append(v)
                    elif str(v).isdigit():
                        answer_numbers.append(int(str(v)))
                # 重複除外
                normalized: list[int] = []
                for n in answer_numbers:
                    if n >= 1 and n not in normalized:
                        normalized.append(n)
                answer_numbers = normalized
        if not answer_numbers:
            continue

        choice_count = None
        choice_list = question.get("choiceTextList")
        if isinstance(choice_list, list) and choice_list:
            choice_count = len(choice_list)
        elif isinstance(question.get("correctChoiceText"), list) and question.get("correctChoiceText"):
            choice_count = len(question.get("correctChoiceText"))
        if not choice_count:
            continue
        if any((n < 1 or n > choice_count) for n in answer_numbers):
            continue

        if intent == "select_incorrect":
            expected = ["正しい"] * choice_count
            for n in answer_numbers:
                expected[n - 1] = "間違い"
        else:
            expected = ["間違い"] * choice_count
            for n in answer_numbers:
                expected[n - 1] = "正しい"

        current = question.get("correctChoiceText")
        if current != expected:
            question["correctChoiceText"] = expected
            correct_choice_updates += 1
        if manual_intent_override:
            question.pop("manualQuestionIntentOverride", None)

    return (intent_updates, correct_choice_updates)


def materialize_view_files(
    source_paths: Iterable[Path],
    output_dir: Path,
    *,
    qtype_map: Mapping[str, Any] | None = None,
    explanation_map: Mapping[str, dict] | None = None,
    question_set_map: Mapping[str, Any] | None = None,
    correct_choice_map: Mapping[str, Any] | None = None,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    for source_path in source_paths:
        data = load_json(source_path)
        if qtype_map:
            apply_question_type(data, qtype_map)
        if explanation_map:
            apply_explanation_fields(data, explanation_map)
        if question_set_map:
            apply_question_set(data, question_set_map)
        if correct_choice_map:
            apply_correct_choice(data, correct_choice_map)

        output_path = output_dir / source_path.name
        valid_data, manual_data = maybe_split_for_manual_output(data, output_path)
        save_json(valid_data, output_path)
        if manual_data:
            manual_path = build_manual_output_path(output_path)
            save_json(manual_data, manual_path)
        written[source_path.name] = output_path

    return written
