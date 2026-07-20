from __future__ import annotations

import re
from typing import Any, Iterable

from tools.question_review_console.projection import normalize_verdict
from scripts.common.explanation_contract import (
    expected_explanation_count,
    uses_question_level_explanation,
)


LAW_AS_SENTENCE_SUBJECT = re.compile(
    r"^(?:正しい|間違い)。\s*"
    r"[^、。]{1,80}(?:法|令|規則|省令|告示)"
    r"第[^、。]{1,80}は[、，]"
)
POINT_IS_WRONG = re.compile(r"(?:点|ところ)が誤り(?:である)?(?:。|$)")
VERDICT_PREFIX = re.compile(r"^(正しい|間違い)。")

LAW_CONTEXT_KEYWORDS = (
    "現行法",
    "出題当時",
    "当時",
    "現在",
    "法",
    "条",
    "項",
    "号",
    "政令",
    "省令",
    "規則",
    "告示",
    "基準",
    "制度",
    "定義",
    "要件",
    "義務",
    "手続",
    "届出",
    "許可",
    "確認",
    "改正",
)
CURRENT_LAW_TERMS = ("現行法", "現在", "現行")
EXAM_TIME_TERMS = (
    "出題当時",
    "当時",
    "試験当時",
    "元の正答",
    "掲載元",
)


