#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE_DIR = ROOT_DIR / "output" / "pdf" / "gas-shunin-official"
DEFAULT_CATALOG_PATH = (
    ROOT_DIR
    / "document"
    / "sources"
    / "gas-shunin"
    / "official_exam_pdf_catalog.json"
)
OFFICIAL_INDEX_URL = "https://www.jia-page.or.jp/exam/examination/answer/"
OFFICIAL_PDF_BASE_URL = "https://www.jia-page.or.jp/files/user/doc/exam/"
SCHEMA_VERSION = "gas-shunin-official-pdf-catalog/v1"
USER_AGENT = "exam_scraper official-exam-archive/1.0"


@dataclass(frozen=True)
class YearSpec:
    year: int
    era: str
    filename_suffix: str


YEAR_SPECS = (
    YearSpec(2017, "平成29年度", "h29"),
    YearSpec(2018, "平成30年度", "h30"),
    YearSpec(2019, "令和元年度", "r1"),
    YearSpec(2020, "令和2年度", "r2"),
    YearSpec(2021, "令和3年度", "r3"),
    YearSpec(2022, "令和4年度", "r4"),
    YearSpec(2023, "令和5年度", "r5"),
    YearSpec(2024, "令和6年度", "r6"),
    YearSpec(2025, "令和7年度", "R7"),
)


