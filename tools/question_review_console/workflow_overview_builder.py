from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.qualification_workflow import (
    QualificationWorkflow,
)


def build_workflow_overview(
    repo_root: Path,
    qualification: str,
) -> dict:
    """Build the dashboard read model outside the UI server process."""

    resolved_root = repo_root.resolve()
    inventory = QuestionInventory(resolved_root)
    workflow = QualificationWorkflow(resolved_root, inventory)
    return workflow.overview(qualification)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="問題整備トップの表示用スナップショットを集計します。",
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--qualification", required=True)
    args = parser.parse_args()
    overview = build_workflow_overview(
        args.repo_root,
        args.qualification,
    )
    json.dump(
        overview,
        sys.stdout,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
