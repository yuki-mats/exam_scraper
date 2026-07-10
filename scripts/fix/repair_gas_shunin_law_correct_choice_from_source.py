#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
TARGET_STAGE_DIRS = (
    "12_merged_questionType",
    "20_merged_1",
    "20_merged_1_law_only",
    "30_merged_2",
)
TF_LABELS = {"正しい", "間違い"}


@dataclass(frozen=True)
class SourceLabels:
    labels: list[str]
    source_path: Path
    source_question_key: str
    question_label: str
    body: str
    choices: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_choice_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_text(item) for item in value]
    if isinstance(value, str):
        return [normalize_text(value)]
    return []


def choice_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "") for item in value]
    if isinstance(value, str):
        return [value]
    return []


def labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(item or "").strip() for item in value]
    if normalized and all(item in TF_LABELS for item in normalized):
        return normalized
    return []


def source_unique_key(question: dict[str, Any]) -> str | None:
    values = question.get("sourceUniqueKeys")
    if not isinstance(values, list) or not values:
        return None
    normalized = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not normalized:
        return None
    return "\u241f".join(normalized)


def body_choice_key(question: dict[str, Any]) -> str:
    body = normalize_text(question.get("questionBodyText") or question.get("originalQuestionBodyText"))
    choices = normalize_choice_list(question.get("choiceTextList") or question.get("originalQuestionChoiceText"))
    return body + "\u241e" + "\u241f".join(choices)


def is_law_true_false(question: dict[str, Any]) -> bool:
    if question.get("questionType") != "true_false":
        return False
    if question.get("sourceSubject") == "law":
        return True
    if question.get("category") == "法令":
        return True
    if "法令" in str(question.get("examLabel") or ""):
        return True
    if ":law:" in str(question.get("sourceQuestionKey") or ""):
        return True
    return False


def source_labels_from_question(path: Path, question: dict[str, Any]) -> SourceLabels | None:
    if not is_law_true_false(question):
        return None
    current_labels = labels(question.get("correctChoiceText"))
    choices = choice_list(question.get("choiceTextList") or question.get("originalQuestionChoiceText"))
    if not current_labels or len(current_labels) != len(choices):
        return None
    return SourceLabels(
        labels=current_labels,
        source_path=path,
        source_question_key=str(question.get("sourceQuestionKey") or ""),
        question_label=str(question.get("questionLabel") or ""),
        body=str(question.get("questionBodyText") or question.get("originalQuestionBodyText") or ""),
        choices=choices,
    )


def build_source_index(qualification: str, year: str) -> tuple[dict[str, SourceLabels], dict[str, SourceLabels]]:
    source_dir = ROOT_DIR / "output" / qualification / "questions_json" / year / "00_source"
    by_unique: dict[str, SourceLabels] = {}
    by_body_choice: dict[str, SourceLabels] = {}
    if not source_dir.exists():
        return by_unique, by_body_choice
    for path in sorted(source_dir.glob("*.json")):
        payload = load_json(path)
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for question in bodies:
            if not isinstance(question, dict):
                continue
            source = source_labels_from_question(path, question)
            if source is None:
                continue
            unique = source_unique_key(question)
            if unique:
                by_unique.setdefault(unique, source)
            by_body_choice.setdefault(body_choice_key(question), source)
    return by_unique, by_body_choice


def latest_stage_files(stage_dir: Path) -> list[Path]:
    if not stage_dir.exists():
        return []
    candidates = [
        path
        for path in stage_dir.glob("*.json")
        if path.is_file() and not path.name.endswith("_invalid.json")
    ]
    selected: dict[str, Path] = {}
    timestamp_pattern = re.compile(r"_20\d{6}_\d{4}(?:\d{2})?$")
    for path in sorted(candidates):
        canonical = timestamp_pattern.sub("", path.stem)
        selected[canonical] = path
    return sorted(selected.values())


def repair_file(
    path: Path,
    *,
    source_by_unique: dict[str, SourceLabels],
    source_by_body_choice: dict[str, SourceLabels],
    apply: bool,
) -> tuple[int, list[dict[str, Any]]]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        return 0, []
    bodies = payload.get("question_bodies")
    if not isinstance(bodies, list):
        return 0, []

    updates: list[dict[str, Any]] = []
    for index, question in enumerate(bodies):
        if not isinstance(question, dict) or not is_law_true_false(question):
            continue
        unique = source_unique_key(question)
        source = source_by_unique.get(unique) if unique else None
        if source is None:
            source = source_by_body_choice.get(body_choice_key(question))
        if source is None:
            continue
        current = labels(question.get("correctChoiceText"))
        choices = choice_list(question.get("choiceTextList") or question.get("originalQuestionChoiceText"))
        if len(source.labels) != len(choices):
            continue
        if current == source.labels:
            continue
        updates.append(
            {
                "file": rel(path),
                "questionIndex": index,
                "publicQuestionId": question.get("public_question_id") or question.get("publicQuestionId"),
                "originalQuestionId": question.get("original_question_id") or question.get("originalQuestionId"),
                "sourceQuestionKey": question.get("sourceQuestionKey") or source.source_question_key,
                "questionLabel": question.get("questionLabel") or source.question_label,
                "questionBodyText": question.get("questionBodyText") or question.get("originalQuestionBodyText"),
                "oldCorrectChoiceText": question.get("correctChoiceText"),
                "newCorrectChoiceText": source.labels,
                "sourcePath": rel(source.source_path),
                "sourceBodyText": source.body,
            }
        )
        if apply:
            question["correctChoiceText"] = list(source.labels)

    if apply and updates:
        write_json(path, payload)
    return len(updates), updates


def repair_all(*, apply: bool) -> dict[str, Any]:
    all_updates: list[dict[str, Any]] = []
    checked_files = 0
    updated_files = 0
    for qualification in QUALIFICATIONS:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        if not root.exists():
            continue
        for year_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
            source_by_unique, source_by_body_choice = build_source_index(qualification, year_dir.name)
            if not source_by_unique and not source_by_body_choice:
                continue
            for stage_name in TARGET_STAGE_DIRS:
                stage_dir = year_dir / stage_name
                for path in latest_stage_files(stage_dir):
                    checked_files += 1
                    count, updates = repair_file(
                        path,
                        source_by_unique=source_by_unique,
                        source_by_body_choice=source_by_body_choice,
                        apply=apply,
                    )
                    if count:
                        updated_files += 1
                        all_updates.extend(updates)

    report_dir = ROOT_DIR / "output" / "gas-shunin-all" / "review" / "source_correct_choice_repair"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_path = report_dir / f"{timestamp}_gas_shunin_law_correct_choice_source_repair.jsonl"
    summary_path = report_dir / f"{timestamp}_gas_shunin_law_correct_choice_source_repair_summary.json"
    if apply:
        detail_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in all_updates),
            encoding="utf-8",
        )
    summary = {
        "generatedAt": utc_now(),
        "apply": apply,
        "checkedFiles": checked_files,
        "updatedFiles": updated_files,
        "updatedQuestions": len(all_updates),
        "detailPath": rel(detail_path) if apply else None,
        "targetStageDirs": list(TARGET_STAGE_DIRS),
        "qualifications": list(QUALIFICATIONS),
    }
    if apply:
        write_json(summary_path, summary)
        summary["summaryPath"] = rel(summary_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="gas-shunin の法令 true_false correctChoiceText を 00_source の肢別正誤へ戻す"
    )
    parser.add_argument("--apply", action="store_true", help="実際に JSON を更新する")
    args = parser.parse_args(argv)
    summary = repair_all(apply=args.apply)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