# Internet Archiveで確認済みのJIA公式PDF capture。
# 現行JIAページから外れた後も、元のJIA URLと内容を固定して再取得する。
ARCHIVE_TIMESTAMPS = {
    "q_kou_h29.pdf": "20230525041825",
    "q_kou_h30.pdf": "20200923053125",
    "q_kou_r1.pdf": "20200923073323",
    "q_kou_r2.pdf": "20230710075330",
    "q_kou_r3.pdf": "20230710075250",
    "q_kou_r4.pdf": "20230710080436",
    "q_kou_r5.pdf": "20240530162159",
    "q_kou_r6.pdf": "20251105230841",
    "q_kou_R7.pdf": "20251105221139",
    "a_kou_h29.pdf": "20230525042457",
    "a_kou_h30.pdf": "20200923062857",
    "a_kou_r1.pdf": "20200923055726",
    "a_kou_r2.pdf": "20230710075934",
    "a_kou_r3.pdf": "20230710080056",
    "a_kou_r4.pdf": "20240530165108",
    "a_kou_r5.pdf": "20240530150630",
    "a_kou_r6.pdf": "20251105220602",
    "a_kou_R7.pdf": "20251105225253",
    "q_otsu_h29.pdf": "20230809103935",
    "q_otsu_h30.pdf": "20200923062546",
    "q_otsu_r1.pdf": "20200929003347",
    "q_otsu_r2.pdf": "20230809110023",
    "q_otsu_r3.pdf": "20230809110647",
    "q_otsu_r4.pdf": "20230809111544",
    "q_otsu_r5.pdf": "20240530154228",
    "q_otsu_r6.pdf": "20251105215053",
    "q_otsu_R7.pdf": "20251105222423",
    "a_otsu_h29.pdf": "20230809104046",
    "a_otsu_h30.pdf": "20200923072641",
    "a_otsu_r1.pdf": "20200923065046",
    "a_otsu_r2.pdf": "20230809105932",
    "a_otsu_r3.pdf": "20230809111112",
    "a_otsu_r4.pdf": "20230809111149",
    "a_otsu_r5.pdf": "20240530163746",
    "a_otsu_r6.pdf": "20251105234644",
    "a_otsu_R7.pdf": "20251105224005",
    "q_hei_h29.pdf": "20230809142601",
    "q_hei_h30.pdf": "20200923062741",
    "q_hei_r1.pdf": "20200929003727",
    "q_hei_r2.pdf": "20230809143012",
    "q_hei_r3.pdf": "20240701031435",
    "q_hei_r4.pdf": "20240530164950",
    "q_hei_r5.pdf": "20240530150339",
    "q_hei_r6.pdf": "20251105215719",
    "q_hei_R7.pdf": "20251105223012",
    "q_hei_h29-2.pdf": "20230809143633",
    "q_hei_h30-2.pdf": "20200923051925",
    "q_hei_r1-2.pdf": "20200923055836",
    "q_hei_r2-2.pdf": "20230809143823",
    "q_hei_r3-2.pdf": "20230809143844",
    "q_hei_r4-2.pdf": "20230809143923",
    "q_hei_r5-2.pdf": "20240530150926",
    "q_hei_r6-2.pdf": "20251105215454",
    "a_hei_h29.pdf": "20230809142729",
    "a_hei_h30.pdf": "20200923072841",
    "a_hei_r1.pdf": "20200923065734",
    "a_hei_r2.pdf": "20230809142943",
    "a_hei_r3.pdf": "20240630020012",
    "a_hei_r4.pdf": "20240530153013",
    "a_hei_r5.pdf": "20240530154951",
    "a_hei_r6.pdf": "20251105221112",
    "a_hei_R7.pdf": "20251105220203",
    "q_kouotsu_h29-2.pdf": "20230525042307",
    "q_kouotsu_h30-2.pdf": "20200923070630",
    "q_kouotsu_r1-2.pdf": "20200929001553",
    "q_kouotsu_r2-2.pdf": "20230710075622",
    "q_kouotsu_r3-2.pdf": "20240630024211",
    "q_kouotsu_r4-2.pdf": "20230710080412",
    "q_kouotsu_r5-2.pdf": "20240610234607",
    "q_kouotsu_r6-2.pdf": "20251105233417",
    "q_kouotsu_R7-2.pdf": "20251105220410",
}


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def archive_url(
    filename: str,
    timestamp: str,
    *,
    original_scheme: str = "https",
) -> str:
    original = (
        f"{original_scheme}://www.jia-page.or.jp/files/user/doc/exam/"
        f"{filename}"
    )
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def _file_specs(year: YearSpec) -> list[dict[str, Any]]:
    suffix = year.filename_suffix
    rows = (
        (f"q_kou_{suffix}.pdf", ["kou"], "mark_sheet_question"),
        (f"q_otsu_{suffix}.pdf", ["otsu"], "mark_sheet_question"),
        (f"q_hei_{suffix}.pdf", ["hei"], "mark_sheet_question"),
        (f"q_kouotsu_{suffix}-2.pdf", ["kou", "otsu"], "essay_question"),
        (f"q_hei_{suffix}-2.pdf", ["hei"], "essay_question"),
        (f"a_kou_{suffix}.pdf", ["kou"], "answer"),
        (f"a_otsu_{suffix}.pdf", ["otsu"], "answer"),
        (f"a_hei_{suffix}.pdf", ["hei"], "answer"),
    )
    specs: list[dict[str, Any]] = []
    for filename, grades, document_type in rows:
        original_url = f"{OFFICIAL_PDF_BASE_URL}{filename}"
        download_urls = [original_url]
        timestamp = ARCHIVE_TIMESTAMPS.get(filename)
        if timestamp:
            archived_urls = [
                archive_url(filename, timestamp, original_scheme=scheme)
                for scheme in ("https", "http")
            ]
            # 現行2年度はJIAを先に、過年度は固定済み保存版を先に読む。
            download_urls = (
                [original_url, *archived_urls]
                if year.year >= 2024
                else [*archived_urls, original_url]
            )
        specs.append(
            {
                "year": year.year,
                "era": year.era,
                "grades": grades,
                "documentType": document_type,
                "filename": filename,
                "localPath": (
                    f"output/pdf/gas-shunin-official/{year.year}/{filename}"
                ),
                "originalUrl": original_url,
                "downloadUrls": download_urls,
                "archiveTimestamp": timestamp,
            }
        )
    return specs


