from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECK_DIR = ROOT / "scripts" / "check"
if str(CHECK_DIR) not in sys.path:
    sys.path.insert(0, str(CHECK_DIR))

import audit_gas_shunin_firestore_live_questions as audit  # noqa: E402


def test_shared_legacy_original_id_variants_keep_separate_answers() -> None:
    catalog = audit.load_source_catalog("gas-shunin-kou")
    rows = audit.source_rows(catalog)
    by_firestore_id = {
        identity: row
        for row in rows
        for identity in row["sourceIds"]
        if identity in {
            "chiefgasengineerlicense-A-10-0163",
            "chiefgasengineerlicense-A-10-0244",
        }
    }

    assert len(catalog) == 528
    assert by_firestore_id["chiefgasengineerlicense-A-10-0163"]["answer"] == "正しい"
    assert by_firestore_id["chiefgasengineerlicense-A-10-0244"]["answer"] == "正しい"


def test_true_false_requires_the_statement_in_display_text() -> None:
    question = {
        "questionType": "true_false",
        "questionText": "次の記述の正誤を答えよ。",
        "originalQuestionChoiceText": "応力は単位断面積当たりの内力である。",
    }

    assert audit.identity_issues(question, None) == [
        "true_false_statement_missing_from_question_text"
    ]


def test_basic_explanation_prefix_follows_current_prompt() -> None:
    question = {
        "questionType": "group_choice",
        "correctChoiceText": "正しい",
        "explanationText": "正解は4である。",
    }

    assert audit.explanation_issues(question) == ["explanation_prefix_mismatch"]