def has_non_empty_law_references(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return any(has_non_empty_law_references(entry) for entry in value)
    return False


def _normalize_for_anchor_match(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _iter_nested_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        if value.strip():
            strings.append(value.strip())
    elif isinstance(value, dict):
        for nested in value.values():
            strings.extend(_iter_nested_strings(nested))
    elif isinstance(value, list):
        for nested in value:
            strings.extend(_iter_nested_strings(nested))
    return strings


def _iter_law_reference_objects(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        refs.append(value)
    elif isinstance(value, list):
        for nested in value:
            refs.extend(_iter_law_reference_objects(nested))
    return refs


def _iter_law_revision_fact_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def _law_revision_fact_statuses(value: Any) -> set[str]:
    statuses: set[str] = set()
    for fact in _iter_law_revision_fact_objects(value):
        status = fact.get("auditStatus")
        if isinstance(status, str) and status:
            statuses.add(status)
    return statuses


def _law_related_for_utilization(
    patch: dict[str, Any], has_law_references: bool
) -> bool:
    if patch.get("isLawRelated") is True:
        return True
    if has_law_references:
        return True
    statuses = _law_revision_fact_statuses(patch.get("lawRevisionFacts"))
    return bool(statuses and statuses != {"not_law_related"})


def _law_evidence_anchors(patch: dict[str, Any]) -> list[str]:
    anchors: list[str] = []

    for ref in _iter_law_reference_objects(patch.get("lawReferences")):
        for key in ("lawTitle", "lawAlias"):
            value = ref.get(key)
            if isinstance(value, str) and len(_normalize_for_anchor_match(value)) >= 3:
                anchors.append(value)
        article = ref.get("article")
        if isinstance(article, str) and article.strip():
            article_text = article.strip()
            if "条" in article_text:
                anchors.append(article_text)
            elif article_text.isdigit():
                anchors.append(f"第{article_text}条")
                anchors.append(f"{article_text}条")

    for fact in _iter_law_revision_fact_objects(patch.get("lawRevisionFacts")):
        for key in ("current", "examTime", "evidenceSummary"):
            source = fact.get(key)
            for text in _iter_nested_strings(source):
                if "法" in text and len(_normalize_for_anchor_match(text)) <= 40:
                    anchors.append(text)
            if isinstance(source, dict):
                law_title = source.get("lawTitle")
                if (
                    isinstance(law_title, str)
                    and len(_normalize_for_anchor_match(law_title)) >= 3
                ):
                    anchors.append(law_title)
                article = source.get("article")
                if isinstance(article, str) and article.strip():
                    article_text = article.strip()
                    if "条" in article_text:
                        anchors.append(article_text)
                    elif article_text.isdigit():
                        anchors.append(f"第{article_text}条")
                        anchors.append(f"{article_text}条")

    normalized_seen: set[str] = set()
    unique: list[str] = []
    for anchor in anchors:
        normalized = _normalize_for_anchor_match(anchor)
        if len(normalized) < 2 or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        unique.append(anchor)
    return unique


def _public_text_for_patch(patch: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(_iter_nested_strings(patch.get("explanationText")))
    parts.extend(
        _iter_nested_strings(patch.get("suggestedQuestionDetailsByChoice"))
    )
    return "\n".join(parts)


def _suggested_question_items(patch: dict[str, Any]) -> list[dict[str, Any]]:
    groups = patch.get("suggestedQuestionDetailsByChoice")
    if not isinstance(groups, list):
        return []
    return [
        item
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("items"), list)
        for item in group["items"]
        if isinstance(item, dict)
    ]


def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(term in text for term in terms)


def law_evidence_utilization_issues(
    patch: dict[str, Any],
    *,
    has_law_references: bool | None = None,
) -> list[str]:
    """Return deterministic violations of the public law-evidence policy."""

    errors: list[str] = []
    if has_law_references is None:
        has_law_references = has_non_empty_law_references(
            patch.get("lawReferences")
        )

    if not _law_related_for_utilization(patch, has_law_references):
        return errors

    statuses = _law_revision_fact_statuses(patch.get("lawRevisionFacts"))
    if statuses == {"hold"}:
        return errors

    details = _suggested_question_items(patch)
    question_text = "\n".join(
        detail.get("question", "")
        for detail in details
        if isinstance(detail.get("question"), str)
    )
    answer_text = "\n".join(
        detail.get("answer", "")
        for detail in details
        if isinstance(detail.get("answer"), str)
    )
    public_text = _public_text_for_patch(patch)

    if not _contains_any(question_text, LAW_CONTEXT_KEYWORDS):
        errors.append(
            "law-related suggestedQuestionDetailsByChoice must include at least one "
            "law/context-specific question"
        )

    if not _contains_any(answer_text, LAW_CONTEXT_KEYWORDS):
        errors.append(
            "law-related suggestedQuestionDetailsByChoice answers must use "
            "law/context evidence"
        )

    anchors = _law_evidence_anchors(patch)
    normalized_public_text = _normalize_for_anchor_match(public_text)
    if anchors and not any(
        _normalize_for_anchor_match(anchor) in normalized_public_text
        for anchor in anchors
    ):
        errors.append(
            "public explanation fields do not mention any concrete law "
            "evidence anchor"
        )

    if "updated_to_current_law" in statuses:
        if not _contains_any(public_text, CURRENT_LAW_TERMS) or not _contains_any(
            public_text, EXAM_TIME_TERMS
        ):
            errors.append(
                "updated_to_current_law explanation must distinguish current "
                "law from exam-time handling"
            )
        if not (
            _contains_any(question_text, CURRENT_LAW_TERMS)
            and _contains_any(question_text, EXAM_TIME_TERMS)
        ):
            errors.append(
                "updated_to_current_law suggestedQuestionDetailsByChoice must ask about "
                "current law and exam-time difference"
            )
    return errors


def validate_law_evidence_utilization(
    *,
    patch: dict[str, Any],
    index: int,
    has_law_references: bool,
    errors: list[str],
) -> None:
    """Append CLI-compatible, index-prefixed utilization violations."""

    errors.extend(
        f"index {index}: {issue}"
        for issue in law_evidence_utilization_issues(
            patch,
            has_law_references=has_law_references,
        )
    )


def explanation_style_issues(
    explanations: Iterable[Any],
    correct_choices: Iterable[Any] | None = None,
    *,
    choice_texts: Iterable[Any] | None = None,
    require_verdict_prefix: bool = True,
    question_type: object = None,
) -> list[str]:
    """Return deterministic violations of the stage-03 Japanese style policy."""

    explanation_values = list(explanations)
    issues: list[str] = []
    verdicts = list(correct_choices) if correct_choices is not None else []
    choices = list(choice_texts) if choice_texts is not None else []
    expected_count = expected_explanation_count(question_type, len(choices))
    if choices and len(explanation_values) != expected_count:
        if uses_question_level_explanation(question_type):
            issues.append(
                "flash_cardとgroup_choiceの解説は問題単位の1件にしてください。"
                f"（解説{len(explanation_values)}件／選択肢{len(choices)}件）"
            )
        else:
            issues.append(
                "解説の件数が選択肢の件数と一致しません。"
                f"（解説{len(explanation_values)}件／選択肢{len(choices)}件）"
            )
    question_level = uses_question_level_explanation(question_type)
    for choice_index, raw in enumerate(explanation_values, start=1):
        item_label = "基本解説" if question_level else f"選択肢{choice_index}"
        text = str(raw or "").strip()
        if not text:
            issues.append(f"{item_label}: 解説が空です。")
            continue
        prefix = VERDICT_PREFIX.match(text)
        if require_verdict_prefix and prefix is None:
            issues.append(
                f"{item_label}: 解説は「正しい。」又は「間違い。」で"
                "始めてください。"
            )
        elif prefix is not None and choice_index <= len(verdicts):
            expected = normalize_verdict(verdicts[choice_index - 1])
            if expected in {"正しい", "間違い"} and prefix.group(1) != expected:
                issues.append(
                    f"{item_label}: 解説冒頭の正誤がcorrectChoiceTextと"
                    "一致しません。"
                )
        if not question_level and choice_index <= len(choices):
            choice = re.sub(r"\s+", "", str(choices[choice_index - 1] or "")).rstrip(
                "。"
            )
            body = text[prefix.end() :] if prefix is not None else text
            body = re.sub(r"\s+", "", body).rstrip("。")
            if choice and body == choice:
                issues.append(
                    f"{item_label}: 解説が選択肢を繰り返すだけです。"
                    "判断理由を説明してください。"
                )
        if LAW_AS_SENTENCE_SUBJECT.search(text):
            issues.append(
                f"{item_label}: 法令名・条文を機械的に文頭の主語へ"
                "置かず、正しい内容を主語にしてください。"
            )
        if POINT_IS_WRONG.search(text):
            issues.append(
                f"{item_label}: 「点が誤り」ではなく、正しい内容と"
                "選択肢との差を示して「ため誤りである」と説明してください。"
            )
    return issues
