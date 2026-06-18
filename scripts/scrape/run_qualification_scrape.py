from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrape.qualification_presets import (
    build_list_first_page_url,
    has_existing_source_json,
    load_scrape_preset,
    resolve_target_list_group_ids,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="資格プリセットに基づいて対象スクレイパーを複数 list_group_id で順番に実行する。"
    )
    parser.add_argument("qualification_code", help="資格コード。例: kaigofukushi")
    parser.add_argument(
        "list_group_ids",
        nargs="*",
        help="対象の list_group_id。未指定時は preset の全件を処理",
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config" / "scrape_presets.json"),
        help="scrape preset JSON のパス",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="code.py 実行に使う Python。既定: このスクリプト自身の実行 Python",
    )
    parser.add_argument(
        "--max-groups",
        type=int,
        default=None,
        help="先頭から処理する list_group_id 数を制限する",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=None,
        help="各 list_group_id で取得する問題数上限。検証用",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="code.py の出力先を一時的に上書きする",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既に 00_source が存在する list_group_id も再取得する",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実行せず、処理予定だけ表示する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resolved_output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (REPO_ROOT / "output")
    )
    preset = load_scrape_preset(
        args.qualification_code,
        config_path=Path(args.config).expanduser().resolve(),
    )

    target_group_ids = resolve_target_list_group_ids(preset, args.list_group_ids)
    if args.max_groups is not None:
        target_group_ids = target_group_ids[: args.max_groups]

    run_targets: list[tuple[str, str]] = []
    for output_list_group_id in target_group_ids:
        already_scraped = has_existing_source_json(
            REPO_ROOT,
            preset.qualification_code,
            output_list_group_id,
            output_root=resolved_output_dir,
        )
        if already_scraped and not args.force:
            print(f"[SKIP] list_group_id={output_list_group_id} は既に 00_source があります")
            continue

        list_url = build_list_first_page_url(preset, output_list_group_id)
        run_targets.append((output_list_group_id, list_url))

    if not run_targets:
        print("[DONE] 実行対象はありません")
        return 0

    print(
        f"[PLAN] qualification={preset.qualification_code} "
        f"groups={', '.join(group_id for group_id, _ in run_targets)}"
    )

    scraper_script_by_type = {
        "kakomonn": "code.py",
        "gassyunin": "scrape_gassyunin.py",
        "sgsiken": "scrape_sgsiken.py",
        "mecnet": "scrape_mecnet_kokushi.py",
        "kurohon": "scrape_kurohon.py",
        "kougai": "scrape_kougai.py",
    }
    scraper_script = scraper_script_by_type.get(preset.scraper_type)
    if not scraper_script:
        raise ValueError(f"未知の scraper_type です: {preset.scraper_type}")

    for index, (list_group_id, list_url) in enumerate(run_targets, start=1):
        print(f"[RUN] ({index}/{len(run_targets)}) list_group_id={list_group_id} url={list_url}")
        if args.dry_run:
            continue

        env = os.environ.copy()
        env["SCRAPER_QUALIFICATION_CODE"] = preset.qualification_code
        env["SCRAPER_QUALIFICATION_NAME"] = preset.qualification_name
        env["SCRAPER_LIST_FIRST_PAGE_URL"] = list_url
        env["SCRAPER_OUTPUT_LIST_GROUP_ID"] = list_group_id

        if args.max_questions is not None:
            env["SCRAPER_MAX_QUESTIONS"] = str(args.max_questions)
        if args.output_dir:
            env["SCRAPER_OUTPUT_DIR"] = args.output_dir

        subprocess.run(
            [args.python_executable, str(REPO_ROOT / scraper_script)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
