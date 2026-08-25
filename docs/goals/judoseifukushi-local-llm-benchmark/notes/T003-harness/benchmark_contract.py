"""T003 blind benchmark constants and source-resolution helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


JUDO = {
    "source-answer-missing": ["9c3273bf54057cd0", "fa0d4e2042e65b59"],
    "law": ["c5167b46942fb08e", "0eb595c2c11278f5", "8987ec55216cbc63", "dfb3fe84e07f47f9", "1ebaca9b85c6dd6e"],
    "numeric": ["130d5c77cc5c2b8b", "5c1ab42128e170ab", "ee361042818c9b9f", "c582757f2a97a68a", "77eea1850fecb0ab"],
    "long": ["b22f649e0b947399", "c1363e3f0487174c", "85cdb2c54567b04a"],
    "negative": ["8300bc9178872872", "31fa2012e42b713a", "23af5153f13d1a5c", "63ed9af2dc1cda2b", "715ae907c4b74436"],
    "current-medical": ["a28db6ba1c8e4c65", "01bd0255c9fc8371", "2380e0e939cce3b7", "1d2014527a7acabb", "da301fba93a4d48c", "ab3c9ed5d41aa3fd", "8168f912ed724458", "e1a6f5219f4e01d8", "a2f7e9ea703084cb", "5d85b2e35574d926"],
}

BUILDING = {
    "dd07b4977677b7bb": ["explanation"],
    "3239d392acd6236c": ["correct_choice"],
    "64032ef7f4bac816": ["question_type"],
    "64bd269e44533561": ["question_type", "explanation"],
    "3b24e06367db4222": ["law_context", "law_audit", "explanation"],
    "ed7d14b661421a12": ["question_type", "explanation"],
}

ALL_IDS = [item for values in JUDO.values() for item in values] + list(BUILDING)
LAW_IDS = set(JUDO["law"])
MULTI_ANSWER_IDS = {"130d5c77cc5c2b8b", "b22f649e0b947399", "85cdb2c54567b04a"}
IMAGE_IDS = {"64bd269e44533561", "ed7d14b661421a12"}
ORACLE_KEYS = {
    "correctChoiceText", "answer_result_text", "answerTableCorrectChoiceNumbers",
    "choiceClassCorrectChoiceNumbers", "answer_result_inferred_correct_choice_numbers",
    "explanation_common_prefix_inferred_correct_choice", "oracle", "answer",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_index(repo: Path) -> dict[str, tuple[Path, int, dict]]:
    wanted = set(ALL_IDS)
    found: dict[str, tuple[Path, int, dict]] = {}
    for path in repo.glob("output/**/00_source/*.json"):
        try:
            body = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for index, question in enumerate(body.get("question_bodies", [])):
            public_id = question.get("public_question_id") or question.get("original_question_id")
            if public_id in wanted:
                if public_id in found:
                    raise ValueError(f"duplicate source id: {public_id}")
                found[public_id] = (path, index, question)
    return found


def targets_for(public_id: str) -> list[str]:
    if public_id in BUILDING:
        return BUILDING[public_id]
    if public_id in JUDO["source-answer-missing"]:
        return ["source_evidence_hold"]
    if public_id in LAW_IDS:
        return ["correct_choice", "law_context", "law_audit", "explanation"]
    return ["correct_choice", "explanation"]


def stratum_for(public_id: str) -> str:
    if public_id in BUILDING:
        return "building"
    return next(name for name, ids in JUDO.items() if public_id in ids)
