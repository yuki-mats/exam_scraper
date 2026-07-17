from __future__ import annotations

from typing import Any, Mapping

from tools.question_review_console.projection import normalize_verdict


VALID_VERDICTS = {"正しい", "間違い"}


def _normalized_verdicts(value: Any) -> list[str] | None:
    if isinstance(value, list):
        verdicts = [normalize_verdict(item) for item in value]
        return (
            verdicts
            if verdicts and all(item in VALID_VERDICTS for item in verdicts)
            else None
        )
    verdict = normalize_verdict(value)
    return [verdict] if verdict in VALID_VERDICTS else None


def law_revision_current_verdict_issues(
    *,
    correct_choice_text: Any,
    law_revision_facts: Any,
) -> list[dict[str, str]]:
    """Validate current-law verdicts at patch/merged or Firestore granularity.

    Patch and merged questions carry a verdict per choice.  Their
    ``lawRevisionFacts`` may be either a per-choice list or one question-level
    object whose ``current.correctChoiceText`` is a list.  Firestore records
    carry one scalar verdict and one facts object.  This function deliberately
    accepts both representations so every caller applies the same invariant.
    """

    expected = _normalized_verdicts(correct_choice_text)
    if expected is None:
        return []

    def issue(code: str, field: str, detail: str) -> dict[str, str]:
        return {"code": code, "field": field, "detail": detail}

    if isinstance(law_revision_facts, list):
        if len(law_revision_facts) != len(expected):
            return [
                issue(
                    "law_audit_metadata_incomplete",
                    "lawRevisionFacts",
                    (
                        "lawRevisionFactsの件数が選択肢の正誤数と一致しません"
                        f"（正誤={len(expected)}、監査={len(law_revision_facts)}）。"
                    ),
                )
            ]
        issues: list[dict[str, str]] = []
        for index, (facts, expected_verdict) in enumerate(
            zip(law_revision_facts, expected, strict=True)
        ):
            field = f"lawRevisionFacts[{index}].current.correctChoiceText"
            current = facts.get("current") if isinstance(facts, Mapping) else None
            actual = (
                _normalized_verdicts(current.get("correctChoiceText"))
                if isinstance(current, Mapping)
                else None
            )
            if actual is None or len(actual) != 1:
                issues.append(
                    issue(
                        "law_audit_metadata_incomplete",
                        field,
                        f"選択肢{index + 1}の現行法監査判定がありません。",
                    )
                )
            elif actual[0] != expected_verdict:
                issues.append(
                    issue(
                        "law_audit_verdict_mismatch",
                        field,
                        (
                            f"選択肢{index + 1}のトップレベル正誤と"
                            "現行法監査判定が一致しません。"
                        ),
                    )
                )
        return issues

    if not isinstance(law_revision_facts, Mapping):
        return [
            issue(
                "law_audit_metadata_incomplete",
                "lawRevisionFacts",
                "現行法監査スナップショットがありません。",
            )
        ]

    field = "lawRevisionFacts.current.correctChoiceText"
    current = law_revision_facts.get("current")
    actual_value = (
        current.get("correctChoiceText") if isinstance(current, Mapping) else None
    )
    actual = _normalized_verdicts(actual_value)
    expects_choice_list = isinstance(correct_choice_text, list)
    has_choice_list = isinstance(actual_value, list)
    if (
        actual is None
        or expects_choice_list != has_choice_list
        or len(actual) != len(expected)
    ):
        detail = (
            "各選択肢に対応する現行法監査判定がありません。"
            if expects_choice_list
            else "現行法監査スナップショットの判定がありません。"
        )
        return [issue("law_audit_metadata_incomplete", field, detail)]
    if actual != expected:
        return [
            issue(
                "law_audit_verdict_mismatch",
                field,
                "トップレベルの正誤と現行法監査判定が一致しません。",
            )
        ]
    return []
