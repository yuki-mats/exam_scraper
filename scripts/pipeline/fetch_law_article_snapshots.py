#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check.check_law_revision_fact_coverage import latest_firestore_file


ARTICLE_NUMBER_PATTERN = re.compile(r"^第?(.+?)条(?:の(.+))?$")
APPENDIX_TABLE_PATTERN = re.compile(r"^別表第([0-9０-９]+)$")
KANJI_DIGITS = {
    0: "零",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}


class LawArticleSnapshotError(RuntimeError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def timestamp_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def output_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_firestore_questions(list_group_dir: Path) -> list[dict[str, Any]]:
    source_file = latest_firestore_file(list_group_dir)
    if source_file is None:
        raise LawArticleSnapshotError(f"no Firestore JSON under {list_group_dir / '40_convert'}")
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [entry for entry in payload["questions"] if isinstance(entry, dict)]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    raise LawArticleSnapshotError(f"unsupported Firestore JSON shape: {source_file}")


def normalize_article_for_api(article: str) -> str:
    value = article.strip()
    table_match = APPENDIX_TABLE_PATTERN.match(value)
    if table_match:
        return f"別表第{kanji_number(int(table_match.group(1).translate(str.maketrans('０１２３４５６７８９', '0123456789'))))}"
    match = ARTICLE_NUMBER_PATTERN.match(value)
    if not match:
        return value
    base = match.group(1).strip()
    suffix = (match.group(2) or "").strip()
    if suffix:
        return f"{base}_{suffix}"
    return base


def kanji_number(value: int) -> str:
    if value < 10:
        return KANJI_DIGITS[value]
    if value == 10:
        return "十"
    if value < 20:
        return "十" + KANJI_DIGITS[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        result = KANJI_DIGITS[tens] + "十"
        if ones:
            result += KANJI_DIGITS[ones]
        return result
    return str(value)


def article_query_candidates(article: str) -> list[str]:
    raw = article.strip()
    normalized = normalize_article_for_api(raw)
    candidates: list[str] = []
    for value in (normalized, raw):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def egov_article_api_url(law_id: str, article_query: str) -> str:
    return (
        "https://laws.e-gov.go.jp/api/1/articles;"
        f"lawId={urllib.parse.quote(law_id, safe='')};"
        f"article={urllib.parse.quote(article_query, safe='')}"
    )


def is_success_response(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    code = root.findtext("./Result/Code")
    return code == "0"


def fetch_article_xml(law_id: str, article: str, *, timeout_seconds: float) -> tuple[str, str, int]:
    last_error = ""
    for article_query in article_query_candidates(article):
        url = egov_article_api_url(law_id, article_query)
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
                status_code = int(response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status_code = int(exc.code)
        except urllib.error.URLError as exc:
            last_error = str(exc)
            continue
        if status_code == 200 and is_success_response(body):
            return article_query, body, status_code
        last_error = f"{status_code}: {body[:180].strip()}"
    raise LawArticleSnapshotError(last_error or "article fetch failed")


def element_text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def parse_article_text(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    law_contents = root.find("./ApplData/LawContents")
    if law_contents is None:
        raise LawArticleSnapshotError("LawContents not found in e-Gov response")
    lines: list[str] = []
    for article in law_contents.findall(".//Article"):
        caption = article.findtext("ArticleCaption")
        title = article.findtext("ArticleTitle")
        if caption and caption.strip():
            lines.append(caption.strip())
        if title and title.strip():
            lines.append(title.strip())
        for paragraph in article.findall("Paragraph"):
            paragraph_lines: list[str] = []
            paragraph_num = paragraph.findtext("ParagraphNum")
            if paragraph_num and paragraph_num.strip():
                paragraph_lines.append(paragraph_num.strip())
            for sentence in paragraph.findall(".//Sentence"):
                text = element_text(sentence)
                if text:
                    paragraph_lines.append(text)
            if paragraph_lines:
                lines.append(" ".join(paragraph_lines))
    for table in law_contents.findall(".//AppdxTable"):
        title = table.findtext("AppdxTableTitle")
        if title and title.strip():
            lines.append(title.strip())
        table_text = element_text(table)
        if table_text:
            for line in table_text.splitlines():
                stripped = line.strip()
                if stripped and stripped not in lines:
                    lines.append(stripped)
    article_text = "\n".join(line for line in lines if line.strip()).strip()
    if not article_text:
        raise LawArticleSnapshotError("article text is empty")
    return article_text


def extract_verified_current_refs(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for question in questions:
        for ref in question.get("lawReferences") or []:
            if not isinstance(ref, dict):
                continue
            if ref.get("verificationStatus") != "verified":
                continue
            if ref.get("role") not in (None, "current_basis"):
                continue
            law_id = ref.get("lawId")
            article = ref.get("article")
            if not isinstance(law_id, str) or not law_id.strip():
                continue
            if not isinstance(article, str) or not article.strip():
                continue
            key = (
                law_id.strip(),
                article.strip(),
                (ref.get("paragraph") or "").strip() if isinstance(ref.get("paragraph"), str) else "",
                (ref.get("item") or "").strip() if isinstance(ref.get("item"), str) else "",
                (ref.get("subitem") or "").strip() if isinstance(ref.get("subitem"), str) else "",
                (ref.get("lawTitle") or "").strip() if isinstance(ref.get("lawTitle"), str) else "",
            )
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = dict(ref)
                by_key[key]["questionIds"] = [question.get("questionId")]
                by_key[key]["originalQuestionIds"] = [question.get("originalQuestionId")]
            else:
                existing["questionIds"].append(question.get("questionId"))
                existing["originalQuestionIds"].append(question.get("originalQuestionId"))
    return [by_key[key] for key in sorted(by_key)]


def raw_xml_filename(ref: dict[str, Any], article_query: str, raw_xml_hash: str) -> str:
    law_id = str(ref["lawId"]).strip()
    safe_article = re.sub(r"[^0-9A-Za-z_\-]+", "_", article_query.strip())
    return f"{law_id}_{safe_article}_{raw_xml_hash[:16]}.xml"


def build_snapshot(
    ref: dict[str, Any],
    *,
    fetched_at: str,
    timeout_seconds: float,
    raw_xml_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    law_id = str(ref["lawId"]).strip()
    article = str(ref["article"]).strip()
    if dry_run:
        article_query = article_query_candidates(article)[0]
        api_url = egov_article_api_url(law_id, article_query)
        return {
            "status": "dry_run",
            "lawId": law_id,
            "lawTitle": ref.get("lawTitle"),
            "article": article,
            "paragraph": ref.get("paragraph"),
            "item": ref.get("item"),
            "subitem": ref.get("subitem"),
            "articleQuery": article_query,
            "apiUrl": api_url,
            "referenceDate": ref.get("referenceDate"),
            "source": "e-gov-law-api-v1",
            "fetchedAt": fetched_at,
        }

    article_query, raw_xml, status_code = fetch_article_xml(
        law_id,
        article,
        timeout_seconds=timeout_seconds,
    )
    article_text = parse_article_text(raw_xml)
    raw_xml_hash = sha256_text(raw_xml)
    article_text_hash = sha256_text(article_text)
    raw_xml_dir.mkdir(parents=True, exist_ok=True)
    raw_xml_path = raw_xml_dir / raw_xml_filename(ref, article_query, raw_xml_hash)
    raw_xml_path.write_text(raw_xml, encoding="utf-8")
    return {
        "status": "fetched",
        "lawId": law_id,
        "lawTitle": ref.get("lawTitle"),
        "article": article,
        "paragraph": ref.get("paragraph"),
        "item": ref.get("item"),
        "subitem": ref.get("subitem"),
        "articleQuery": article_query,
        "apiUrl": egov_article_api_url(law_id, article_query),
        "referenceDate": ref.get("referenceDate"),
        "source": "e-gov-law-api-v1",
        "httpStatus": status_code,
        "fetchedAt": fetched_at,
        "articleText": article_text,
        "articleTextHash": article_text_hash,
        "rawXmlPath": str(raw_xml_path),
        "rawXmlHash": raw_xml_hash,
        "questionIds": sorted({value for value in ref.get("questionIds", []) if isinstance(value, str)}),
        "originalQuestionIds": sorted({value for value in ref.get("originalQuestionIds", []) if isinstance(value, str)}),
    }


def default_output_dir(list_group_dir: Path) -> Path:
    questions_json_dir = list_group_dir.parent
    qualification_dir = questions_json_dir.parent
    return qualification_dir / "law_evidence" / list_group_dir.name / "current_article_snapshots"


def run(
    *,
    list_group_dir: Path,
    output_dir: Path | None,
    timestamp: str | None,
    limit: int | None,
    timeout_seconds: float,
    delay_seconds: float,
    dry_run: bool,
    fail_on_fetch_error: bool,
) -> int:
    questions = load_firestore_questions(list_group_dir)
    refs = extract_verified_current_refs(questions)
    if limit is not None:
        refs = refs[:limit]
    stamp = timestamp or output_timestamp()
    out_dir = output_dir or default_output_dir(list_group_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{list_group_dir.name}_current_article_snapshots_{stamp}.jsonl"
    raw_xml_dir = out_dir / "raw_xml" / stamp
    fetched_at = timestamp_now()
    failures = 0
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for index, ref in enumerate(refs, start=1):
            try:
                snapshot = build_snapshot(
                    ref,
                    fetched_at=fetched_at,
                    timeout_seconds=timeout_seconds,
                    raw_xml_dir=raw_xml_dir,
                    dry_run=dry_run,
                )
            except Exception as exc:
                failures += 1
                snapshot = {
                    "status": "fetch_failed",
                    "lawId": ref.get("lawId"),
                    "lawTitle": ref.get("lawTitle"),
                    "article": ref.get("article"),
                    "paragraph": ref.get("paragraph"),
                    "item": ref.get("item"),
                    "subitem": ref.get("subitem"),
                    "referenceDate": ref.get("referenceDate"),
                    "source": "e-gov-law-api-v1",
                    "fetchedAt": fetched_at,
                    "error": str(exc),
                }
            fh.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")
            print(
                f"[{index}/{len(refs)}] {snapshot['status']} "
                f"{snapshot.get('lawId')} {snapshot.get('article')}",
                flush=True,
            )
            if delay_seconds > 0 and index < len(refs):
                time.sleep(delay_seconds)
    print(f"snapshot_jsonl: {jsonl_path}", flush=True)
    print(f"target_refs: {len(refs)}", flush=True)
    print(f"fetch_failures: {failures}", flush=True)
    if failures and fail_on_fetch_error:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch current e-Gov article snapshots for verified current lawReferences."
    )
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timestamp")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--delay-seconds", type=float, default=0.1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-fetch-error", action="store_true")
    args = parser.parse_args()
    return run(
        list_group_dir=args.list_group_dir,
        output_dir=args.output_dir,
        timestamp=args.timestamp,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
        delay_seconds=args.delay_seconds,
        dry_run=args.dry_run,
        fail_on_fetch_error=args.fail_on_fetch_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
