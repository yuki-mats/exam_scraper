from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.question_review_console.question_list_read_model import (
    build_question_list_read_model,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="問題一覧の軽量表示用スナップショットを集計します。",
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--qualification", required=True)
    args = parser.parse_args()
    snapshot = build_question_list_read_model(
        args.repo_root,
        args.qualification,
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
