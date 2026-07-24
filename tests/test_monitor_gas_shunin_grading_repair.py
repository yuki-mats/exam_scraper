import hashlib
import json
from pathlib import Path

from scripts.check.monitor_gas_shunin_grading_repair import (
    canonical_sha256,
    load_candidate_questions,
    source_snapshot_fingerprint,
    target_candidate_fingerprint,
    upload_difference_fields,
)


def test_load_candidate_questions_rejects_duplicate_ids(tmp_path: Path) -> None:
    for name in ("2024_kou_firestore_a.json", "2024_otsu_firestore_b.json"):
        (tmp_path / name).write_text(
            json.dumps({"questions": [{"questionId": "duplicate"}]}),
            encoding="utf-8",
        )

    try:
        load_candidate_questions(sorted(tmp_path.glob("*.json")))
    except ValueError as exc:
        assert "重複" in str(exc)
    else:
        raise AssertionError("duplicate questionId must fail")


def test_source_snapshot_fingerprint_uses_relative_file_hashes(tmp_path: Path) -> None:
    (tmp_path / "question_1.json").write_bytes(b'{"a":1}\\n')
    (tmp_path / "question_2.json").write_bytes(b'{"b":2}\\n')
    expected = canonical_sha256(
        [
            ("question_1.json", hashlib.sha256(b'{"a":1}\\n').hexdigest()),
            ("question_2.json", hashlib.sha256(b'{"b":2}\\n').hexdigest()),
        ]
    )

    assert source_snapshot_fingerprint(tmp_path) == expected


def test_target_candidate_fingerprint_preserves_artifact_question_order() -> None:
    first = {"questionId": "q1", "questionType": "flash_card"}
    second = {"questionId": "q2", "questionType": "flash_card"}

    assert target_candidate_fingerprint([first, second]) == canonical_sha256(
        [first, second]
    )
    assert target_candidate_fingerprint([first, second]) != target_candidate_fingerprint(
        [second, first]
    )


def test_upload_difference_fields_uses_upload_contract() -> None:
    candidate = {
        "questionId": "q1",
        "questionSetId": "set",
        "listGroupId": "2024",
        "originalQuestionId": "original",
        "originalQuestionBodyText": "body",
        "questionBodyText": "body",
        "originalQuestionChoiceText": "choice",
        "questionText": "body",
        "questionType": "flash_card",
        "qualificationId": "gas-shunin-otsu",
        "correctChoiceText": "正しい",
        "examSource": "source",
        "questionTags": [],
        "isOfficial": True,
        "isDeleted": False,
        "isChoiceOnly": False,
        "isGroupable": True,
        "explanationText": "explanation",
    }
    live = dict(candidate)
    live["questionType"] = "true_false"
    live["correctChoiceText"] = "正しい"

    assert upload_difference_fields(candidate, live) == ["questionType"]
