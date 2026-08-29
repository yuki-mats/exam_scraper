from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.fix.materialize_gas_shunin_missing_explanations import (
    apply_overrides,
    canonicalize_explanation_prefix,
    explanation_hash,
    mark_rejected_answer_conflicts,
    normalize_text,
    select_manual_review_source,
    validate_answer_correction_decision,
    validate_answer_alignment,
)


def test_normalize_text_handles_spacing_and_unicode_width():
    assert normalize_text("Ａ － B\n") == "A-B"


def test_validate_answer_alignment_accepts_matching_true_false_prefix():
    validate_answer_alignment(
        {
            "questionId": "q1",
            "questionType": "true_false",
            "correctChoiceText": "間違い",
        },
        "間違い。基準値が異なる。",
    )


def test_validate_answer_alignment_rejects_mismatch():
    with pytest.raises(ValueError, match="prefix mismatch"):
        validate_answer_alignment(
            {
                "questionId": "q1",
                "questionType": "true_false",
                "correctChoiceText": "正しい",
            },
            "間違い。基準値が異なる。",
        )


def test_canonicalize_explanation_prefix_maps_review_labels():
    assert canonicalize_explanation_prefix("正解。計算結果は9.5。", "正解") == "正しい。計算結果は9.5。"
    assert canonicalize_explanation_prefix("不正解。式が異なる。", "不正解") == "間違い。式が異なる。"
    assert canonicalize_explanation_prefix("定義と一致する。", "正しい") == "正しい。定義と一致する。"


def test_select_manual_review_source_accepts_unique_high_similarity_choice():
    source = {
        "kind": "manual_01_04_review",
        "explanationHash": "hash-1",
        "sourceCorrectChoiceText": "正しい",
        "explanationText": "正しい。理由。",
    }
    selected = select_manual_review_source(
        "設問[quote]配管下部と上部の温度差が大きくなるため、時間をかける必要がある。[/quote]",
        {},
        [
            (
                normalize_text("配管下部と上部の温度差が大きくなるため時間をかける必要がある。"),
                source,
            ),
            (normalize_text("全く異なる長い選択肢の記述である。"), {**source, "explanationHash": "hash-2"}),
        ],
    )

    assert selected is not None
    assert selected["explanationText"] == "正しい。理由。"
    assert selected["choiceSimilarity"] >= 0.94


def test_select_manual_review_source_rejects_short_fuzzy_choice():
    assert (
        select_manual_review_source(
            "設問[quote]9.5[/quote]",
            {},
            [(normalize_text("9.6"), {"explanationHash": "hash"})],
        )
        is None
    )


def test_apply_overrides_requires_existing_unique_target(tmp_path: Path):
    decisions = tmp_path / "decisions.json"
    overrides = tmp_path / "overrides.json"
    output = tmp_path / "final.json"
    decisions.write_text(
        '{"decisions":[{"questionId":"q1","status":"needs_review","source":null}]}',
        encoding="utf-8",
    )
    overrides.write_text(
        '{"overrides":[{"questionId":"q1","source":{"kind":"authored_from_similar","explanationText":"正しい。定義どおりである。"}}]}',
        encoding="utf-8",
    )
    result = apply_overrides(decisions, overrides, output)
    source = result["decisions"][0]["source"]
    assert result["decisions"][0]["status"] == "ready"
    assert source["explanationHash"] == explanation_hash(source["explanationText"])


def test_apply_overrides_rejects_unknown_target(tmp_path: Path):
    decisions = tmp_path / "decisions.json"
    overrides = tmp_path / "overrides.json"
    decisions.write_text('{"decisions":[{"questionId":"q1"}]}', encoding="utf-8")
    overrides.write_text(
        '{"overrides":[{"questionId":"q2","source":{"kind":"authored_from_similar","explanationText":"正しい。"}}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in ledger"):
        apply_overrides(decisions, overrides, tmp_path / "output.json")


def test_apply_overrides_records_explicit_answer_conflict_hold(tmp_path: Path):
    decisions = tmp_path / "decisions.json"
    overrides = tmp_path / "holds.json"
    decisions.write_text(
        '{"decisions":[{"questionId":"q1","status":"needs_review","source":null}]}',
        encoding="utf-8",
    )
    overrides.write_text(
        """{
          "overrides": [{
            "questionId": "q1",
            "status": "hold",
            "reason": "Firestore answer conflict",
            "evidence": ["verified local review"],
            "proposedCorrectChoiceText": "間違い",
            "proposedExplanationText": "間違い。根拠と一致しない。"
          }]
        }""",
        encoding="utf-8",
    )

    result = apply_overrides(decisions, overrides, tmp_path / "output.json")

    decision = result["decisions"][0]
    assert decision["status"] == "hold"
    assert decision["source"] is None
    assert decision["proposedCorrectChoiceText"] == "間違い"
    assert result["counts"] == {"total": 1, "ready": 0, "needsReview": 0, "hold": 1}


def test_mark_rejected_answer_conflicts_uses_verified_opposite_source(tmp_path: Path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        """{
          "decisions": [{
            "questionId": "q1",
            "correctChoiceText": "正しい",
            "status": "needs_review",
            "source": null,
            "reviewContext": {"rejectedSource": {
              "kind": "manual_01_04_review",
              "sourceCorrectChoiceText": "間違い",
              "explanationText": "不正解。基準値と異なる。"
            }}
          }]
        }""",
        encoding="utf-8",
    )

    result = mark_rejected_answer_conflicts(decisions, tmp_path / "output.json")

    decision = result["decisions"][0]
    assert decision["status"] == "hold"
    assert decision["proposedCorrectChoiceText"] == "間違い"
    assert decision["proposedExplanationText"] == "間違い。基準値と異なる。"
    assert result["answerConflictsMarked"] == 1


def test_mark_rejected_answer_conflicts_leaves_same_answer_for_review(tmp_path: Path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        """{"decisions":[{
          "questionId":"q1","correctChoiceText":"正しい","status":"needs_review","source":null,
          "reviewContext":{"rejectedSource":{"sourceCorrectChoiceText":"正しい","explanationText":"正しい。"}}
        }]}""",
        encoding="utf-8",
    )

    result = mark_rejected_answer_conflicts(decisions, tmp_path / "output.json")

    assert result["decisions"][0]["status"] == "needs_review"
    assert result["answerConflictsMarked"] == 0


def test_validate_answer_correction_decision_accepts_evidenced_opposite_answer():
    validate_answer_correction_decision(
        {
            "questionId": "q1",
            "status": "hold",
            "correctChoiceText": "正しい",
            "proposedCorrectChoiceText": "間違い",
            "proposedExplanationText": "間違い。条文と異なる。",
            "holdReason": "Firestore answer conflict",
            "holdEvidence": ["manual review"],
        }
    )


def test_validate_answer_correction_decision_rejects_same_answer():
    with pytest.raises(ValueError, match="does not change answer"):
        validate_answer_correction_decision(
            {
                "questionId": "q1",
                "status": "hold",
                "correctChoiceText": "正しい",
                "proposedCorrectChoiceText": "正しい",
                "proposedExplanationText": "正しい。条文どおり。",
                "holdReason": "review",
                "holdEvidence": ["manual review"],
            }
        )