def build_source_specs() -> list[dict[str, Any]]:
    return [
        spec
        for year in YEAR_SPECS
        for spec in _file_specs(year)
    ]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"PDFがありません: {path}")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"PDF headerが不正です: {path}")
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError(f"暗号化PDFは保管できません: {path}")
        page_count = len(reader.pages)
    except PdfReadError as exc:
        raise ValueError(f"PDF構造が不完全です: {path}: {exc}") from exc
    if page_count < 1:
        raise ValueError(f"ページがありません: {path}")
    return {
        "sha256": sha256_path(path),
        "bytes": path.stat().st_size,
        "pages": page_count,
    }


def _download_to(
    url: str,
    destination: Path,
    *,
    timeout_seconds: int,
) -> str:
    part = destination.with_suffix(destination.suffix + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.unlink(missing_ok=True)
    try:
        curl = shutil.which("curl")
        if curl:
            resolved_url = url
            transient_error_count = 0
            for segment_attempt in range(1, 129):
                previous_size = part.stat().st_size if part.exists() else 0
                completed = subprocess.run(
                    [
                        curl,
                        "--fail",
                        "--location",
                        "--http1.1",
                        "--connect-timeout",
                        "20",
                        "--max-time",
                        str(timeout_seconds),
                        "--retry",
                        "2",
                        "--retry-all-errors",
                        "--user-agent",
                        USER_AGENT,
                        "--header",
                        "Accept-Encoding: identity",
                        "--continue-at",
                        "-",
                        "--output",
                        str(part),
                        "--write-out",
                        "%{url_effective}",
                        url,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    transient_error_count += 1
                    if (
                        completed.returncode in {7, 18, 28, 33, 56, 92}
                        and transient_error_count <= 5
                    ):
                        time.sleep(min(2 ** (transient_error_count - 1), 8))
                        continue
                    raise OSError(
                        f"curl exit={completed.returncode}: "
                        f"{completed.stderr.strip()}"
                    )
                transient_error_count = 0
                resolved_url = completed.stdout.strip() or resolved_url
                current_size = part.stat().st_size
                if current_size <= previous_size:
                    raise OSError(
                        "分割取得が進みません: "
                        f"{url} bytes={current_size}"
                    )
                with part.open("rb") as stream:
                    stream.seek(max(0, current_size - 2048))
                    if b"%%EOF" not in stream.read():
                        continue
                inspect_pdf(part)
                part.replace(destination)
                return resolved_url
            raise OSError(
                f"PDF分割取得の上限を超えました: {url} "
                f"bytes={part.stat().st_size}"
            )
        else:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                },
            )
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                resolved_url = response.geturl()
                with part.open("wb") as stream:
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
        inspect_pdf(part)
        part.replace(destination)
        return resolved_url
    finally:
        part.unlink(missing_ok=True)


def download_pdf(
    entry: dict[str, Any],
    archive_dir: Path,
    *,
    timeout_seconds: int,
    retry_count: int,
    expected_sha256: str | None,
) -> dict[str, Any]:
    destination = archive_dir / str(entry["year"]) / str(entry["filename"])
    if destination.is_file():
        metadata = inspect_pdf(destination)
        if not expected_sha256 or metadata["sha256"] == expected_sha256:
            return {
                **entry,
                **metadata,
                "resolvedUrl": str(
                    entry.get("resolvedUrl")
                    or (entry.get("downloadUrls") or [entry["originalUrl"]])[0]
                ),
                "downloadStatus": "existing",
            }

    errors: list[str] = []
    urls = list(
        dict.fromkeys(
            [
                str(entry.get("resolvedUrl") or ""),
                *[str(value) for value in entry.get("downloadUrls") or []],
                str(entry["originalUrl"]),
            ]
        )
    )
    urls = [url for url in urls if url]
    for url in urls:
        for attempt in range(1, retry_count + 1):
            try:
                resolved_url = _download_to(
                    url,
                    destination,
                    timeout_seconds=timeout_seconds,
                )
                metadata = inspect_pdf(destination)
                if expected_sha256 and metadata["sha256"] != expected_sha256:
                    destination.unlink(missing_ok=True)
                    raise ValueError(
                        "SHA-256がcatalogと一致しません: "
                        f"{entry['filename']} expected={expected_sha256} "
                        f"actual={metadata['sha256']}"
                    )
                return {
                    **entry,
                    **metadata,
                    "resolvedUrl": resolved_url,
                    "downloadStatus": "downloaded",
                }
            except (
                OSError,
                ValueError,
                urllib.error.HTTPError,
                urllib.error.URLError,
            ) as exc:
                errors.append(f"{url} attempt={attempt}: {exc}")
                destination.unlink(missing_ok=True)
                if attempt < retry_count:
                    time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(
        f"{entry['filename']}を取得できませんでした。\n" + "\n".join(errors)
    )


def _catalog_payload(files: list[dict[str, Any]]) -> dict[str, Any]:
    catalog_files = [
        {
            key: value
            for key, value in entry.items()
            if key not in {"downloadStatus", "resolvedUrl"}
        }
        for entry in files
    ]
    by_year: dict[str, dict[str, int]] = {}
    for entry in catalog_files:
        year = str(entry["year"])
        summary = by_year.setdefault(
            year,
            {"fileCount": 0, "pageCount": 0, "byteCount": 0},
        )
        summary["fileCount"] += 1
        summary["pageCount"] += int(entry["pages"])
        summary["byteCount"] += int(entry["bytes"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "officialIndexUrl": OFFICIAL_INDEX_URL,
        "archiveRoot": "output/pdf/gas-shunin-official",
        "coverage": {
            "firstYear": min(spec.year for spec in YEAR_SPECS),
            "lastYear": max(spec.year for spec in YEAR_SPECS),
            "years": [spec.year for spec in YEAR_SPECS],
            "grades": ["kou", "otsu", "hei"],
            "fileCount": len(catalog_files),
        },
        "byYear": by_year,
        "files": catalog_files,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_archive_index(archive_dir: Path, catalog: dict[str, Any]) -> None:
    by_year = {
        int(year): {
            str(entry["filename"])
            for entry in catalog["files"]
            if int(entry["year"]) == int(year)
        }
        for year in catalog["coverage"]["years"]
    }

    def link(year: int, filename: str, label: str) -> str:
        if filename not in by_year[year]:
            return "—"
        return f"[{label}]({year}/{filename})"

    lines = [
        "# ガス主任技術者試験 公式PDF archive",
        "",
        (
            f"{catalog['coverage']['firstYear']}〜"
            f"{catalog['coverage']['lastYear']}年度、"
            f"{catalog['coverage']['fileCount']}ファイル。"
        ),
        "",
        "| 年度 | 甲種 | 乙種 | 丙種 | 論述 | 正答 |",
        "|---:|---|---|---|---|---|",
    ]
    for year_spec in YEAR_SPECS:
        year = year_spec.year
        suffix = year_spec.filename_suffix
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{year}（{year_spec.era}）",
                    link(year, f"q_kou_{suffix}.pdf", "問題"),
                    link(year, f"q_otsu_{suffix}.pdf", "問題"),
                    link(year, f"q_hei_{suffix}.pdf", "問題"),
                    (
                        link(
                            year,
                            f"q_kouotsu_{suffix}-2.pdf",
                            "甲乙",
                        )
                        + " / "
                        + link(year, f"q_hei_{suffix}-2.pdf", "丙")
                    ),
                    (
                        link(year, f"a_kou_{suffix}.pdf", "甲")
                        + " / "
                        + link(year, f"a_otsu_{suffix}.pdf", "乙")
                        + " / "
                        + link(year, f"a_hei_{suffix}.pdf", "丙")
                    ),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "取得元URL・保存版URL・SHA-256・ページ数は、",
            "`document/sources/gas-shunin/official_exam_pdf_catalog.json` "
            "を参照してください。",
            "",
        ]
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    temporary = archive_dir / "INDEX.md.tmp"
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(archive_dir / "INDEX.md")


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"catalog schemaが不正です: {path}")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"catalog filesが不正です: {path}")
    return payload


