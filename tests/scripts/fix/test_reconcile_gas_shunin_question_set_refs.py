from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.fix.reconcile_gas_shunin_question_set_refs import (
    apply_local,
    build_plan,
    verify_plan_hash,
)


MALFORMED = (
    "projects/undefined/databases/(default)/documents/questionSets/set-1"
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_snapshot(root: Path, *, malformed_ref: str | None = MALFORMED) -> Path:
    questions = [
        {
            "questionId": "q-1",
            "questionSetId": "set-1",
            "questionSetRef": malformed_ref,
            "questionText": "問題1",
            "isDeleted": False,
            "isChoiceOnly": False,
        },
        {
            "questionId": "q-2",
            "questionSetId": "set-1",
            "questionText": "問題2",
            "isDeleted": False,
            "isChoiceOnly": False,
        },
    ]
    if malformed_ref is None:
        questions[0].pop("questionSetRef")
    write_json(root / "reconstructed/questions.json", {"questions": questions})
    raw_questions = []
    for question in questions:
        raw_questions.append(
            {
                "_id": question["questionId"],
                "decoded": question,
                "updateTime": "2026-08-30T00:00:00Z",
            }
        )
    write_json(root / "raw/questions.json", {"documents": raw_questions})
    write_json(
        root / "raw/questionSets.json",
        {"documents": [{"_id": "set-1", "decoded": {"questionCount": 2}}]},
    )
    return root


def make_local_root(root: Path) -> tuple[Path, Path]:
    current = root / "questions_json/2025/22_questionSetId_linked/current.json"
    protected = root / "questions_json/2025/00_source/source.json"
    payload = [
        {
            "firestore_docs": [
                {
                    "questionId": "q-1",
                    "questionSetId": "set-1",
                    "questionSetRef": MALFORMED,
                    "questionText": "問題1",
                }
            ]
        }
    ]
    write_json(current, payload)
    write_json(protected, payload)
    return current, protected


def build_fixture_plan(tmp_path: Path) -> tuple[dict, Path, Path]:
    kou = make_snapshot(tmp_path / "kou")
    otsu = make_snapshot(tmp_path / "otsu", malformed_ref=None)
    local_root = tmp_path / "output/gas-shunin-kou"
    current, protected = make_local_root(local_root)
    plan = build_plan(
        kou_snapshot=kou,
        otsu_snapshot=otsu,
        local_roots=[local_root],
    )
    return plan, current, protected


def test_build_plan_separates_targets_omitted_and_protected_local_files(
    tmp_path: Path,
) -> None:
    plan, _, _ = build_fixture_plan(tmp_path)

    assert plan["summary"]["activeDisplayQuestionCount"] == 4
    assert plan["summary"]["deleteFieldTargetCount"] == 1
    assert plan["summary"]["alreadyOmittedCount"] == 3
    assert plan["summary"]["localTargetFileCount"] == 1
    assert plan["summary"]["localRemovedReferenceCount"] == 1
    assert plan["summary"]["questionSetIdMismatchCount"] == 0
    assert plan["summary"]["missingParentCount"] == 0
    assert plan["protectedLocalTree"]["fileCount"] == 1
    verify_plan_hash(plan)


def test_apply_local_removes_only_current_derived_reference(tmp_path: Path) -> None:
    plan, current, protected = build_fixture_plan(tmp_path)
    protected_before = protected.read_bytes()
    receipt = apply_local(plan=plan)

    assert receipt["changedFileCount"] == 1
    assert receipt["removedReferenceCount"] == 1
    assert "questionSetRef" not in current.read_text(encoding="utf-8")
    assert protected.read_bytes() == protected_before
    payload = json.loads(current.read_text(encoding="utf-8"))
    assert payload[0]["firestore_docs"][0]["questionSetId"] == "set-1"
    assert payload[0]["firestore_docs"][0]["questionText"] == "問題1"


def test_build_plan_stops_on_reference_id_mismatch(tmp_path: Path) -> None:
    bad_ref = (
        "projects/undefined/databases/(default)/documents/questionSets/set-other"
    )
    kou = make_snapshot(tmp_path / "kou", malformed_ref=bad_ref)
    otsu = make_snapshot(tmp_path / "otsu", malformed_ref=None)
    local_root = tmp_path / "output/gas-shunin-kou"
    make_local_root(local_root)

    with pytest.raises(ValueError, match="questionSetRef/questionSetId mismatch"):
        build_plan(
            kou_snapshot=kou,
            otsu_snapshot=otsu,
            local_roots=[local_root],
        )


def test_build_plan_stops_on_unexpected_reference_shape(tmp_path: Path) -> None:
    valid_ref = (
        "projects/repaso-rbaqy4/databases/(default)/documents/questionSets/set-1"
    )
    kou = make_snapshot(tmp_path / "kou", malformed_ref=valid_ref)
    otsu = make_snapshot(tmp_path / "otsu", malformed_ref=None)
    local_root = tmp_path / "output/gas-shunin-kou"
    make_local_root(local_root)

    with pytest.raises(ValueError, match="unexpected active questionSetRef"):
        build_plan(
            kou_snapshot=kou,
            otsu_snapshot=otsu,
            local_roots=[local_root],
        )


def test_plan_hash_is_fail_closed(tmp_path: Path) -> None:
    plan, _, _ = build_fixture_plan(tmp_path)
    plan["summary"]["deleteFieldTargetCount"] = 999

    with pytest.raises(ValueError, match="plan hash mismatch"):
        verify_plan_hash(plan)
