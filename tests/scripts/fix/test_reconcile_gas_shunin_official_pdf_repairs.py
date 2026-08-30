from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts/fix/reconcile_gas_shunin_official_pdf_repairs.py"
PLAN_PATH = (
    ROOT
    / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes"
    / "T030-official-repair-plan.json"
)
SPEC = importlib.util.spec_from_file_location("gas_pdf_repair", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_manual_exam_answer_repairs_preserve_scope() -> None:
    fields, reasons = MODULE.manual_fields(
        "gas-shunin-otsu-2018-seizo-q05-s05",
        {"lawRevisionFacts": {"current": {}, "examTime": {}, "evidenceSummary": {}}},
    )
    assert fields["correctChoiceText"] == "正しい"
    assert fields["explanationText"].startswith("正しい。")
    assert "技術的な疑義" in fields["explanationText"]
    assert fields["lawRevisionFacts"]["examTime"]["correctChoiceText"] == "正しい"
    assert reasons == ["official_answer_alignment", "technical_caveat"]

    fields, reasons = MODULE.manual_fields(
        "gas-shunin-otsu-2018-shohi-q22-s01", {}
    )
    assert fields["correctChoiceText"] == "正しい"
    assert "2018年の出題時点" in fields["explanationText"]
    assert "現行制度では" in fields["explanationText"]
    assert reasons == ["official_answer_alignment", "exam_time_law_scope"]


def test_missing_statement_is_restored_from_existing_official_text() -> None:
    document = {
        "originalQuestionBodyText": "材料に外力を加えると内力が生じる。",
        "questionBodyText": "次の記述の正誤を答えよ。",
    }
    fields, reasons = MODULE.manual_fields("chiefgasengineerlicense-C-10137", document)
    assert fields["originalQuestionChoiceText"] == document["originalQuestionBodyText"]
    assert fields["questionText"] == (
        "次の記述の正誤を答えよ。"
        "[quote]材料に外力を加えると内力が生じる。[/quote]"
    )
    assert reasons == ["restore_official_statement_display"]


def test_metadata_uses_official_identity_and_actual_choice_number() -> None:
    fields = MODULE.metadata_fields(
        "chiefgasengineerlicense-A-10-0246",
        {"choiceNumber": 1},
        {"grade": "kou", "examYear": 2022, "section": "law", "questionNumber": 2},
    )
    assert fields == {
        "examYear": 2022,
        "questionNumber": 2,
        "examSource": "ガス主任技術者（甲種）, 2022年, 問2, 設問1",
        "originalQuestionId": "gasushunin-koushu-hourei-2022-2",
    }


def test_local_rewrite_only_changes_fields_present_in_question_records() -> None:
    value = {
        "questions": [
            {
                "_id": "q1",
                "isDeleted": False,
                "explanationText": "old",
                "questionText": "body",
            },
            {"questionId": "q1", "reason": "audit reference only"},
            {"_id": "q2", "isDeleted": False, "questionText": "body"},
        ]
    }
    after, counts = MODULE.apply_changes_to_value(
        value,
        updates={"q1": {"setFields": {"explanationText": "new", "examYear": 2020}}},
        soft_delete_ids={"q2"},
    )
    assert after["questions"][0]["explanationText"] == "new"
    assert "examYear" not in after["questions"][0]
    assert after["questions"][1] == value["questions"][1]
    assert after["questions"][2]["isDeleted"] is True
    assert counts == {"q1": 1, "q2": 1}


def test_aggregate_source_corrections_keep_local_catalog_reproducible() -> None:
    value = [
        {
            "public_question_id": "58726aadc48afa5f",
            "correctChoiceText": ["間違い", "正しい"],
            "explanationText": ["old", "other"],
        }
    ]
    after, counts = MODULE.apply_aggregate_source_corrections(value)
    assert after[0]["correctChoiceText"][0] == "正しい"
    assert after[0]["explanationText"][0].startswith("正しい。2018年の出題時点")
    assert counts == {"source:58726aadc48afa5f": 2}


def test_generated_plan_has_expected_closed_scope() -> None:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    MODULE.verify_plan_hash(plan)
    assert plan["summary"] == {
        "activeDisplayQuestionCountBefore": 4326,
        "softDeleteTargetCount": 201,
        "softDeleteByGrade": {"kou": 6, "otsu": 195},
        "projectedActiveDisplayQuestionCount": 4125,
        "updateTargetCount": 562,
        "explanationPrefixRepairCount": 540,
        "metadataRepairCount": 19,
        "manualContentOrAnswerRepairCount": 3,
        "holdCount": 0,
    }
    update_ids = {item["questionId"] for item in plan["updates"]}
    delete_ids = {item["questionId"] for item in plan["softDeletes"]}
    assert update_ids.isdisjoint(delete_ids)
    assert len(update_ids) == 562
    assert len(delete_ids) == 201
    assert all(item["officialEvidence"]["questionPdf"]["sha256"] for item in plan["updates"])
    assert all(item["officialEvidence"]["answerPdf"]["sha256"] for item in plan["softDeletes"])
