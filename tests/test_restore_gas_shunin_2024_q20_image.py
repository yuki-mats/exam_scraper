from __future__ import annotations

import copy

import pytest

from scripts.fix.restore_gas_shunin_2024_q20_image import (
    EXPECTED_IDENTITY,
    IMAGE_URL,
    MIN_SYNC_UPDATED_AT,
    QUESTION_IDS,
    UPDATED_BY_ID,
    build_receipt,
    sync_metadata_is_current,
    verify_canonical,
    verify_document,
)


def document(image_urls: object = None) -> dict[str, object]:
    return {**EXPECTED_IDENTITY, "questionImageUrls": image_urls}


def test_verify_document_accepts_only_missing_empty_or_expected_image() -> None:
    assert verify_document(QUESTION_IDS[0], document()) == "needs_update"
    assert verify_document(QUESTION_IDS[0], document([])) == "needs_update"
    assert verify_document(QUESTION_IDS[0], document([IMAGE_URL])) == "already_applied"

    with pytest.raises(ValueError, match="別の画像を上書きしない"):
        verify_document(QUESTION_IDS[0], document(["https://example.test/other.png"]))


def test_verify_document_rejects_identity_drift() -> None:
    drifted = copy.deepcopy(document())
    drifted["examYear"] = 2025
    with pytest.raises(ValueError, match="identity"):
        verify_document(QUESTION_IDS[0], drifted)


def test_sync_metadata_requires_new_enough_server_timestamp_and_actor() -> None:
    current = document([IMAGE_URL])
    current["updatedAt"] = MIN_SYNC_UPDATED_AT
    current["updatedById"] = UPDATED_BY_ID
    assert sync_metadata_is_current(current) is True

    current["updatedById"] = "someone-else"
    assert sync_metadata_is_current(current) is False


def test_verified_publication_keeps_image_on_all_group_members() -> None:
    payload = verify_canonical()
    targets = {
        question["questionId"]: question
        for question in payload["questions"]
        if question.get("questionId") in QUESTION_IDS
    }
    assert set(targets) == set(QUESTION_IDS)
    assert all(
        targets[question_id]["questionImageUrls"] == [IMAGE_URL]
        for question_id in QUESTION_IDS
    )


def test_receipt_keeps_readback_and_exact_rollback_shape() -> None:
    before = {
        question_id: {
            "fieldExisted": False,
            "questionImageUrls": None,
            "updatedAt": f"old-updated-at-{index}",
            "updatedById": f"old-updated-by-{index}",
            "updateTime": f"before-{index}",
        }
        for index, question_id in enumerate(QUESTION_IDS)
    }
    after = {
        question_id: {
            "questionImageUrls": [IMAGE_URL],
            "updatedAt": MIN_SYNC_UPDATED_AT.isoformat(),
            "updatedById": UPDATED_BY_ID,
            "identityMatches": True,
            "updateTime": f"after-{index}",
        }
        for index, question_id in enumerate(QUESTION_IDS)
    }
    statuses = {question_id: "needs_update" for question_id in QUESTION_IDS}
    needs_writes = {question_id: True for question_id in QUESTION_IDS}

    receipt = build_receipt(
        project_id="repaso-rbaqy4",
        before=before,
        after=after,
        statuses=statuses,
        needs_writes=needs_writes,
    )

    assert receipt["status"] == "applied"
    assert receipt["readback"]["allMatchExpectedAfter"] is True
    assert all(
        item["questionImageUrls"]["deleteField"] is True
        for item in receipt["rollback"]["perQuestion"].values()
    )
