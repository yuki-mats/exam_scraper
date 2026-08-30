from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FIX_DIR = ROOT / "scripts" / "fix"
if str(FIX_DIR) not in sys.path:
    sys.path.insert(0, str(FIX_DIR))

import reconcile_gas_shunin_individual_questions as reconcile  # noqa: E402


def test_repair_specs_are_exactly_the_authorized_19_questions() -> None:
    assert len(reconcile.REPAIR_SPECS) == 19
    assert "chiefgasengineerlicense-A-10-0241" in reconcile.REPAIR_SPECS
    assert "gas-shunin-kou-2017-kiso-q11-s04" in reconcile.REPAIR_SPECS
    assert "chiefgasengineerlicense-C-10298" in reconcile.REPAIR_SPECS


def test_true_false_text_fields_keep_body_and_choice_in_sync() -> None:
    document = {"questionType": "true_false"}
    result = reconcile.text_fields(document, body="本文", choice="選択肢")

    assert result == {
        "originalQuestionBodyText": "本文",
        "questionBodyText": "本文",
        "originalQuestionChoiceText": "選択肢",
        "questionText": "本文[quote]選択肢[/quote]",
    }


def test_group_choice_display_does_not_append_choice() -> None:
    document = {"questionType": "group_choice"}
    result = reconcile.text_fields(document, body="本文", choice="正答肢")

    assert result["questionText"] == "本文"


def test_official_content_hash_ignores_unrelated_user_or_timestamp_fields() -> None:
    question = {field: field for field in reconcile.OFFICIAL_VERIFICATION_FIELDS}
    expected = reconcile.official_content_hash(question)
    question["updatedAt"] = "later"
    question["userAnswer"] = "must not matter"

    assert reconcile.official_content_hash(question) == expected
