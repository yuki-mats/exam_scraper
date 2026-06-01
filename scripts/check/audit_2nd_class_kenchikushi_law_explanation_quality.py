#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


QUALIFICATION = "2nd-class-kenchikushi"
PATCH_SUBDIR = "21_explanationText_added"
PATCH_GLOB = "question_*_law_merged_explanationText_added_*.json"
EXPECTED_ENTRY_COUNT = 256
EXPECTED_CANDIDATE_ALIAS_COUNTS = {
    "規則": 24,
    "宅地造成等規制法": 6,
    "施行規則": 4,
}


def latest_patch_files(repo_root: Path) -> list[Path]:
    root = repo_root / "output" / QUALIFICATION / "questions_json"
    paths: list[Path] = []
    for list_group_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        candidates = sorted((list_group_dir / PATCH_SUBDIR).glob(PATCH_GLOB))
        if not candidates:
            continue
        latest = candidates[-1]
        entries = json.loads(latest.read_text(encoding="utf-8"))
        if entries:
            paths.append(latest)
    return paths


def audit(repo_root: Path) -> dict[str, Any]:
    summary = {
        "patchFiles": [],
        "entryCount": 0,
        "withExplanation": 0,
        "withSuggestedQuestions": 0,
        "withSuggestedQuestionDetails": 0,
        "withLawReferences": 0,
        "referenceCount": 0,
        "statusCounts": Counter(),
        "candidateAliasCounts": Counter(),
    }
    missing: list[dict[str, str]] = []

    for patch_path in latest_patch_files(repo_root):
        summary["patchFiles"].append(str(patch_path))
        entries = json.loads(patch_path.read_text(encoding="utf-8"))
        for entry in entries:
            summary["entryCount"] += 1
            if entry.get("explanationText"):
                summary["withExplanation"] += 1
            else:
                missing.append(
                    {
                        "type": "explanationText",
                        "original_question_id": str(entry.get("original_question_id") or ""),
                        "question_url": str(entry.get("question_url") or ""),
                    }
                )
            if entry.get("suggestedQuestions"):
                summary["withSuggestedQuestions"] += 1
            else:
                missing.append(
                    {
                        "type": "suggestedQuestions",
                        "original_question_id": str(entry.get("original_question_id") or ""),
                        "question_url": str(entry.get("question_url") or ""),
                    }
                )
            if entry.get("suggestedQuestionDetails"):
                summary["withSuggestedQuestionDetails"] += 1
            else:
                missing.append(
                    {
                        "type": "suggestedQuestionDetails",
                        "original_question_id": str(entry.get("original_question_id") or ""),
                        "question_url": str(entry.get("question_url") or ""),
                    }
                )
            if entry.get("lawReferences"):
                summary["withLawReferences"] += 1
            else:
                missing.append(
                    {
                        "type": "lawReferences",
                        "original_question_id": str(entry.get("original_question_id") or ""),
                        "question_url": str(entry.get("question_url") or ""),
                    }
                )
            for refs in entry.get("lawReferences") or []:
                for ref in refs:
                    summary["referenceCount"] += 1
                    status = str(ref.get("verificationStatus") or "unknown")
                    summary["statusCounts"][status] += 1
                    if status != "verified":
                        summary["candidateAliasCounts"][str(ref.get("lawAlias") or ref.get("lawTitle") or "")] += 1

    return {
        "summary": {
            "patchFiles": summary["patchFiles"],
            "entryCount": summary["entryCount"],
            "withExplanation": summary["withExplanation"],
            "withSuggestedQuestions": summary["withSuggestedQuestions"],
            "withSuggestedQuestionDetails": summary["withSuggestedQuestionDetails"],
            "withLawReferences": summary["withLawReferences"],
            "referenceCount": summary["referenceCount"],
            "statusCounts": dict(summary["statusCounts"]),
            "candidateAliasCounts": dict(summary["candidateAliasCounts"]),
        },
        "expected": {
            "entryCount": EXPECTED_ENTRY_COUNT,
            "candidateAliasCounts": EXPECTED_CANDIDATE_ALIAS_COUNTS,
        },
        "missing": missing,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit(args.repo_root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")

    if args.strict:
        summary = result["summary"]
        expected = result["expected"]
        if result["missing"]:
            return 1
        if summary["entryCount"] != expected["entryCount"]:
            return 1
        if summary["withExplanation"] != expected["entryCount"]:
            return 1
        if summary["withSuggestedQuestions"] != expected["entryCount"]:
            return 1
        if summary["withSuggestedQuestionDetails"] != expected["entryCount"]:
            return 1
        if summary["withLawReferences"] != expected["entryCount"]:
            return 1
        if summary["candidateAliasCounts"] != expected["candidateAliasCounts"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
