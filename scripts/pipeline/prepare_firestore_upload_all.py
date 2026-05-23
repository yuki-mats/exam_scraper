#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
import json
from datetime import datetime
import shutil


ROOT_DIR = Path(__file__).resolve().parents[2]


def iter_qualification_dirs(output_dir: Path) -> list[Path]:
    dirs: list[Path] = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_dir():
            continue
        if not (path / "questions_json").is_dir():
            continue
        dirs.append(path)
    return dirs


def iter_list_group_ids(base_dir: Path) -> list[str]:
    group_ids: list[str] = []
    for path in sorted(base_dir.iterdir()):
        if path.is_dir() and path.name.isdigit():
            group_ids.append(path.name)
    return group_ids


def collect_missing_answers(*, base_dir: Path, list_group_id: str) -> list[str]:
    source_dir = base_dir / list_group_id / "00_source"
    if not source_dir.exists():
        return []
    missing: list[str] = []
    for json_path in sorted(source_dir.glob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: S110
            continue
        bodies = payload.get("question_bodies")
        if not isinstance(bodies, list):
            continue
        for idx, body in enumerate(bodies):
            if not isinstance(body, dict):
                continue
            ans = body.get("answer_result_text")
            if ans is None or (isinstance(ans, str) and not ans.strip()):
                q_id = (
                    body.get("public_question_id")
                    or body.get("original_question_id")
                    or f"index_{idx}"
                )
                missing.append(f"{json_path.name} (index {idx}, ID: {q_id})")
    return missing


def collect_invalid_details(*, base_dir: Path, list_group_id: str) -> dict[str, Counter]:
    """
    30_merged_2/*_invalid.json に外出しされた invalid_reasons を集計して返す。
    戻り値: { filename: Counter(reason->count) }
    """
    merged2_dir = base_dir / list_group_id / "30_merged_2"
    if not merged2_dir.exists():
        return {}

    details: dict[str, Counter] = {}
    for invalid_path in sorted(merged2_dir.glob("*_invalid.json")):
        try:
            payload = json.loads(invalid_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: S110
            continue
        bodies = payload.get("question_bodies")
        if not isinstance(bodies, list):
            continue
        counter: Counter = Counter()
        for body in bodies:
            if not isinstance(body, dict):
                continue
            reasons = body.get("invalid_reasons") or []
            if isinstance(reasons, list) and reasons:
                counter.update(str(r) for r in reasons)
            else:
                counter.update(["unknown"])
        if counter:
            details[invalid_path.name] = counter
    return details


def run_prepare_for_qualification(*, python_cmd: str, qualification: str, extra_args: list[str]) -> int:
    cmd = [python_cmd, str(ROOT_DIR / "scripts" / "pipeline" / "prepare_firestore_upload.py"), qualification]
    cmd.extend(extra_args)
    print("\n" + "=" * 80)
    print(f"[QUALIFICATION] {qualification}")
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT_DIR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="全資格を prepare_firestore_upload.py で処理し、最後に対応必要件数/理由を総括表示する",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT_DIR / "output",
        help="output ディレクトリ（デフォルト: ./output）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="prepare_firestore_upload.py を --dry-run で実行する",
    )
    parser.add_argument(
        "--requirements-warn-only",
        action="store_true",
        help="requirements違反があっても停止せず警告のみ出す",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="総括レポート出力先ディレクトリ（省略時: output/reports/prepare_firestore_upload_all/<timestamp>/）",
    )
    args = parser.parse_args(argv)

    python_cmd = sys.executable
    output_dir = args.output_dir.resolve()

    extra_args: list[str] = []
    if args.dry_run:
        extra_args.append("--dry-run")
    if args.requirements_warn_only:
        extra_args.append("--requirements-warn-only")

    qualification_dirs = iter_qualification_dirs(output_dir)
    if not qualification_dirs:
        print(f"[ERROR] qualification dirs not found under: {output_dir}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for qdir in qualification_dirs:
        qualification = qdir.name
        rc = run_prepare_for_qualification(
            python_cmd=python_cmd,
            qualification=qualification,
            extra_args=extra_args,
        )
        if rc != 0:
            failures.append(qualification)

    # --- 総括集計（ファイル名+理由） ---
    print("\n" + "=" * 80)
    print("=== 総括: アップロード不能レコード数（対応必要。0であるべき） ===")

    grand_missing = 0
    grand_invalid = 0
    per_qual_summary: dict[str, dict[str, object]] = {}

    for qdir in qualification_dirs:
        qualification = qdir.name
        base_dir = qdir / "questions_json"
        list_group_ids = iter_list_group_ids(base_dir)
        qual_missing: dict[str, list[str]] = {}
        qual_invalid: dict[str, dict[str, Counter]] = {}

        missing_count = 0
        invalid_count = 0

        for gid in list_group_ids:
            missing = collect_missing_answers(base_dir=base_dir, list_group_id=gid)
            if missing:
                qual_missing[gid] = missing
                missing_count += len(missing)
            invalid = collect_invalid_details(base_dir=base_dir, list_group_id=gid)
            if invalid:
                qual_invalid[gid] = invalid
                invalid_count += sum(sum(c.values()) for c in invalid.values())

        per_qual_summary[qualification] = {
            "missing_count": missing_count,
            "invalid_count": invalid_count,
            "missing": qual_missing,
            "invalid": qual_invalid,
        }
        grand_missing += missing_count
        grand_invalid += invalid_count

    grand_total = grand_missing + grand_invalid
    print(f"合計: {grand_total}")
    print(f"  - 00_source answer_result_text 欠損: {grand_missing}")
    print(f"  - 30_merged_2/*_invalid.json 外出し: {grand_invalid}")

    # --- upload_to_firestore のファイル一覧も含めてレポート出力 ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # レポートの出力先:
    # - --report-dir 未指定: 実行時刻ディレクトリを作ってそこへ出力（既存と衝突しない）
    # - --report-dir 指定: そのディレクトリ直下に「時刻付きファイル名」で出力し、
    #   既存ファイルは old/<timestamp>/ へ退避する
    if args.report_dir:
        report_dir = args.report_dir.expanduser().resolve()
        report_dir.mkdir(parents=True, exist_ok=True)

        existing = [p for p in report_dir.iterdir() if p.name != "old"]
        if existing:
            archive_dir = report_dir / "old" / timestamp
            archive_dir.mkdir(parents=True, exist_ok=False)
            for path in existing:
                shutil.move(str(path), str(archive_dir / path.name))
    else:
        report_dir = (output_dir / "reports" / "prepare_firestore_upload_all" / timestamp).resolve()
        report_dir.mkdir(parents=True, exist_ok=True)

    report_payload: dict[str, object] = {
        "generated_at": timestamp,
        "output_dir": str(output_dir),
        "dry_run": bool(args.dry_run),
        "requirements_warn_only": bool(args.requirements_warn_only),
        "failures": failures,
        "unuploadable_total": grand_total,
        "unuploadable_missing_answers_total": grand_missing,
        "unuploadable_invalid_total": grand_invalid,
        "qualifications": {},
    }

    qualifications_out: dict[str, object] = {}
    for qdir in qualification_dirs:
        qualification = qdir.name
        base_dir = qdir / "questions_json"
        upload_dir = base_dir / "upload_to_firestore"
        upload_files = (
            sorted(upload_dir.glob("*_firestore_*.json"))
            if upload_dir.exists()
            else []
        )
        info = per_qual_summary.get(qualification, {})
        qual_payload = {
            "qualification": qualification,
            "questions_json_dir": str(base_dir),
            "upload_to_firestore_dir": str(upload_dir),
            "upload_to_firestore_file_count": len(upload_files),
            "upload_to_firestore_files": [p.name for p in upload_files],
            "unuploadable_missing_count": int(info.get("missing_count", 0) or 0),
            "unuploadable_invalid_count": int(info.get("invalid_count", 0) or 0),
            "missing": info.get("missing", {}),
            "invalid": {
                gid: {
                    filename: dict(counter)
                    for filename, counter in file_map.items()
                }
                for gid, file_map in (info.get("invalid", {}) or {}).items()
            },
        }
        qualifications_out[qualification] = qual_payload
        (report_dir / f"{qualification}_{timestamp}.json").write_text(
            json.dumps(qual_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    report_payload["qualifications"] = qualifications_out
    (report_dir / f"summary_{timestamp}.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 人間が見やすいテキストも出す
    lines: list[str] = []
    lines.append(f"generated_at: {timestamp}")
    lines.append(f"output_dir: {output_dir}")
    lines.append(f"dry_run: {args.dry_run}")
    lines.append(f"requirements_warn_only: {args.requirements_warn_only}")
    lines.append(f"failures: {', '.join(failures) if failures else '(none)'}")
    lines.append("")
    lines.append("=== unuploadable ===")
    lines.append(f"total: {grand_total}")
    lines.append(f"  - missing answer_result_text: {grand_missing}")
    lines.append(f"  - invalid externalized: {grand_invalid}")
    lines.append("")
    lines.append("=== upload_to_firestore files ===")
    for qdir in qualification_dirs:
        qualification = qdir.name
        upload_dir = qdir / "questions_json" / "upload_to_firestore"
        upload_files = sorted(upload_dir.glob("*_firestore_*.json")) if upload_dir.exists() else []
        lines.append(f"- {qualification}: {len(upload_files)} files")
        for p in upload_files:
            lines.append(f"  * {p.name}")
    lines.append("")
    lines.append("=== unuploadable details ===")
    for qualification, info in per_qual_summary.items():
        missing_count = int(info["missing_count"])  # type: ignore[arg-type]
        invalid_count = int(info["invalid_count"])  # type: ignore[arg-type]
        if missing_count == 0 and invalid_count == 0:
            continue
        lines.append(f"[{qualification}] missing={missing_count} invalid={invalid_count}")
        missing: dict[str, list[str]] = info["missing"]  # type: ignore[assignment]
        for gid, items in sorted(missing.items()):
            lines.append(f"  - {gid} missing {len(items)}")
            for line in items[:20]:
                lines.append(f"    * {line}")
            if len(items) > 20:
                lines.append(f"    ... truncated ({len(items) - 20} more)")
        invalid: dict[str, dict[str, Counter]] = info["invalid"]  # type: ignore[assignment]
        for gid, file_map in sorted(invalid.items()):
            total = sum(sum(c.values()) for c in file_map.values())
            lines.append(f"  - {gid} invalid {total}")
            for filename, counter in sorted(file_map.items()):
                reason_str = ", ".join(f"{k}:{v}" for k, v in counter.most_common())
                lines.append(f"    * {filename}: {reason_str}")
        lines.append("")
    (report_dir / f"summary_{timestamp}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[REPORT] wrote: {report_dir}")

    for qualification, info in per_qual_summary.items():
        missing_count = int(info["missing_count"])  # type: ignore[arg-type]
        invalid_count = int(info["invalid_count"])  # type: ignore[arg-type]
        if missing_count == 0 and invalid_count == 0:
            continue
        print("\n" + "-" * 80)
        print(f"[{qualification}] missing={missing_count} invalid={invalid_count}")

        missing: dict[str, list[str]] = info["missing"]  # type: ignore[assignment]
        for gid, items in sorted(missing.items()):
            print(f"  - {gid} (00_source欠損 {len(items)}件)")
            for line in items[:20]:
                print(f"    * {line}")
            if len(items) > 20:
                print(f"    ... truncated ({len(items) - 20} more)")

        invalid: dict[str, dict[str, Counter]] = info["invalid"]  # type: ignore[assignment]
        for gid, file_map in sorted(invalid.items()):
            total = sum(sum(c.values()) for c in file_map.values())
            print(f"  - {gid} (invalid外出し {total}件)")
            for filename, counter in sorted(file_map.items()):
                reason_str = ", ".join(f"{k}:{v}" for k, v in counter.most_common())
                print(f"    * {filename}: {reason_str}")

    if failures:
        print("\n[WARN] prepare_firestore_upload.py が失敗した資格があります:")
        for qual in failures:
            print(f"  - {qual}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
