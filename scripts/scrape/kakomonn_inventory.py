#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrape.common import create_http_session, load_local_secure_env  # noqa: E402


DEFAULT_TOP_URL = "https://kakomonn.com/"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "scrape_presets.json"
GENERIC_LINK_TEXTS = {
    "",
    "出題",
    "続きから出題",
    "問題一覧",
    "付箋メモ（ログインが必要です）",
    "解答履歴（ログインが必要です）",
    "分析（ログインが必要です）",
    "ログイン",
    "無料会員登録",
    "アンケート",
    "総合トップページ",
}


@dataclass(frozen=True)
class KakomonnQualification:
    name: str
    host: str
    slug: str
    home_url: str
    list_url: str


@dataclass(frozen=True)
class KakomonnListTarget:
    label: str
    source_list_group_id: str
    output_list_group_id: str
    inferred_year: int | None
    url: str


@dataclass(frozen=True)
class ConfiguredPreset:
    preset_key: str
    qualification_code: str
    qualification_name: str
    host: str
    list_group_ids: list[str]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def fetch_text(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def is_kakomonn_qualification_host(host: str) -> bool:
    host = host.lower()
    return host.endswith(".kakomonn.com") and host != "kakomonn.com"


def discover_qualifications_from_html(
    html: str,
    *,
    base_url: str = DEFAULT_TOP_URL,
) -> list[KakomonnQualification]:
    soup = BeautifulSoup(html, "html.parser")
    by_host: dict[str, KakomonnQualification] = {}

    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, str(anchor.get("href") or ""))
        parsed = urlparse(href)
        host = parsed.netloc.lower()
        if not is_kakomonn_qualification_host(host):
            continue

        text = normalize_text(anchor.get_text(" ", strip=True))
        if text in GENERIC_LINK_TEXTS:
            continue

        path = parsed.path.rstrip("/")
        if path not in ("", "/"):
            continue

        slug = host.removesuffix(".kakomonn.com")
        by_host.setdefault(
            host,
            KakomonnQualification(
                name=text,
                host=host,
                slug=slug,
                home_url=f"https://{host}/",
                list_url=f"https://{host}/list",
            ),
        )

    return list(by_host.values())


def discover_qualifications(
    session: requests.Session,
    *,
    top_url: str = DEFAULT_TOP_URL,
) -> list[KakomonnQualification]:
    return discover_qualifications_from_html(fetch_text(session, top_url), base_url=top_url)


def ensure_page_one(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = query.get("page") or "1"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            urlencode(query),
            "",
        )
    )


def infer_year_from_label(label: str) -> int | None:
    normalized = normalize_text(label)
    western_match = re.search(r"((?:19|20)\d{2})\s*年?度?", normalized)
    if western_match:
        return int(western_match.group(1))

    reiwa_match = re.search(r"令和\s*(元|\d+)\s*年?度?", normalized)
    if reiwa_match:
        year_number = 1 if reiwa_match.group(1) == "元" else int(reiwa_match.group(1))
        return 2018 + year_number

    heisei_match = re.search(r"平成\s*(元|\d+)\s*年?度?", normalized)
    if heisei_match:
        year_number = 1 if heisei_match.group(1) == "元" else int(heisei_match.group(1))
        return 1988 + year_number

    return None


def discover_list_targets_from_html(
    html: str,
    *,
    list_url: str,
    output_group_id_mode: str = "source-list-group-id",
) -> list[KakomonnListTarget]:
    soup = BeautifulSoup(html, "html.parser")
    raw_targets: list[tuple[str, str, int | None, str]] = []
    seen_source_ids: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = ensure_page_one(urljoin(list_url, str(anchor.get("href") or "")))
        parsed = urlparse(href)
        match = re.search(r"/list1/([^/?#]+)/?$", parsed.path)
        if not match:
            continue

        source_list_group_id = match.group(1)
        if source_list_group_id in seen_source_ids:
            continue
        seen_source_ids.add(source_list_group_id)

        label = normalize_text(anchor.get_text(" ", strip=True))
        raw_targets.append(
            (
                label,
                source_list_group_id,
                infer_year_from_label(label),
                href,
            )
        )

    year_counts: dict[int, int] = {}
    for _, _, inferred_year, _ in raw_targets:
        if inferred_year is not None:
            year_counts[inferred_year] = year_counts.get(inferred_year, 0) + 1

    targets: list[KakomonnListTarget] = []
    for label, source_list_group_id, inferred_year, href in raw_targets:
        output_list_group_id = source_list_group_id
        if (
            output_group_id_mode == "year-when-unique"
            and inferred_year is not None
            and year_counts.get(inferred_year) == 1
        ):
            output_list_group_id = str(inferred_year)

        targets.append(
            KakomonnListTarget(
                label=label,
                source_list_group_id=source_list_group_id,
                output_list_group_id=output_list_group_id,
                inferred_year=inferred_year,
                url=href,
            )
        )

    return targets


