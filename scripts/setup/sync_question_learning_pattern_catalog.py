from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_learning_patterns import DEFAULT_CATALOG_PATH


APP_CATALOG_RELATIVE_PATH = Path("assets/config/question_learning_patterns.json")


def sync_catalog(*, repaso_root: Path, check: bool) -> Path:
    source_bytes = DEFAULT_CATALOG_PATH.read_bytes()
    target = repaso_root.expanduser().resolve() / APP_CATALOG_RELATIVE_PATH
    if check:
        if not target.is_file() or target.read_bytes() != source_bytes:
            raise ValueError(
                f"アプリの問題学習分類が正本と一致しません: {target}"
            )
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_bytes)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(
        description="問題学習分類の正本JSONをRepaso assetへ同期する。"
    )
    parser.add_argument("--repaso-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = sync_catalog(repaso_root=args.repaso_root, check=args.check)
    print(target)


if __name__ == "__main__":
    main()
