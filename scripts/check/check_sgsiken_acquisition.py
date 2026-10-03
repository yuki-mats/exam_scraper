"""取得時HTMLとsourceを独立に照合する。sourceには書き込まない。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.scrape.common import SUBSCRIPT_MAP, SUPERSCRIPT_MAP, create_http_session, fetch_html_text, is_placeholder_image_url, download_image_with_retry
from scripts.scrape.qualification_presets import build_list_first_page_url, load_scrape_preset


def verify_inventory_html(html: str, expected_urls: set[str], index_url: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = {urljoin(index_url, link["href"]) for link in soup.select("a[href]") if re.fullmatch(r"/kakomon/\d{2}_(?:haru|aki)/", link["href"])}
    if urls != expected_urls:
        raise ValueError(f"年度一覧がpresetと一致しません: 未設定={sorted(urls - expected_urls)} 掲載なし={sorted(expected_urls - urls)}")
    return urls


def check_live_inventory(preset, output_dir: Path) -> None:
    index_url = "https://www.sg-siken.com/sgkakomon.php"
    html = fetch_html_text(create_http_session(), index_url)
    verify_inventory_html(html, {build_list_first_page_url(preset, group) for group in preset.list_group_ids}, index_url)
    path = output_dir / preset.qualification_code / "verification" / "dojo" / "inventory.html.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as evidence:
        evidence.write(html)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def visible(node, *, preserve_choice_labels: bool = False) -> str:
    if node is None:
        return ""
    soup = BeautifulSoup(str(node), "html.parser")
    for button in soup.select("button, .tool-box"):
        button.decompose()
    if preserve_choice_labels:
        css_labels = dict(zip(("lia", "lii", "liu", "lie", "lio", "lika", "liki"), "アイウエオカキ"))
        for item in soup.select("li"):
            labels = [css_labels[name] for name in item.get("class", []) if name in css_labels]
            if labels:
                item.insert(0, labels[0] + " ")
    for listing in soup.select("ul, ol"):
        ordinal = int(listing.get("start", "1")) - 1
        css_ordinal = 0
        for item in listing.find_all("li", recursive=False):
            classes = item.get("class", [])
            family = next(("maru" if name.startswith("maru") else "li" for name in classes if re.fullmatch(r"(?:li|maru)\d+", name)), None)
            if family:
                css_ordinal = 1 if f"{family}1" in classes else css_ordinal + 1
                marker = chr(0x2460 + css_ordinal - 1) if family == "maru" and css_ordinal <= 20 else f"({css_ordinal})"
                item.insert(0, marker + " ")
            elif listing.name == "ol":
                ordinal = int(item.get("value", ordinal + 1))
                style = listing.get("type", "1")
                marker = str(ordinal)
                if style.lower() == "i":
                    units = ("", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix")
                    tens = ("", "x", "xx", "xxx", "xl", "l", "lx", "lxx", "lxxx", "xc")
                    hundreds = ("", "c", "cc", "ccc", "cd", "d", "dc", "dcc", "dccc", "cm")
                    marker = "m" * (ordinal // 1000) + hundreds[(ordinal // 100) % 10] + tens[(ordinal // 10) % 10] + units[ordinal % 10]
                    if style == "I":
                        marker = marker.upper()
                elif style.lower() == "a":
                    import string
                    alphabet = string.ascii_uppercase if style == "A" else string.ascii_lowercase
                    number, marker = ordinal, ""
                    while number:
                        number, remainder = divmod(number - 1, 26)
                        marker = alphabet[remainder] + marker
                item.insert(0, f"{marker}. ")
    for tag in reversed(soup.select("sup, sub, .ol, .dol, .frac, .root")):
        classes = tag.get("class", [])
        if tag.name in ("sup", "sub"):
            mapping = SUPERSCRIPT_MAP if tag.name == "sup" else SUBSCRIPT_MAP
            raw = tag.get_text()
            if any(c not in mapping for c in raw):
                text = ("^" if tag.name == "sup" else "_") + f"({raw})"
            else:
                text = "".join(mapping[c] for c in raw)
        elif "frac" in classes:
            children = tag.find_all(recursive=False)
            if len(children) == 2:
                numerator, denominator = (child.get_text() for child in children)
            elif len(children) == 1:
                before = "".join(str(x) for x in children[0].previous_siblings)
                after = "".join(str(x) for x in children[0].next_siblings)
                numerator, denominator = (children[0].get_text(), after) if children[0].get_text() else (before, after)
            else:
                raise ValueError("未対応の分数表記")
            text = f"({numerator})/({denominator})"
        elif "root" in classes:
            text = f"√({tag.get_text()})"
        else:
            text = "".join(c + ("̅̅" if "dol" in classes else "̅") for c in tag.get_text())
        tag.replace_with(text)
    return compact(soup.get_text())


def source_images(nodes, url: str) -> list[str]:
    urls = []
    for node in nodes:
        if node is None:
            continue
        for image in node.select("img"):
            for attribute in ("data-src", "data-lazy-src", "src"):
                value = image.get(attribute, "").strip()
                if not value or is_placeholder_image_url(value):
                    continue
                source = urljoin(url, value)
                if source not in urls:
                    urls.append(source)
    return urls


def image_bindings(nodes, references: list[str], prefix: str, url: str) -> list[tuple[str, str]]:
    sources = source_images(nodes, url)
    if len(sources) != len(references):
        raise ValueError("画像参照数が取得元と一致しません")
    pairs = []
    for number, (source, reference) in enumerate(zip(sources, references), 1):
        extension = Path(urlparse(source).path).suffix.lower()
        if extension not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
            extension = ".png"
        filename = unquote(urlparse(reference).path).rsplit("/", 1)[-1]
        if prefix and filename != f"{prefix}_img{number:02d}{extension}":
            raise ValueError("画像参照の問題・用途・順序が取得元と一致しません")
        pairs.append((source, reference))
    return pairs


def audit_explanation(node, record: dict, url: str, prefix: str, *, afternoon: bool) -> list[str]:
    failures = []
    text = visible(node, preserve_choice_labels=afternoon)
    if afternoon and "この設問の解説はまだありません" in text:
        text = ""
    snippets = record.get("explanation_choice_snippets", [])
    saved_prefix = compact("".join(record.get("explanation_common_prefix", [])))
    saved_summary = compact("".join(record.get("explanation_common_summary", [])))
    marker_indices = {"lia": 0, "lii": 1, "liu": 2, "lie": 3}
    labeled = [(next(marker_indices[c] for c in li.get("class", []) if c in marker_indices), li)
               for li in node.select("li") if any(c in marker_indices for c in li.get("class", []))] if node and not afternoon else []
    if labeled and len(labeled) == len(record["choiceTextList"]) and {i for i, _ in labeled} == set(range(len(snippets))):
        # 同じ問のア・イ等の記号で照合し、DOM順が変わっても別選択肢へ混入させない。
        if any(visible(li) != compact("".join(snippets[i])) for i, li in labeled):
            failures.append("explanation_choice_binding")
        saved = saved_prefix + "".join(compact("".join(snippets[i])) for i, _ in labeled) + saved_summary
        if text != saved:
            failures.append("explanation_text")
    else:
        saved = saved_prefix + "".join(compact("".join(values)) for values in snippets) + saved_summary
        fallback = not saved_prefix and not saved_summary and len(snippets) == len(record["choiceTextList"]) and all(compact("".join(values)) == text for values in snippets)
        if text != saved and not fallback:
            failures.append("explanation_text")
    sources = source_images([node], url)
    if sources != record.get("explanationImageSourceUrls", []):
        failures.append("explanation_image_sources")
    try:
        image_bindings([node], record.get("explanationImageStorageUrls", []), prefix, url)
    except ValueError:
        failures.append("explanation_image_binding")
    return failures


def audit_page(html: str, records: list[dict], url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    failures = []
    if not records:
        return ["missing_page_records"]
    h2 = soup.select_one("h2")
    heading = h2.get_text() if h2 else ""
    year = re.search(r"(令和|平成)(元|\d+)年", heading)
    expected_year = (2018 if year[1] == "令和" else 1988) + (1 if year[2] == "元" else int(year[2])) if year else None
    if any(record.get("examYear") != expected_year for record in records):
        failures.append("exam_year")
    def image_count(nodes) -> int:
        return len(source_images(nodes, url))
    if "/pm" not in url:
        if len(records) != 1:
            return failures + ["single_page_record_count"]
        record = records[0]
        if image_count([soup.select_one("#mondai")]) != len(record.get("questionImageStorageUrls", [])):
            failures.append("question_image_count")
        if visible(soup.select_one("#mondai")) != compact(record["questionBodyText"]):
            failures.append("question_text")
        choices = soup.select("ul.selectList > li")
        if len(choices) == 1 and len(choices[0].select("button.selectBtn")) > 1:
            texts = [button.get_text(strip=True) for button in choices[0].select("button.selectBtn")]
        else:
            texts = [visible(choice) or (choice.select_one("button.selectBtn").get_text(strip=True) if choice.select_one("button.selectBtn") else "") for choice in choices]
        if texts != [compact(text) for text in record["choiceTextList"]]:
            failures.append("choice_text_order")
        saved_images = record.get("originalQuestionChoiceImageUrls", [])
        expected_image_counts = [image_count([choice]) for choice in choices]
        if len(choices) == 1 and len(markers := choices[0].select("button.selectBtn")) > 1:
            expected_image_counts *= len(markers)
        if expected_image_counts != [len(refs) for refs in saved_images]:
            failures.append("choice_image_count")
        markers = [button.get_text(strip=True) for button in soup.select("ul.selectList button.selectBtn")]
        answer_markers = [span.get_text(strip=True) for span in soup.select(".answerBox #answerChar")]
        expected_answers = sorted({markers.index(marker) + 1 for marker in answer_markers if marker in markers})
        if not expected_answers or expected_answers != record["answer_result_inferred_correct_choice_numbers"]:
            failures.append("answer")
        explanation = soup.select_one("#kaisetsu")
        failures.extend(audit_explanation(explanation, record, url, f"q{record['public_question_id']}_exp" if record.get("public_question_id") else "", afternoon=False))
        return failures

    selects = []
    for box in soup.select("div.inputAnswerBox"):
        seen = {}
        for select in box.select("select"):
            key_match = re.search(r"([a-z]+)$", select.get("name", ""))
            key = key_match[1] if key_match else "main"
            if key in seen:
                if visible(select) != visible(seen[key]):
                    failures.append("afternoon_repeated_answer_choices")
            else:
                seen[key] = select
                selects.append(select)
    if len(selects) != len(records):
        failures.append("afternoon_select_count")
    common = []
    common_nodes = []
    qno = soup.select_one("h3.qno")
    if qno:
        for block in qno.find_all_next("div", class_="mondai"):
            if block.find_previous("h3", id=re.compile(r"^s\d+$")) or block.select_one('h3[id^="s"]'):
                break
            common_nodes.append(block)
            common.append(visible(block))
    if not common:
        failures.append("afternoon_common_missing")
    by_id = {record["source_question_id"]: record for record in records}
    for select in selects:
        box = select.find_parent("div", class_="inputAnswerBox")
        statement = box.find_previous("div", class_="mondai")
        section = box.find_previous("h3", id=re.compile(r"^s\d+$"))
        section_text = section.get_text() if section else ""
        section_number = re.search(r"設問\s*(\d+)", section_text)
        sub_number = re.match(r"\((\d+)\)", visible(statement))
        blank = re.search(r"([a-z]+)$", select.get("name", ""))
        key = blank[1] if blank else "main"
        suffix = f":setumon{section_number[1] if section_number else ''}:{sub_number[1] if sub_number else ''}:{key}:{url}"
        matches = [record for source_id, record in by_id.items() if source_id.endswith(suffix)]
        if len(matches) != 1:
            failures.append(f"afternoon_identity:{suffix}")
            continue
        record = matches[0]
        choice_block = box.find_previous("div", class_=lambda value: value and "select" in value.split())
        expected_images = image_count(common_nodes) + image_count([choice_block, statement])
        if expected_images != len(record.get("questionImageStorageUrls", [])):
            failures.append("afternoon_image_count")
        body = "".join(common)
        section_block = section.find_parent("div", class_="mondai") if section else None
        if section_block is not None and section_block is not statement:
            body += visible(section_block)
        elif section is not None and section_block is None:
            body += visible(section)
        body += visible(statement)
        if body != compact(record["questionBodyText"]):
            failures.append("afternoon_body")
        options = [option.get_text(" ", strip=True) for option in select.select("option") if option.get_text(strip=True) not in ("", "-")]
        parts = [re.split(r"\s+", option, maxsplit=1) for option in options]
        if [compact(part[1] if len(part) == 2 else part[0]) for part in parts] != [compact(text) for text in record["choiceTextList"]]:
            failures.append("afternoon_choices")
        # 解答欄と次の設問の間のDOMだけを検査し、別設問への後方検索を防ぐ。
        following = []
        for tag in box.find_all_next():
            if "inputAnswerBox" in tag.get("class", []) or "mondai" in tag.get("class", []) or (tag.name == "h3" and re.match(r"(?:設問|問)\s*[0-9０-９]+", tag.get_text(strip=True))):
                break
            following.append(tag)
        answer_boxes = [tag for tag in following if "answerChars" in tag.get("class", [])]
        explanation_nodes = [tag for tag in following if "kaisetsu" in tag.get("class", [])]
        answer_box = answer_boxes[0] if len(answer_boxes) == 1 else None
        answer_markers = [span.get_text(strip=True) for span in answer_box.select("span") if (re.search(r"([a-z]+)$", span.get("id", "")) or [None, "main"])[1] == key] if answer_box else []
        numbers = sorted({index + 1 for index, part in enumerate(parts) if part[0] in answer_markers})
        if not numbers or numbers != record["answer_result_inferred_correct_choice_numbers"]:
            failures.append("afternoon_answer")
        if len(explanation_nodes) > 1:
            failures.append("afternoon_explanation_ambiguous")
        explanation = explanation_nodes[0] if len(explanation_nodes) == 1 else None
        pm_number = int(re.search(r"pm(\d+)", url)[1])
        prefix = f"pm{record['list_group_id']}_q{pm_number}_s{section_number[1] if section_number else 'x'}_{sub_number[1] if sub_number else 'x'}_exp"
        failures.extend(audit_explanation(explanation, record, url, prefix, afternoon=True))
    return failures


def page_image_bindings(html: str, records: list[dict], url: str) -> list[tuple[str, str]]:
    """用途別のHTML領域と保存名を照合し、画像bytesの検査対象を列挙する。"""
    soup = BeautifulSoup(html, "html.parser")
    pairs = []
    if "/pm" not in url:
        record = records[0]
        public = record["public_question_id"]
        pairs.extend(image_bindings([soup.select_one("#mondai")], record.get("questionImageStorageUrls", []), f"q{public}_q", url))
        choices = soup.select("ul.selectList > li")
        shared = len(choices) == 1 and len(choices[0].select("button.selectBtn")) > 1
        for number, refs in enumerate(record.get("originalQuestionChoiceImageUrls", []), 1):
            pairs.extend(image_bindings([choices[0] if shared else choices[number - 1]], refs, f"q{public}_choices" if shared else f"q{public}_c{number:02d}", url))
        pairs.extend(image_bindings([soup.select_one("#kaisetsu")], record.get("explanationImageStorageUrls", []), f"q{public}_exp", url))
        return pairs
    common = []
    for block in soup.select("div.mondai"):
        if block.find_previous("h3", id=re.compile(r"^s\d+$")) or block.select_one('h3[id^="s"]'):
            break
        common.append(block)
    for record in records:
        match = re.search(r":pm(\d+):setumon([^:]*):([^:]*):([^:]*):", record["source_question_id"])
        pm, section, sub, key = match.groups()
        located = []
        for box in soup.select("div.inputAnswerBox"):
            heading = box.find_previous("h3", id=re.compile(r"^s\d+$"))
            found_section = re.search(r"設問\s*(\d+)", heading.get_text()) if heading else None
            statement = box.find_previous("div", class_="mondai")
            found_sub = re.match(r"\((\d+)\)", visible(statement))
            keys = {(re.search(r"([a-z]+)$", sel.get("name", "")) or [None, "main"])[1] for sel in box.select("select")}
            if (found_section[1] if found_section else "") == section and (found_sub[1] if found_sub else "") == sub and key in keys:
                located.append((box, statement))
        if len(located) != 1:
            raise ValueError("画像の設問対応が一意ではありません")
        box, statement = located[0]
        choice = box.find_previous("div", class_=lambda value: value and "select" in value.split())
        common_count = len(source_images(common, url))
        references = record.get("questionImageStorageUrls", [])
        pairs.extend(image_bindings(common, references[:common_count], f"pm{record['list_group_id']}_q{pm}_common", url))
        prefix = f"pm{record['list_group_id']}_q{pm}_s{section or 'x'}_{sub or 'x'}"
        pairs.extend(image_bindings([choice, statement], references[common_count:], prefix, url))
        explanation = None
        for tag in box.find_all_next():
            if "inputAnswerBox" in tag.get("class", []) or "mondai" in tag.get("class", []) or (tag.name == "h3" and re.match(r"(?:設問|問)\s*[0-9０-９]+", tag.get_text(strip=True))):
                break
            if "kaisetsu" in tag.get("class", []):
                explanation = tag
                break
        pairs.extend(image_bindings([explanation], record.get("explanationImageStorageUrls", []), prefix + "_exp", url))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qualification")
    parser.add_argument("groups", nargs="*")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--verify-live-images", action="store_true", help="全参照画像を取得元から再読込し保存bytesとSHA256照合する")
    args = parser.parse_args()
    preset = load_scrape_preset(args.qualification)
    root = args.output_dir / args.qualification
    inventory = root / "verification" / "dojo" / "inventory.html.gz"
    if not inventory.exists():
        check_live_inventory(preset, args.output_dir)
    with gzip.open(inventory, "rt", encoding="utf-8") as evidence:
        verify_inventory_html(evidence.read(), {build_list_first_page_url(preset, group) for group in preset.list_group_ids}, "https://www.sg-siken.com/sgkakomon.php")
    results, failures, all_ids, all_source_ids = [], [], [], []
    session = create_http_session() if args.verify_live_images else None
    live_hashes = {}
    for group in args.groups or preset.list_group_ids:
        evidence = root / "verification" / "dojo" / group
        records = [record for path in sorted((root / "questions_json" / group / "00_source").glob("*.json")) for record in json.loads(path.read_text())["question_bodies"]]
        by_url = {}
        for record in records:
            by_url.setdefault(record["question_url"], []).append(record)
            all_ids.append(record["public_question_id"])
            all_source_ids.append(record["source_question_id"])
        list_url = build_list_first_page_url(preset, group)
        with gzip.open(evidence / "index.html.gz", "rt", encoding="utf-8") as source:
            index = BeautifulSoup(source.read(), "html.parser")
        urls = {urljoin(list_url, a["href"]) for a in index.select("a[href]") if re.fullmatch(r"(?:q\d+|[ab]\d+|pm\d+|am[12]_\d+|\d{2})\.html", a["href"])}
        group_failures = []
        if urls != set(by_url):
            group_failures.append({"kind": "url_inventory", "missing": sorted(urls - set(by_url)), "extra": sorted(set(by_url) - urls)})
        bindings = {}
        for url in sorted(urls):
            with gzip.open(evidence / (urlparse(url).path.rsplit("/", 1)[-1] + ".gz"), "rt", encoding="utf-8") as source:
                html = source.read()
                errors = audit_page(html, by_url.get(url, []), url)
            try:
                for image_source, reference in page_image_bindings(html, by_url.get(url, []), url):
                    if reference in bindings and bindings[reference] != image_source:
                        raise ValueError("同じ保存画像に異なる取得元画像が割り当てられています")
                    bindings[reference] = image_source
            except (ValueError, IndexError, KeyError, AttributeError) as error:
                errors.append(f"image_binding:{error}")
            if errors:
                group_failures.append({"url": url, "errors": errors})
        images = {reference for record in records for reference in record.get("questionImageStorageUrls", []) + record.get("explanationImageStorageUrls", []) + [reference for refs in record.get("originalQuestionChoiceImageUrls", []) for reference in refs]}
        image_proofs = []
        for reference in images:
            filename = unquote(urlparse(reference).path).rsplit("/", 1)[-1]
            path = root / "question_images" / group / filename
            try:
                with Image.open(path) as picture:
                    picture.verify()
                local_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                source_url = bindings[reference]
                if args.verify_live_images:
                    if source_url not in live_hashes:
                        data = download_image_with_retry(session, source_url)
                        if data is None:
                            raise ValueError("取得元画像の再読込に失敗しました")
                        live_hashes[source_url] = hashlib.sha256(data).hexdigest()
                    if local_hash != live_hashes[source_url]:
                        raise ValueError("取得元画像と保存画像のSHA256が一致しません")
                image_proofs.append({"sourceUrl": source_url, "reference": reference, "sha256": local_hash})
            except Exception as error:
                group_failures.append({"image": filename, "error": str(error)})
        results.append({"group": group, "questionCount": len(records), "pageCount": len(urls), "imageCount": len(images), "imageProofs": image_proofs, "failures": group_failures})
        failures.extend(group_failures)
        print(f"{group}: questions={len(records)} pages={len(urls)} images={len(images)} failures={len(group_failures)}", flush=True)
    duplicates = [key for key, count in Counter(all_ids).items() if count > 1]
    if duplicates:
        failures.append({"duplicatePublicIds": duplicates})
    source_duplicates = [key for key, count in Counter(all_source_ids).items() if count > 1]
    if source_duplicates:
        failures.append({"duplicateSourceIds": source_duplicates})
    report = {"status": "failed" if failures else "passed", "completedAt": datetime.now(timezone.utc).isoformat(), "liveImageHashesVerified": bool(args.verify_live_images and not failures), "liveImageUrlCount": len(live_hashes), "questionCount": len(all_ids), "listGroupIds": [result["group"] for result in results], "groups": results, "failures": failures}
    suffix = "_" + "_".join(args.groups) if args.groups else ""
    report_path = root / "reports" / f"siken_acquisition_audit{suffix}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"report: {report_path}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