def discover_list_targets(
    session: requests.Session,
    qualification: KakomonnQualification,
    *,
    output_group_id_mode: str = "source-list-group-id",
) -> list[KakomonnListTarget]:
    html = fetch_text(session, qualification.list_url)
    return discover_list_targets_from_html(
        html,
        list_url=qualification.list_url,
        output_group_id_mode=output_group_id_mode,
    )


def raw_target_group_ids(preset: dict) -> list[str]:
    raw_targets = preset.get("scrape_targets")
    if isinstance(raw_targets, list):
        return [
            str(target.get("output_list_group_id"))
            for target in raw_targets
            if isinstance(target, dict) and target.get("output_list_group_id") is not None
        ]
    raw_ids = preset.get("list_group_ids") or []
    return [str(group_id) for group_id in raw_ids]


def load_configured_kakomonn_presets(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, list[ConfiguredPreset]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    by_host: dict[str, list[ConfiguredPreset]] = {}
    for preset_key, preset in raw.items():
        if not isinstance(preset, dict):
            continue
        if str(preset.get("scraper_type", "kakomonn")) != "kakomonn":
            continue

        template = str(preset.get("list_first_page_url_template") or "")
        if not template:
            continue
        try:
            sample_url = template.format(list_group_id="0")
        except Exception:
            sample_url = template.replace("{list_group_id}", "0")

        host = urlparse(sample_url).netloc.lower()
        if not is_kakomonn_qualification_host(host):
            continue

        by_host.setdefault(host, []).append(
            ConfiguredPreset(
                preset_key=str(preset_key),
                qualification_code=str(preset.get("qualification_code") or preset_key),
                qualification_name=str(preset.get("qualification_name") or ""),
                host=host,
                list_group_ids=raw_target_group_ids(preset),
            )
        )
    return by_host


def existing_source_group_ids(output_root: Path, qualification_code: str) -> list[str]:
    questions_root = output_root / qualification_code / "questions_json"
    if not questions_root.exists():
        return []
    group_ids: list[str] = []
    for source_dir in sorted(questions_root.glob("*/00_source")):
        if source_dir.is_dir() and any(source_dir.glob("question*.json")):
            group_ids.append(source_dir.parent.name)
    return group_ids


def build_inventory_rows(
    *,
    qualifications: list[KakomonnQualification],
    configured_by_host: dict[str, list[ConfiguredPreset]],
    output_root: Path,
    target_counts_by_host: dict[str, int] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for qualification in qualifications:
        configured_presets = configured_by_host.get(qualification.host, [])
        primary_preset = configured_presets[0] if configured_presets else None
        qualification_code = primary_preset.qualification_code if primary_preset else qualification.slug
        existing_groups = existing_source_group_ids(output_root, qualification_code)

        if primary_preset and existing_groups:
            status = "configured_scraped"
        elif primary_preset:
            status = "configured_not_scraped"
        elif existing_groups:
            status = "unconfigured_source_exists"
        else:
            status = "missing_preset"

        rows.append(
            {
                "status": status,
                "name": qualification.name,
                "slug": qualification.slug,
                "host": qualification.host,
                "home_url": qualification.home_url,
                "list_url": qualification.list_url,
                "suggested_qualification_code": qualification.slug,
                "configured_preset_keys": [preset.preset_key for preset in configured_presets],
                "qualification_code": qualification_code,
                "configured_group_count": (
                    len(primary_preset.list_group_ids) if primary_preset else 0
                ),
                "existing_source_group_count": len(existing_groups),
                "existing_source_group_ids": existing_groups,
                "discovered_list1_count": (
                    target_counts_by_host or {}
                ).get(qualification.host),
            }
        )
    return rows


def format_table(rows: list[dict]) -> str:
    headers = ["status", "slug", "name", "preset", "source", "list1"]
    data = [
        [
            row["status"],
            row["slug"],
            row["name"],
            ",".join(row["configured_preset_keys"]) or "-",
            str(row["existing_source_group_count"]),
            "" if row["discovered_list1_count"] is None else str(row["discovered_list1_count"]),
        ]
        for row in rows
    ]
    widths = [
        max(len(str(value)) for value in [header, *[row[index] for row in data]])
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for row in data:
        lines.append("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, rows: list[dict], *, source_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# kakomonn inventory",
        "",
        f"- source: {source_url}",
        f"- total qualifications: {len(rows)}",
        "",
        "| status | slug | name | preset | source groups | list1 |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        list1_count = row["discovered_list1_count"]
        lines.append(
            "| {status} | {slug} | {name} | {preset} | {source} | {list1} |".format(
                status=row["status"],
                slug=row["slug"],
                name=row["name"].replace("|", "\\|"),
                preset=", ".join(row["configured_preset_keys"]) or "-",
                source=row["existing_source_group_count"],
                list1="" if list1_count is None else list1_count,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_rows(rows: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {"total": len(rows)}
    for row in rows:
        status = str(row["status"])
        summary[status] = summary.get(status, 0) + 1
    return summary


def resolve_requested_qualifications(
    rows: list[dict],
    qualifications: list[KakomonnQualification],
    requested: list[str],
) -> list[KakomonnQualification]:
    by_key: dict[str, KakomonnQualification] = {}
    for qualification in qualifications:
        by_key[qualification.slug] = qualification
        by_key[qualification.host] = qualification
        by_key[qualification.name] = qualification

    selected: list[KakomonnQualification] = []
    unknown: list[str] = []
    for key in requested:
        qualification = by_key.get(key)
        if qualification is None:
            unknown.append(key)
            continue
        if qualification not in selected:
            selected.append(qualification)

    if unknown:
        raise ValueError(f"unknown kakomonn qualification: {', '.join(unknown)}")
    return selected


def print_scrape_plan(
    *,
    qualification: KakomonnQualification,
    qualification_code: str,
    targets: list[KakomonnListTarget],
) -> None:
    print(
        f"[PLAN] qualification={qualification_code} name={qualification.name} "
        f"host={qualification.host} groups={len(targets)}",
        flush=True,
    )
    for target in targets:
        year = "" if target.inferred_year is None else f" year={target.inferred_year}"
        print(
            "  - output={output} source={source}{year} label={label} url={url}".format(
                output=target.output_list_group_id,
                source=target.source_list_group_id,
                year=year,
                label=target.label,
                url=target.url,
            ),
            flush=True,
        )


def run_inventory(args: argparse.Namespace) -> int:
    session = create_http_session()
    qualifications = discover_qualifications(session, top_url=args.top_url)
    configured_by_host = load_configured_kakomonn_presets(Path(args.config))
    output_root = Path(args.output_dir).expanduser().resolve()

    target_counts_by_host: dict[str, int] = {}
    if args.discover_targets:
        for qualification in qualifications:
            try:
                targets = discover_list_targets(
                    session,
                    qualification,
                    output_group_id_mode=args.output_group_id,
                )
                target_counts_by_host[qualification.host] = len(targets)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] target discovery failed: {qualification.host}: {exc}")
                target_counts_by_host[qualification.host] = -1

    rows = build_inventory_rows(
        qualifications=qualifications,
        configured_by_host=configured_by_host,
        output_root=output_root,
        target_counts_by_host=target_counts_by_host if args.discover_targets else None,
    )
    if args.only_missing:
        rows = [
            row
            for row in rows
            if row["status"] in {"missing_preset", "configured_not_scraped"}
        ]

    summary = summarize_rows(rows)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(format_table(rows))

    payload = {
        "source_url": args.top_url,
        "summary": summary,
        "rows": rows,
    }
    if args.json_output:
        write_json(Path(args.json_output).expanduser().resolve(), payload)
        print(f"[WRITE] {args.json_output}")
    if args.markdown_output:
        write_markdown(
            Path(args.markdown_output).expanduser().resolve(),
            rows,
            source_url=args.top_url,
        )
        print(f"[WRITE] {args.markdown_output}")

    return 0


def run_scrape(args: argparse.Namespace) -> int:
    session = create_http_session()
    qualifications = discover_qualifications(session, top_url=args.top_url)
    configured_by_host = load_configured_kakomonn_presets(Path(args.config))
    output_root = Path(args.output_dir).expanduser().resolve()
    rows = build_inventory_rows(
        qualifications=qualifications,
        configured_by_host=configured_by_host,
        output_root=output_root,
    )

    if args.all_missing:
        selected_hosts = {
            row["host"]
            for row in rows
            if row["status"] in {"missing_preset", "configured_not_scraped"}
        }
        selected = [qualification for qualification in qualifications if qualification.host in selected_hosts]
    else:
        selected = resolve_requested_qualifications(rows, qualifications, args.qualification)

    if args.max_qualifications is not None:
        selected = selected[: args.max_qualifications]

    if not selected:
        print("[ERROR] no kakomonn qualifications selected")
        return 2

    if (
        args.all_missing
        and not args.dry_run
        and args.max_qualifications is None
        and not args.yes
    ):
        print("[ERROR] --all-missing の本取得には --yes か --max-qualifications を指定してください")
        return 2

    if args.qualification_code and len(selected) != 1:
        print("[ERROR] --qualification-code can only be used with a single qualification")
        return 2

    failures = 0
    for qualification in selected:
        presets = configured_by_host.get(qualification.host) or []
        default_code = presets[0].qualification_code if presets else qualification.slug
        qualification_code = args.qualification_code or default_code
        targets = discover_list_targets(
            session,
            qualification,
            output_group_id_mode=args.output_group_id,
        )
        if args.max_groups is not None:
            targets = targets[: args.max_groups]

        if not args.force:
            existing_groups = set(existing_source_group_ids(output_root, qualification_code))
            targets = [
                target
                for target in targets
                if target.output_list_group_id not in existing_groups
            ]

        if not targets:
            print(f"[SKIP] qualification={qualification_code} は実行対象がありません")
            continue

        print_scrape_plan(
            qualification=qualification,
            qualification_code=qualification_code,
            targets=targets,
        )
        if args.dry_run:
            continue

        for index, target in enumerate(targets, start=1):
            print(
                f"[RUN] qualification={qualification_code} "
                f"({index}/{len(targets)}) list_group_id={target.output_list_group_id}",
                flush=True,
            )
            env = os.environ.copy()
            env["SCRAPER_QUALIFICATION_CODE"] = qualification_code
            env["SCRAPER_QUALIFICATION_NAME"] = qualification.name
            env["SCRAPER_LIST_FIRST_PAGE_URL"] = target.url
            env["SCRAPER_OUTPUT_LIST_GROUP_ID"] = target.output_list_group_id
            env["SCRAPER_OUTPUT_DIR"] = str(output_root)
            if args.max_questions is not None:
                env["SCRAPER_MAX_QUESTIONS"] = str(args.max_questions)

            result = subprocess.run(
                [args.python_executable, str(REPO_ROOT / "code.py")],
                cwd=REPO_ROOT,
                env=env,
                check=False,
            )
            if result.returncode != 0:
                failures += 1
                print(
                    f"[ERROR] scrape failed qualification={qualification_code} "
                    f"group={target.output_list_group_id} exit={result.returncode}"
                )
                if args.stop_on_error:
                    return result.returncode

    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="kakomonn.com の資格棚卸しと未登録資格の取得を行う。"
    )
    parser.add_argument(
        "--top-url",
        default=DEFAULT_TOP_URL,
        help=f"kakomonn 総合トップ URL。既定: {DEFAULT_TOP_URL}",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="scrape preset JSON のパス",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "output"),
        help="取得済み 00_source の確認先、または scrape 出力先",
    )
    parser.add_argument(
        "--output-group-id",
        choices=("source-list-group-id", "year-when-unique"),
        default="source-list-group-id",
        help="自動取得時の output list_group_id の決め方",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="資格一覧と未対応資格を棚卸しする")
    inventory.add_argument(
        "--discover-targets",
        action="store_true",
        help="各資格の /list を取得し list1 件数も数える",
    )
    inventory.add_argument(
        "--only-missing",
        action="store_true",
        help="missing_preset と configured_not_scraped だけ表示する",
    )
    inventory.add_argument("--json-output", help="棚卸し結果 JSON の保存先")
    inventory.add_argument("--markdown-output", help="棚卸し結果 Markdown の保存先")

    scrape = subparsers.add_parser("scrape", help="発見した kakomonn 資格を取得する")
    scrape.add_argument(
        "qualification",
        nargs="*",
        help="取得する資格 slug / host / 表示名。例: itpass",
    )
    scrape.add_argument(
        "--all-missing",
        action="store_true",
        help="missing_preset と configured_not_scraped を全て対象にする",
    )
    scrape.add_argument(
        "--qualification-code",
        help="単一資格取得時の出力 qualification code を明示する",
    )
    scrape.add_argument(
        "--python-executable",
        default=sys.executable,
        help="code.py 実行に使う Python",
    )
    scrape.add_argument("--max-qualifications", type=int, help="対象資格数の上限")
    scrape.add_argument("--max-groups", type=int, help="各資格の list1 グループ数上限")
    scrape.add_argument("--max-questions", type=int, help="各 list1 で取得する問題数上限")
    scrape.add_argument("--force", action="store_true", help="既存 00_source があっても再取得する")
    scrape.add_argument("--dry-run", action="store_true", help="実行計画だけ表示する")
    scrape.add_argument("--yes", action="store_true", help="--all-missing 本取得の安全確認")
    scrape.add_argument("--stop-on-error", action="store_true", help="失敗時にそこで止める")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_local_secure_env()
    args = parse_args(argv)
    if args.command == "inventory":
        return run_inventory(args)
    if args.command == "scrape":
        return run_scrape(args)
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
