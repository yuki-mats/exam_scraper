from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.question_review_console.question_detail_read_model import (
    build_question_detail_read_model,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="問題詳細の表示用スナップショットを年度単位で集計します。",
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--qualification", required=True)
    parser.add_argument("--list-group-id", required=True)
    args = parser.parse_args()
    snapshot = build_question_detail_read_model(
        args.repo_root,
        args.qualification,
        args.list_group_id,
    )
    json.dump(
        snapshot,
        sys.stdout,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
