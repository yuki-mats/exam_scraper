from __future__ import annotations

import copy

import pytest

from scripts.fix.fix_kanrigyoumu_2018_q38_lock_typo import (
    AFTER_FIELDS,
    BEFORE_FIELDS,
    IDENTITY_FIELDS,
    build_receipt,
    verify_snapshot,
)


def snapshot(fields: dict[str, object]) -> dict[str, object]:
    return {**IDENTITY_FIELDS, **fields}


def test_verify_snapshot_accepts_only_exact_before_or_after() -> None:
    assert verify_snapshot(snapshot(BEFORE_FIELDS)) == "needs_update"
    assert verify_snapshot(snapshot(AFTER_FIELDS)) == "already_applied"

    mixed = copy.deepcopy(BEFORE_FIELDS)
    mixed["questionText"] = AFTER_FIELDS["questionText"]
    with pytest.raises(ValueError, match="どちらとも一致しません"):
        verify_snapshot(snapshot(mixed))


def test_verify_snapshot_rejects_identity_drift() -> None:
    drifted = snapshot(BEFORE_FIELDS)
    drifted["questionNumber"] = 39
    with pytest.raises(ValueError, match="identity"):
        verify_snapshot(drifted)


def test_receipt_keeps_exact_rollback_and_readback() -> None:
    receipt = build_receipt(
        project_id="repaso-rbaqy4",
        status="applied",
        before_update_time="before",
        after_update_time="after",
        readback=snapshot(AFTER_FIELDS),
    )

    assert receipt["readback"]["matchesExpectedAfter"] is True
    assert receipt["readback"]["identityMatches"] is True
    assert receipt["rollback"]["values"] == BEFORE_FIELDS
    assert receipt["change"]["before"] == BEFORE_FIELDS
    assert receipt["change"]["after"] == AFTER_FIELDS
