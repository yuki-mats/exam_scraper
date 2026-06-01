#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


QUALIFICATION = "gas-shunin-otsu"
PATCH_SUBDIR = "21_explanationText_added"

EXPECTED_NON_VERIFIED_COUNTS: dict[str, int] = {
    "ceeb1f8297cda1cd": 5,
    "2c627ceffe62a06d": 1,
    "be16e16dc9ec58c3": 1,
    "85f05cbb8ce93d82": 1,
    "13088ed044a86680": 5,
    "4e2e4af72d3c96aa": 4,
    "606300689177531c": 5,
    "f1a7b8ecb4f3315f": 7,
}


@dataclass(frozen=True)
class NonVerifiedIssue:
    year: str
    original_question_id: str
    question_url: str
    choice_index: int
    law_alias: str
    article: str | None
    paragraph: str | None
    item: str | None
    verification_status: str
    category: str
    reason: str


def patch_root(repo_root: Path) -> Path:
    return repo_root / "output" / QUALIFICATION / "questions_json"


def latest_patch_files(repo_root: Path) -> list[Path]:
    root = patch_root(repo_root)
    files: list[Path] = []
    for year_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        candidates = sorted((year_dir / PATCH_SUBDIR).glob("question_*_merged_explanationText_added_*.json"))
        if not candidates:
            raise FileNotFoundError(f"patch not found: {year_dir / PATCH_SUBDIR}")
        files.append(candidates[-1])
    return files


def classify_non_verified(reference: dict[str, Any]) -> str:
    reason = str(reference.get("reason") or "")
    if not reference.get("lawId"):
        return "missing_law_id"
    if "設問文脈から" in reason:
        return "context_inference"
    return "other"


def build_issue(
    *,
    year: str,
    entry: dict[str, Any],
    choice_index: int,
    reference: dict[str, Any],
) -> NonVerifiedIssue:
    return NonVerifiedIssue(
        year=year,
        original_question_id=str(entry["original_question_id"]),
        question_url=str(entry["question_url"]),
        choice_index=choice_index,
        law_alias=str(reference.get("lawAlias") or reference.get("lawTitle") or ""),
        article=str(reference.get("article")) if reference.get("article") is not None else None,
        paragraph=str(reference.get("paragraph")) if reference.get("paragraph") is not None else None,
        item=str(reference.get("item")) if reference.get("item") is not None else None,
        verification_status=str(reference.get("verificationStatus") or ""),
        category=classify_non_verified(reference),
        reason=str(reference.get("reason") or ""),
    )


def audit(repo_root: Path) -> dict[str, Any]:
    summary = {
        "patchFiles": [],
        "entryCount": 0,
        "referenceCount": 0,
        "statusCounts": Counter(),
        "categoryCounts": Counter(),
        "missingLawIdAliases": Counter(),
    }
    issues: list[NonVerifiedIssue] = []

    for patch_path in latest_patch_files(repo_root):
        year = patch_path.parent.parent.name
        summary["patchFiles"].append(str(patch_path))
        data = json.loads(patch_path.read_text(encoding="utf-8"))
        for entry in data:
            summary["entryCount"] += 1
            for choice_index, refs in enumerate(entry.get("lawReferences") or []):
                for reference in refs:
                    summary["referenceCount"] += 1
                    status = str(reference.get("verificationStatus") or "unknown")
                    summary["statusCounts"][status] += 1
                    if status != "verified":
                        issue = build_issue(
                            year=year,
                            entry=entry,
                            choice_index=choice_index,
                            reference=reference,
                        )
                        issues.append(issue)
                        summary["categoryCounts"][issue.category] += 1
                        if not reference.get("lawId"):
                            summary["missingLawIdAliases"][issue.law_alias] += 1

    issue_count_by_question = Counter(issue.original_question_id for issue in issues)
    unexpected_question_ids = sorted(
        set(issue_count_by_question) - set(EXPECTED_NON_VERIFIED_COUNTS)
    )
    mismatched_counts = {
        question_id: {
            "expected": EXPECTED_NON_VERIFIED_COUNTS[question_id],
            "actual": issue_count_by_question[question_id],
        }
        for question_id in EXPECTED_NON_VERIFIED_COUNTS
        if issue_count_by_question.get(question_id, 0) != EXPECTED_NON_VERIFIED_COUNTS[question_id]
    }

    return {
        "summary": {
            "patchFiles": summary["patchFiles"],
            "entryCount": summary["entryCount"],
            "referenceCount": summary["referenceCount"],
            "statusCounts": dict(summary["statusCounts"]),
            "categoryCounts": dict(summary["categoryCounts"]),
            "missingLawIdAliases": dict(summary["missingLawIdAliases"]),
            "nonVerifiedQuestionCount": len(issue_count_by_question),
        },
        "expected": {
            "questionIds": EXPECTED_NON_VERIFIED_COUNTS,
            "unexpectedQuestionIds": unexpected_question_ids,
            "mismatchedCounts": mismatched_counts,
        },
        "issues": [issue.__dict__ for issue in issues],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="exam_scraper repo root",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail if non-verified references deviate from expected inventory",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="optional path to write the audit result as json",
    )
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
        unexpected = result["expected"]["unexpectedQuestionIds"]
        mismatched = result["expected"]["mismatchedCounts"]
        if unexpected or mismatched:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
