from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FIX_DIR = ROOT / "scripts" / "fix"
if str(FIX_DIR) not in sys.path:
    sys.path.insert(0, str(FIX_DIR))

import apply_gas_shunin_question_set_semantic_repairs as repair  # noqa: E402


def test_supplemental_target_splits_otsu_regulations() -> None:
    base = {"questionSetId": "chiefgasengineerlicense-B-1230"}

    assert repair.supplemental_target(
        "otsu", {**base, "originalQuestionChoiceText": "保安業務規程を定める。"}
    )[0] == "chiefgasengineerlicense-B-1250"
    assert repair.supplemental_target(
        "otsu", {**base, "originalQuestionChoiceText": "保安規程を定める。"}
    )[0] == "chiefgasengineerlicense-B-1240"
    assert repair.supplemental_target(
        "otsu", {**base, "originalQuestionChoiceText": "ガス工作物を基準に適合させる。"}
    )[0] == "chiefgasengineerlicense-B-1120"


def test_audit_target_uses_ideal_taxonomy_when_available() -> None:
    row = {
        "idealQuestionSetDisplayName": "技省令_静電気除去措置（乙種）",
        "recommendedQuestionSetId": "chiefgasengineerlicense-B-1760",
    }

    assert repair.audit_target(row) == "chiefgasengineerlicense-B-2190"


def test_apply_projection_to_record_sets_choice_level_projection() -> None:
    record = {"sourceQuestionKey": "sample", "questionSetId": "old"}
    projection = {
        "sourceQuestionKey": "sample",
        "questionSetId": "set-1",
        "choiceQuestionSetIds": ["set-1", "set-2"],
    }

    counts = repair.apply_projection_to_record(record, projection)

    assert record["questionSetId"] == "set-1"
    assert record["choiceQuestionSetIds"] == ["set-1", "set-2"]
    assert record["questionSetIdResolution"] == "choiceQuestionSetIds"
    assert sum(counts.values()) == 2


def test_local_record_identity_tracks_exact_record_precondition() -> None:
    record = {
        "sourceQuestionKey": "legacy-key",
        "reviewQuestionId": "firestore:q-1,q-2",
        "questionSetId": "old",
        "choiceTextList": ["肢 1", "肢\n2"],
    }

    identity = repair.local_record_identity(record)

    assert identity["embeddedQuestionIds"] == ["q-1", "q-2"]
    assert identity["choiceTexts"] == ["肢1", "肢2"]
    assert identity["questionSetId"] == "old"


def test_apply_projection_to_record_merges_sparse_choice_targets() -> None:
    record = {
        "sourceQuestionKey": "sample",
        "questionSetId": "default-set",
        "choiceTextList": ["肢1", "肢2", "肢3"],
    }
    projection = {
        "sourceQuestionKey": "sample",
        "questionSetId": "default-set",
        "choiceQuestionSetIds": None,
        "choiceQuestionSetIdsByIndex": {"2": "set-2"},
    }

    repair.apply_projection_to_record(record, projection)

    assert record["choiceQuestionSetIds"] == [
        "default-set",
        "set-2",
        "default-set",
    ]