def run_downloads(
    entries: list[dict[str, Any]],
    archive_dir: Path,
    *,
    workers: int,
    timeout_seconds: int,
    retry_count: int,
    locked: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                download_pdf,
                entry,
                archive_dir,
                timeout_seconds=timeout_seconds,
                retry_count=retry_count,
                expected_sha256=(
                    str(entry.get("sha256") or "") or None
                    if locked
                    else None
                ),
            ): entry
            for entry in entries
        }
        total = len(futures)
        for index, future in enumerate(as_completed(futures), start=1):
            entry = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failures.append(f"{entry['filename']}: {exc}")
                print(
                    f"[{index:02d}/{total:02d}] "
                    f"{entry['year']} {entry['filename']} ERROR",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            results.append(result)
            print(
                f"[{index:02d}/{total:02d}] "
                f"{entry['year']} {entry['filename']} "
                f"{result['downloadStatus']} pages={result['pages']}",
                flush=True,
            )
    if failures:
        raise RuntimeError(
            f"PDF取得失敗: {len(failures)}件\n" + "\n".join(failures)
        )
    return sorted(
        results,
        key=lambda item: (int(item["year"]), str(item["filename"])),
    )


def verify_catalog(catalog: dict[str, Any], archive_dir: Path) -> list[str]:
    errors: list[str] = []
    files = catalog["files"]
    expected_paths: set[str] = set()
    for entry in files:
        relative = f"{entry['year']}/{entry['filename']}"
        expected_paths.add(relative)
        path = archive_dir / relative
        try:
            actual = inspect_pdf(path)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        for key in ("sha256", "bytes", "pages"):
            if actual[key] != entry.get(key):
                errors.append(
                    f"{relative}: {key} expected={entry.get(key)} "
                    f"actual={actual[key]}"
                )
    actual_paths = {
        path.relative_to(archive_dir).as_posix()
        for path in archive_dir.glob("*/*.pdf")
    }
    for unexpected in sorted(actual_paths - expected_paths):
        errors.append(f"catalog外PDF: {unexpected}")
    return errors


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "JIA公式のガス主任技術者試験PDFを年度・甲乙丙種ごとに"
            "ローカルarchiveへ保存・検証します。"
        )
    )
    result.add_argument(
        "action",
        choices=("refresh-catalog", "sync", "verify"),
    )
    result.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    result.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    result.add_argument("--workers", type=int, default=4)
    result.add_argument("--timeout-seconds", type=int, default=90)
    result.add_argument("--retry-count", type=int, default=5)
    return result


