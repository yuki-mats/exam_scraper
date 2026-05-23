#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def load_secure_env_if_present() -> None:
    """
    ~/.config/exam_scraper/secure.env があれば読み込み、環境変数へ反映する。
    """
    secure_env = Path.home() / ".config" / "exam_scraper" / "secure.env"
    if not secure_env.exists():
        return
    for line in secure_env.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


def iter_qualification_dirs(output_dir: Path) -> list[Path]:
    dirs: list[Path] = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_dir():
            continue
        if not (path / "questions_json").is_dir():
            continue
        if not (path / "category" / "category.json").is_file():
            continue
        dirs.append(path)
    return dirs


def latest_upload_files_for_questions_json(questions_json_dir: Path) -> list[Path]:
    """
    questions_json/upload_to_firestore から list_group_id ごとに最新 *_firestore_*.json を集める。
    """
    upload_dir = questions_json_dir / "upload_to_firestore"
    if not upload_dir.is_dir():
        raise FileNotFoundError(f"upload_to_firestore not found: {upload_dir}")

    group_ids = [p.name for p in sorted(questions_json_dir.iterdir()) if p.is_dir() and p.name.isdigit()]
    if not group_ids:
        raise FileNotFoundError(f"list_group_id dirs not found: {questions_json_dir}")

    files: list[Path] = []
    missing: list[str] = []
    for gid in group_ids:
        candidates = sorted(upload_dir.glob(f"{gid}_firestore_*.json"))
        if candidates:
            files.append(candidates[-1])
            continue
        legacy = upload_dir / f"{gid}_firestore.json"
        if legacy.exists():
            files.append(legacy)
            continue
        missing.append(gid)

    if missing:
        raise FileNotFoundError(
            f"upload_to_firestore の最新ファイルが見つからない list_group_id があります: {', '.join(missing)}"
        )
    return files


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT_DIR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="全資格の questions / questionSets / folders を最新化して Firestore へアップロードする",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT_DIR / "output",
        help="output ディレクトリ（デフォルト: ./output）",
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス（未指定時は GOOGLE_APPLICATION_CREDENTIALS を使用）",
    )
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help="事前のローカル生成物最新化（prepare_firestore_upload_all.py）をスキップする",
    )
    parser.add_argument(
        "--prepare-report-dir",
        type=Path,
        default=ROOT_DIR / "output" / "reports" / "prepare_summary",
        help="prepare の総括レポート出力先（デフォルト: output/reports/prepare_summary）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="questions のアップロードを dry-run で実行する（category は dry-run のまま）",
    )
    args = parser.parse_args(argv)

    load_secure_env_if_present()

    output_dir = args.output_dir.expanduser().resolve()
    qualification_dirs = iter_qualification_dirs(output_dir)
    if not qualification_dirs:
        print(f"[ERROR] qualifications not found under: {output_dir}", file=sys.stderr)
        return 2

    python_cmd = sys.executable
    credentials_args: list[str] = []
    if args.credentials_json is not None:
        credentials_args = ["--credentials-json", str(args.credentials_json)]

    # 0) まずローカル生成物を最新化（category.json の件数更新も含む）
    if not args.skip_prepare:
        prepare_cmd = [
            python_cmd,
            str(ROOT_DIR / "scripts" / "pipeline" / "prepare_firestore_upload_all.py"),
            "--report-dir",
            str(args.prepare_report_dir.expanduser().resolve()),
        ]
        run(prepare_cmd)

    for qdir in qualification_dirs:
        qualification = qdir.name
        questions_json_dir = qdir / "questions_json"
        category_json = qdir / "category" / "category.json"

        print("\n" + "=" * 80)
        print(f"[QUALIFICATION] {qualification}")

        # 1) questions: list_group_id ごとの最新 upload_to_firestore をアップロード
        upload_files = latest_upload_files_for_questions_json(questions_json_dir)
        for path in upload_files:
            cmd = [python_cmd, str(ROOT_DIR / "scripts" / "upload" / "upload_questions_to_firestore.py"), str(path)]
            if args.dry_run:
                cmd.append("--dry-run")
            cmd.extend(credentials_args)
            run(cmd)

        # 2) category: upload_to_firestore の実態から questionCount を集計して反映し、Firestoreへアップロード
        # updatedAt は差分がある場合のみ更新される（upload_category_to_firestore.py 側の実装）
        cat_cmd = [
            python_cmd,
            str(ROOT_DIR / "scripts" / "upload" / "upload_category_to_firestore.py"),
            str(category_json),
            "--all-list-groups",
            "--questions-json-dir",
            str(questions_json_dir),
        ]
        cat_cmd.extend(credentials_args)
        if not args.dry_run:
            cat_cmd.append("--upload")
        run(cat_cmd)

    print("\n[OK] all done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:  # pragma: no cover
        raise SystemExit(0)
