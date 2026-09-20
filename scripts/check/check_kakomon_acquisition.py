"""取得HTMLと保存sourceを別経路で照合する（00_sourceには書き込まない）。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.scrape.common import create_http_session, fetch_html_text, SUPERSCRIPT_MAP, SUBSCRIPT_MAP
from scripts.scrape.kakomon import discover_groups
from scripts.scrape.qualification_presets import load_scrape_preset


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def visible_text(element) -> str:
    """DOM文字と数式構造を独立に照合用文字列へ写す。"""
    copy = BeautifulSoup(str(element), "html.parser")
    for node in reversed(copy.select("sup, sub, small, .fraction")):
        if "fraction" in node.get("class", []):
            parts = node.find_all(recursive=False)
            if len(parts) != 2:
                raise ValueError("未対応の分数構造です")
            node.replace_with(f"({parts[0].get_text()})/({parts[1].get_text()})")
        else:
            mapping = SUPERSCRIPT_MAP if node.name == "sup" else SUBSCRIPT_MAP
            node.replace_with("".join(mapping.get(c, mapping.get(c.lower(), c)) for c in node.get_text()))
    return compact(copy.get_text())


def audit_page(html: str, record: dict, *, number: int, group: str) -> list[str]:
    """serializerを呼ばず、問題領域全体・肢順・正答・遷移を検査する。"""
    soup = BeautifulSoup(html, "html.parser")
    failures = []
    wrap = soup.select_one(".question-wrap")
    if not wrap or visible_text(wrap) != compact(record["questionBodyText"]):
        failures.append("question_text")
    choices = soup.select(".choices-wrap .choice-body")
    if len(choices) != len(record["choiceTextList"]):
        failures.append("choice_count")
    for index, (element, saved) in enumerate(zip(choices, record["choiceTextList"]), 1):
        text = re.sub(r"^[１-５1-5][：:]", "", visible_text(element))
        if compact(text) != compact(saved) or element.get("data-cnum") != str(index):
            failures.append(f"choice_{index}")
    answer = soup.select_one(".answer-body")
    match = re.search(r"答\s*[：:]\s*([1-5１-５])", answer.get_text() if answer else "")
    if not match or [int(match[1])] != record["answer_result_inferred_correct_choice_numbers"]:
        failures.append("answer")
    canonical = soup.select_one('link[rel="canonical"]')
    if not canonical or canonical.get("href") != record["question_url"]:
        failures.append("canonical_url")
    title = soup.select_one("h1")
    heading = title.get_text() if title else ""
    year = re.search(r"(令和|平成)(元|\d+)年(前|後)期", heading)
    expected_year, term = group.split("-")
    if not year or (
        (2018 if year[1] == "令和" else 1988) + (1 if year[2] == "元" else int(year[2]))
        != int(expected_year)
        or ("1" if year[3] == "前" else "2") != term
    ):
        failures.append("exam_year_term")
    suffix = re.search(r"(?:-([AB]))?-問(\d+)$", heading.strip())
    if not suffix or int(suffix[2]) + (20 if suffix[1] == "B" else 0) != number:
        failures.append("question_number")
    next_link = soup.select_one("a.question-next[href]")
    if not next_link:
        failures.append("next_link_missing")
    elif number < 40:
        next_number = number + 1
        expected_suffix = f"/{group}-{(next_number - 1) // 10 + 1:02d}-{next_number:03d}/"
        if not next_link["href"].endswith(expected_suffix):
            failures.append("next_link_sequence")
    elif f"/{group}-" in next_link["href"]:
        failures.append("unexpected_extra_question")
    image_elements = [wrap, *choices] if wrap else choices
    extracted_images = [
        list(dict.fromkeys(
            urljoin(record["question_url"], str(img.get("data-src") or img.get("src") or ""))
            for img in element.select("img")
        )) for element in image_elements
    ]
    if extracted_images != [record["questionImageSourceUrls"], *record["choiceImageSourceUrlsByChoice"]]:
        failures.append("image_assignment")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qualification")
    parser.add_argument("--index-url", required=True)
    args = parser.parse_args()
    root = REPO_ROOT / "output" / args.qualification
    verification = root / "verification"
    verification.mkdir(parents=True, exist_ok=True)
    session = create_http_session()
    index_html = fetch_html_text(session, args.index_url)
    with gzip.open(verification / "index.html.gz", "wt", encoding="utf-8") as output:
        output.write(index_html)
    inventory = discover_groups(index_html, page_url=args.index_url)
    preset = load_scrape_preset(args.qualification)
    errors = []
    if set(inventory) != set(preset.list_group_ids):
        errors.append({"inventoryPresetDifference": sorted(set(inventory) ^ set(preset.list_group_ids))})
    rows = []
    seen_ids = set()
    seen_public_ids = set()
    image_files = set()
    all_urls = set()
    next_urls = set()
    for group in inventory:
        path = root / "questions_json" / group / "00_source" / f"question_{group}.json"
        if not path.is_file():
            errors.append({"group": group, "error": "source_missing"})
            continue
        records = json.loads(path.read_text())["question_bodies"]
        if len(records) != 40:
            errors.append({"group": group, "error": "count", "actual": len(records)})
        record_urls = {record["question_url"] for record in records}
        all_urls.update(record_urls)
        if not set(inventory[group]) <= record_urls:
            errors.append({"group": group, "error": "index_link_missing"})
        supplemental = image_questions = 0
        for number, record in enumerate(records, 1):
            html_path = verification / "html" / group / f"{number:03d}.html.gz"
            if not html_path.is_file():
                errors.append({"group": group, "number": number, "error": "html_missing"})
                continue
            with gzip.open(html_path, "rt", encoding="utf-8") as source:
                html = source.read()
            failures = audit_page(html, record, number=number, group=group)
            next_link = BeautifulSoup(html, "html.parser").select_one("a.question-next[href]")
            if next_link:
                next_urls.add(urljoin(record["question_url"], next_link["href"]))
            source_id = record["source_question_id"]
            public_id = record["public_question_id"]
            if source_id in seen_ids or public_id in seen_public_ids:
                failures.append("duplicate_id")
            seen_ids.add(source_id)
            seen_public_ids.add(public_id)
            image_urls = [*record["questionImageStorageUrls"], *[url for urls in record["originalQuestionChoiceImageUrls"] for url in urls]]
            image_questions += bool(image_urls)
            for url in image_urls:
                filename = Path(unquote(urlparse(url).path)).name
                image_path = root / "question_images" / group / filename
                if not image_path.is_file() or not image_path.stat().st_size:
                    failures.append("image_file_missing")
                image_files.add(str(image_path))
            supplemental += "\n" in record["questionBodyText"]
            if failures:
                errors.append({"group": group, "number": number, "failures": failures})
        rows.append({"group": group, "questionCount": len(records), "supplementalTextQuestions": supplemental,
                     "imageQuestions": image_questions, "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if next_urls != all_urls:
        errors.append({"error": "navigation_coverage", "notLinked": sorted(all_urls - next_urls),
                       "notAcquired": sorted(next_urls - all_urls)})
    result = {"status": "passed" if not errors else "failed", "indexUrl": args.index_url,
              "auditedAt": datetime.now(timezone.utc).isoformat(),
              "siteGroupCount": len(inventory), "questionCount": sum(row["questionCount"] for row in rows),
              "uniqueSourceIds": len(seen_ids), "uniquePublicIds": len(seen_public_ids),
              "imageFileCount": len(image_files), "groups": rows, "errors": errors}
    report_path = verification / "acquisition_audit.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "groups"}, ensure_ascii=False, indent=2))
    print(report_path)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
