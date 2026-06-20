#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
VARIANT_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_source_paths(qualifications: list[str]) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for qualification in qualifications:
        root = ROOT_DIR / "output" / qualification / "questions_json"
        paths.extend(
            (qualification, path)
            for path in sorted(root.glob("*/00_source/question_*.json"))
            if "99_archived" not in path.parts
        )
    return paths


def stable_variant_key(question: dict[str, Any]) -> str:
    firestore_ids = question.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        for value in firestore_ids:
            text = str(value or "").strip()
            if text:
                return VARIANT_SAFE_RE.sub("_", text)
    raise ValueError("conflict row has no firestoreQuestionIds")


def natural_unique_keys(question: dict[str, Any]) -> list[str]:
    existing = question.get("sourceNaturalUniqueKeys")
    if isinstance(existing, list) and all(isinstance(item, str) for item in existing):
        return existing
    keys = question.get("sourceUniqueKeys")
    if isinstance(keys, list) and all(isinstance(item, str) for item in keys):
        return keys
    return []


def apply_conflict_variant(question: dict[str, Any], duplicate_keys: set[str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    base_unique_keys = natural_unique_keys(question)
    intersecting = sorted(set(base_unique_keys) & duplicate_keys)
    if not intersecting:
        return question, None

    variant_key = stable_variant_key(question)
    resolved_keys = [f"{key}:legacy:{variant_key}" for key in base_unique_keys]
    source_question_key = str(question.get("sourceQuestionKey") or "")
    conflict = {
        "reason": "duplicate_natural_source_key_from_firestore_snapshot",
        "variantKey": variant_key,
        "variantSource": "firestoreQuestionIds[0]",
        "naturalSourceQuestionKey": source_question_key,
        "naturalSourceUniqueKeys": base_unique_keys,
        "duplicateNaturalSourceUniqueKeys": intersecting,
        "resolvedSourceUniqueKeys": resolved_keys,
        "resolvedAt": utc_now(),
    }

    repaired = dict(question)
    repaired["sourceNaturalQuestionKey"] = source_question_key
    repaired["sourceNaturalUniqueKeys"] = base_unique_keys
    repaired["sourceConflictVariantKey"] = variant_key
    repaired["sourceKeyConflict"] = conflict
    repaired["sourceUniqueKeys"] = resolved_keys

    statuses = repaired.get("statementSourceStatuses")
    if isinstance(statuses, list):
        new_statuses: list[Any] = []
        for index, status in enumerate(statuses):
            if isinstance(status, dict) and index < len(resolved_keys):
                new_status = dict(status)
                new_status["sourceUniqueKey"] = resolved_keys[index]
                new_status["sourceNaturalUniqueKey"] = base_unique_keys[index]
                new_statuses.append(new_status)
            else:
                new_statuses.append(status)
        repaired["statementSourceStatuses"] = new_statuses

    return repaired, {
        "variantKey": variant_key,
        "sourceQuestionKey": source_question_key,
        "duplicateNaturalSourceUniqueKeys": intersecting,
        "resolvedSourceUniqueKeys": resolved_keys,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    loaded_files: list[tuple[str, Path, dict[str, Any]]] = []
    key_counter: Counter[str] = Counter()
    key_locations: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for qualification, path in iter_source_paths(args.qualifications):
        payload = load_json(path)
        loaded_files.append((qualification, path, payload))
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        for index, question in enumerate(bodies):
            if not isinstance(question, dict):
                continue
            for key in natural_unique_keys(question):
                key_counter[key] += 1
                key_locations[key].append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        "firestoreQuestionIds": question.get("firestoreQuestionIds"),
                    }
                )

    duplicate_keys = {key for key, count in key_counter.items() if count > 1}
    changed_files: list[str] = []
    repaired_questions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for qualification, path, payload in loaded_files:
        bodies = payload.get("question_bodies") if isinstance(payload, dict) else None
        if not isinstance(bodies, list):
            continue
        new_bodies: list[Any] = []
        file_changed = False
        for index, question in enumerate(bodies):
            if not isinstance(question, dict):
                new_bodies.append(question)
                continue
            try:
                repaired, record = apply_conflict_variant(question, duplicate_keys)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        "error": str(exc),
                    }
                )
                new_bodies.append(question)
                continue
            new_bodies.append(repaired)
            if record is not None:
                file_changed = True
                repaired_questions.append(
                    {
                        "qualification": qualification,
                        "sourceFile": rel(path),
                        "questionIndex": index,
                        "questionLabel": question.get("questionLabel"),
                        **record,
                    }
                )
        if file_changed:
            changed_files.append(rel(path))
            if args.write:
                payload["question_bodies"] = new_bodies
                save_json(path, payload)

    return {
        "schemaVersion": "gas-shunin-source-key-conflict-resolution/v1",
        "generatedAt": utc_now(),
        "writeApplied": bool(args.write),
        "qualifications": args.qualifications,
        "duplicateNaturalSourceUniqueKeyCount": len(duplicate_keys),
        "duplicateNaturalSourceUniqueKeys": sorted(duplicate_keys),
        "duplicateLocations": {key: key_locations[key] for key in sorted(duplicate_keys)},
        "changedFileCount": len(changed_files),
        "changedFiles": changed_files,
        "repairedQuestionCount": len(repaired_questions),
        "repairedQuestions": repaired_questions,
        "errorCount": len(errors),
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve duplicate gas-shunin natural source keys with stable Firestore variants")
    parser.add_argument(
        "--qualifications",
        nargs="+",
        default=list(QUALIFICATIONS),
        choices=QUALIFICATIONS,
    )
    parser.add_argument("--write", action="store_true", help="write repaired 00_source JSON files")
    parser.add_argument("--report", type=Path, help="write a JSON report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run(args)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 1 if report["errorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
