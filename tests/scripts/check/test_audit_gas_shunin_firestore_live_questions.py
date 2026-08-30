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


def test_source_rows_prefers_choice_level_question_set_ids() -> None:
    catalog = {
        "sample": {
            "qualification": "gas-shunin-kou",
            "year": 2025,
            "sourceKey": "sample",
            "sourceIds": {"source-id"},
            "choices": ["肢1", "肢2"],
            "answers": ["正しい", "間違い"],
            "explanations": ["説明1", "説明2"],
            "questionSetId": "question-set-default",
            "choiceQuestionSetIds": ["question-set-1", "question-set-2"],
            "questionType": "true_false",
            "fieldEvidence": {},
        }
    }

    rows = audit.source_rows(catalog)

    assert [row["questionSetId"] for row in rows] == [
        "question-set-1",
        "question-set-2",
    ]


def test_basic_explanation_prefix_follows_current_prompt() -> None:
    question = {
        "questionType": "group_choice",
        "correctChoiceText": "正しい",
        "explanationText": "正解は4である。",
    }

    assert audit.explanation_issues(question) == ["explanation_prefix_mismatch"]


def test_group_choice_same_stem_different_choices_are_not_duplicates() -> None:
    questions = [
        {
            "questionId": "q6-s3",
            "examYear": 2018,
            "questionText": "技術基準に関する問題",
            "originalQuestionChoiceText": "溶接施工方法を確認する。",
        },
        {
            "questionId": "q7-s5",
            "examYear": 2018,
            "questionText": "技術基準に関する問題",
            "originalQuestionChoiceText": "逆流を防止する。",
        },
    ]

    assert audit.duplicate_map(questions) == {}


def test_official_verification_requires_exact_content_hash() -> None:
    question = {
        "questionId": "verified-question",
        "examYear": 2022,
        "questionText": "問題[quote]肢[/quote]",
        "originalQuestionBodyText": "問題",
        "questionBodyText": "問題",
        "originalQuestionChoiceText": "肢",
        "correctChoiceText": "正しい",
        "explanationText": "正しい。理由。",
        "questionSetId": "set-1",
        "questionType": "true_false",
    }
    verification = {
        "verified-question": {
            "questionId": "verified-question",
            "verificationStatus": "official_pdf_verified",
            "verifiedContentSha256": audit.official_content_hash(question),
            "officialEvidence": {"pdfPage": 1},
        }
    }

    assert audit.official_verified_source(question, verification) is not None
    question["originalQuestionChoiceText"] = "変更された肢"
    assert audit.official_verified_source(question, verification) is None


def test_air_ratio_lambda_is_accepted_as_dimensionless_result() -> None:
    explanation = (
        "湿り燃焼ガス総量は10λ+1、過剰酸素は2(λ-1)である。"
        "2(λ-1)/(10λ+1)=0.05を解くとλ≒1.37となるため正しい。"
    )

    assert audit.beginner_flags(explanation)["hasUnit"] is True
