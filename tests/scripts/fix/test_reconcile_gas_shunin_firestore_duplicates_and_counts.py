from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.fix.reconcile_gas_shunin_firestore_duplicates_and_counts import (
    classify_group,
    content_duplicate_groups,
    direct_content_match,
    fingerprint,
    normalize_choice,
)


def question(question_id: str, *, body: str = "本文", choice: str = "1", text: str = "設問") -> dict:
    return {
        "questionId": question_id,
        "examYear": 2023,
        "originalQuestionBodyText": body,
        "originalQuestionChoiceText": choice,
        "questionText": text,
        "questionType": "true_false",
        "correctChoiceText": "正しい",
        "questionSetId": "set-1",
        "isDeleted": False,
        "isChoiceOnly": False,
    }


def test_numeric_choices_are_normalized_for_duplicate_identity() -> None:
    assert normalize_choice("1") == normalize_choice("1.0")
    values = [question("legacy", choice="1"), question("canonical", choice="1.0")]
    assert content_duplicate_groups(values) == [["canonical", "legacy"]]


def test_same_generic_stem_with_different_choices_is_not_duplicate() -> None:
    values = [question("q1", choice="選択肢A"), question("q2", choice="選択肢B")]
    assert content_duplicate_groups(values) == []


def test_otsu_canonical_wins_over_legacy() -> None:
    canonical = "gas-shunin-otsu-2023-law-q01-s01"
    legacy = "gasushunin-otsushu-hourei-2023-1-1"
    values = {item["questionId"]: item for item in [question(canonical), question(legacy)]}
    decision = classify_group("otsu", [canonical, legacy], values)
    assert decision["keepIds"] == [canonical]
    assert decision["softDeleteIds"] == [legacy]


def test_q21_and_q22_are_kept_as_distinct_official_questions() -> None:
    q21 = "gas-shunin-otsu-2020-shohi-q21-s01"
    q22 = "gas-shunin-otsu-2020-shohi-q22-s01"
    legacy = "gasushunin-otsushu-gizyutsu-2020-22-1"
    values = {item["questionId"]: item for item in [question(q21), question(q22), question(legacy)]}
    decision = classify_group("otsu", [q21, q22, legacy], values)
    assert decision["keepIds"] == [q21, q22]
    assert decision["softDeleteIds"] == [legacy]
    assert decision["contentRepairIds"] == [q21]


def test_legacy_technology_question_wins_over_misregistered_law_copy() -> None:
    technology = "gasushunin-otsushu-gizyutsu-2021-3-1"
    law = "gasushunin-otsushu-hourei-2021-3-1"
    values = {item["questionId"]: item for item in [question(technology), question(law)]}
    decision = classify_group("otsu", [technology, law], values)
    assert decision["keepIds"] == [technology]
    assert decision["softDeleteIds"] == [law]


def test_direct_match_requires_same_year_choice_and_content() -> None:
    left = question("left", body="同じ本文", choice="2")
    right = question("right", body="同じ本文", choice="2.0")
    assert direct_content_match(left, right)
    right["examYear"] = 2022
    assert not direct_content_match(left, right)


def test_fingerprint_changes_when_logical_delete_changes() -> None:
    value = question("q1")
    fields = ("isDeleted", "questionText")
    before = fingerprint(value, fields)
    value["isDeleted"] = True
    assert fingerprint(value, fields) != before
