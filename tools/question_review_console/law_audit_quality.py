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


def _verified_law_snapshot_verdicts(
    law_revision_facts: Any,
    *,
    choice_count: int,
    snapshot: str,
) -> list[str] | None:
    if isinstance(law_revision_facts, list):
        if len(law_revision_facts) != choice_count:
            return None
        verdicts: list[str] = []
        has_verified_update = False
        for facts in law_revision_facts:
            if not isinstance(facts, Mapping):
                return None
            audit_status = facts.get("auditStatus")
            review_state = facts.get("reviewState")
            if audit_status == "updated_to_current_law":
                if review_state != "tertiary_verified":
                    return None
                has_verified_update = True
            elif audit_status in {"same_as_current", "not_law_related"}:
                if review_state not in {"secondary_verified", "tertiary_verified"}:
                    return None
            else:
                return None
            selected_snapshot = facts.get(snapshot)
            actual = (
                _normalized_verdicts(selected_snapshot.get("correctChoiceText"))
                if isinstance(selected_snapshot, Mapping)
                else None
            )
            if actual is None or len(actual) != 1:
                return None
            verdicts.append(actual[0])
        return verdicts if has_verified_update else None

    if not isinstance(law_revision_facts, Mapping):
        return None
    if (
        law_revision_facts.get("auditStatus") != "updated_to_current_law"
        or law_revision_facts.get("reviewState") != "tertiary_verified"
    ):
        return None
    selected_snapshot = law_revision_facts.get(snapshot)
    verdicts = (
        _normalized_verdicts(selected_snapshot.get("correctChoiceText"))
        if isinstance(selected_snapshot, Mapping)
        else None
    )
    return verdicts if verdicts is not None and len(verdicts) == choice_count else None


def verified_current_law_verdicts(
    law_revision_facts: Any,
    *,
    choice_count: int,
) -> list[str] | None:
    """Return a complete current-law verdict only for a resolved law update.

    Explanation generation may run after 02a has restored the official
    exam-time answer and before 03b writes the current-law answer back to 23.
    A previously verified law snapshot is therefore the only safe source for
    the effective verdict during that interval.
    """

    return _verified_law_snapshot_verdicts(
        law_revision_facts,
        choice_count=choice_count,
        snapshot="current",
    )


def verified_exam_time_law_verdicts(
    law_revision_facts: Any,
    *,
    choice_count: int,
) -> list[str] | None:
    """Return the exam-time half of a resolved current-law update."""

    return _verified_law_snapshot_verdicts(
        law_revision_facts,
        choice_count=choice_count,
        snapshot="examTime",
    )


def law_revision_current_verdict_issues(
    *,
    correct_choice_text: Any,
    law_revision_facts: Any,
    compare_with_correct_choice: bool = True,
) -> list[dict[str, str]]:
    """Validate current-law verdicts at patch/merged or Firestore granularity.

    Patch and merged questions carry a verdict per choice.  Their
    ``lawRevisionFacts`` may be either a per-choice list or one question-level
    object whose ``current.correctChoiceText`` is a list.  Firestore records
    carry one scalar verdict and one facts object, but ``group_choice`` and
    ``flash_card`` convert the top-level verdict to whether the choice is the
    selected answer while the law snapshot keeps the statement truth.  Callers
    validating those public records therefore disable the value comparison and
    retain only the presence/cardinality check.
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
    if compare_with_correct_choice and actual != expected:
        return [
            issue(
                "law_audit_verdict_mismatch",
                field,
                "トップレベルの正誤と現行法監査判定が一致しません。",
            )
        ]
    return []