def main() -> int:
    args = parser().parse_args()
    archive_dir = args.archive_dir.resolve()
    catalog_path = args.catalog.resolve()
    if args.workers < 1 or args.workers > 8:
        raise SystemExit("--workersは1〜8で指定してください。")

    if args.action == "refresh-catalog":
        files = run_downloads(
            build_source_specs(),
            archive_dir,
            workers=args.workers,
            timeout_seconds=args.timeout_seconds,
            retry_count=args.retry_count,
            locked=False,
        )
        catalog = _catalog_payload(files)
        write_json(catalog_path, catalog)
        write_archive_index(archive_dir, catalog)
        errors = verify_catalog(catalog, archive_dir)
    else:
        catalog = load_catalog(catalog_path)
        if args.action == "sync":
            run_downloads(
                [dict(entry) for entry in catalog["files"]],
                archive_dir,
                workers=args.workers,
                timeout_seconds=args.timeout_seconds,
                retry_count=args.retry_count,
                locked=True,
            )
        write_archive_index(archive_dir, catalog)
        errors = verify_catalog(catalog, archive_dir)

    if errors:
        for error in errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        print(f"[NG] PDF archive検証失敗: {len(errors)}件", file=sys.stderr)
        return 1
    print(
        f"[OK] JIA公式PDF archive: "
        f"{len(catalog['files'])} files / "
        f"{len(catalog['coverage']['years'])} years / "
        f"{archive_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
