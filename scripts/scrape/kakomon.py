from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrape.common import (
    choice_truth_labels,
    create_http_session,
    download_image_with_retry,
    extract_text_with_subsup,
    fetch_html_text,
    guess_image_extension,
    load_local_secure_env,
    make_canonical_question_key,
    make_public_question_id,
    make_storage_url,
    make_url_source_question_id,
    normalize_inline_text,
    prepare_output_dirs,
    source_site_from_url,
)


QUESTION_PATH_RE = re.compile(
    r"^/(?P<site_qualification>[a-z0-9-]+)/"
    r"(?P<source_group>\d{4}-[12])-(?P<section>\d{2})-(?P<number>\d{3})/$"
)
QUESTIONS_PER_SECTION = 10
INCORRECT_PATTERNS = (
    "適切でない",
    "適当でない",
    "正しくない",
    "誤っている",
    "誤り",
    "定められていない",
    "該当しない",
    "含まれない",
)


@dataclass(frozen=True)
class ParsedQuestion:
    site_qualification: str
    source_group_id: str
    section_number: int
    question_number: int
    question_url: str
    title: str
    question_text: str
    choices: tuple[str, ...]
    correct_choice_number: int
    category: str
    topic: str
    explanation_items: tuple[str, ...]
    question_image_urls: tuple[str, ...]
    choice_image_urls: tuple[tuple[str, ...], ...]


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _image_urls(element: Tag | None, *, page_url: str) -> tuple[str, ...]:
    if element is None:
        return ()
    return _unique(
        urljoin(page_url, str(image.get("data-src") or image.get("src") or "").strip())
        for image in element.select("img")
    )


def _table_value(soup: BeautifulSoup, label: str) -> str:
    for row in soup.select("table.question-information tr"):
        heading = row.find("th")
        value = row.find("td")
        if heading and value and normalize_inline_text(heading.get_text(" ", strip=True)) == label:
            return normalize_inline_text(value.get_text(" ", strip=True))
    return ""


def _path_match(page_url: str) -> re.Match[str]:
    match = QUESTION_PATH_RE.fullmatch(urlparse(page_url).path)
    if match is None:
        raise ValueError(f"kako-mon.comの問題URLではありません: {page_url}")
    return match


def discover_groups(html: str, *, page_url: str) -> dict[str, list[str]]:
    """資格トップの実リンクから試験回と科目開始URLを列挙する。"""
    slug = urlparse(page_url).path.strip("/")
    groups: dict[str, list[str]] = {}
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        url = urljoin(page_url, anchor["href"])
        match = QUESTION_PATH_RE.fullmatch(urlparse(url).path)
        if match and match["site_qualification"] == slug:
            urls = groups.setdefault(match["source_group"], [])
            if url not in urls:
                urls.append(url)
    if not groups:
        raise ValueError("資格トップから試験回を取得できません")
    return groups


def extract_question_text(element: Tag) -> str:
    """サイト固有の分数と小字の添字を標準HTMLへ変換し、既存の共通抽出を使う。"""
    copy = BeautifulSoup(str(element), "html.parser")
    for small in copy.select("small"):
        small.name = "sub"
    for fraction in reversed(copy.select(".fraction")):
        parts = fraction.find_all(recursive=False)
        if len(parts) != 2 or "numerator" not in parts[0].get("class", []):
            raise ValueError("未対応の分数構造です")
        numerator, denominator = (normalize_inline_text(extract_text_with_subsup(part)) for part in parts)
        fraction.replace_with(f"({numerator})/({denominator})")
    return normalize_inline_text(extract_text_with_subsup(copy).replace("\n", " "))


def parse_question_page(html: str, *, page_url: str) -> ParsedQuestion:
    match = _path_match(page_url)
    question_number = int(match.group("number"))
    expected_section = (question_number - 1) // QUESTIONS_PER_SECTION + 1
    section_number = int(match.group("section"))
    if section_number != expected_section:
        raise ValueError(f"URLの科目番号と問題番号が一致しません: {page_url}")

    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".question-container")
    if container is None:
        raise ValueError(f"問題要素がありません: {page_url}")
    body = container.select_one(".question-body")
    choices = container.select(".choice-body[data-cnum]")
    footer = container.select_one(".question-footer[data-corr]")
    answer = container.select_one(".answer-body")
    if body is None or footer is None or answer is None:
        raise ValueError(f"問題文・正答要素が不足しています: {page_url}")

    choice_numbers = [int(str(choice.get("data-cnum") or "0")) for choice in choices]
    if choice_numbers != [1, 2, 3, 4, 5]:
        raise ValueError(f"選択肢は1から5の5件である必要があります: {page_url}")
    choice_texts = tuple(
        re.sub(
            r"^[１-５1-5]\s*[：:]\s*",
            "",
            extract_question_text(choice),
        )
        for choice in choices
    )

    correct_choice_number = int(str(footer.get("data-corr") or "0"))
    answer_match = re.search(r"答\s*[：:]\s*([１-５1-5])", answer.get_text(" ", strip=True))
    if answer_match is None:
        raise ValueError(f"正答表示を取得できません: {page_url}")
    answer_number = int(answer_match.group(1).translate(str.maketrans("１２３４５", "12345")))
    marked = [
        int(str(choice.get("data-cnum")))
        for choice in choices
        if "choice-correct" in (choice.get("class") or [])
    ]
    if correct_choice_number != answer_number or marked != [correct_choice_number]:
        raise ValueError(
            "正答指定が一致しません: "
            f"data={correct_choice_number} answer={answer_number} marked={marked} url={page_url}"
        )

    heading = soup.select_one("h1.entry-title") or soup.find("h1")
    title = normalize_inline_text(heading.get_text(" ", strip=True)) if heading else ""
    label = re.search(r"(?:-([AB]))?-問\s*(\d+)\s*$", title)
    displayed_number = int(label[2]) if label else -1
    if label and label[1] == "B":
        displayed_number += 20
    if displayed_number != question_number:
        raise ValueError(f"ページ題名と問題番号が一致しません: {title} / {page_url}")
    # supplementary statements/tables belong to the question, not the choices.
    subs = container.select_one(".question-subs")
    body_text = extract_question_text(body)
    subs_text = extract_question_text(subs) if subs else ""
    question_text = "\n".join(text for text in (body_text, subs_text) if text)

    explanation_items = tuple(
        normalize_inline_text(item.get_text(" ", strip=True))
        for item in soup.select(".explanation-wrap .explanation-body")
        if normalize_inline_text(item.get_text(" ", strip=True))
    )
    question_images = _unique(
        [
            *_image_urls(body, page_url=page_url),
            *_image_urls(container.select_one(".question-subs"), page_url=page_url),
        ]
    )
    choice_images = tuple(_image_urls(choice, page_url=page_url) for choice in choices)
    return ParsedQuestion(
        site_qualification=match.group("site_qualification"),
        source_group_id=match.group("source_group"),
        section_number=section_number,
        question_number=question_number,
        question_url=page_url,
        title=title,
        question_text=question_text,
        choices=choice_texts,
        correct_choice_number=correct_choice_number,
        category=_table_value(soup, "カテゴリ"),
        topic=_table_value(soup, "出題分野"),
        explanation_items=explanation_items,
        question_image_urls=question_images,
        choice_image_urls=choice_images,
    )


def question_url(site_qualification: str, source_group_id: str, question_number: int) -> str:
    section = (question_number - 1) // QUESTIONS_PER_SECTION + 1
    return (
        f"https://kako-mon.com/{site_qualification}/"
        f"{source_group_id}-{section:02d}-{question_number:03d}/"
    )


def determine_question_intent(text: str) -> str:
    return "select_incorrect" if any(pattern in text for pattern in INCORRECT_PATTERNS) else "select_correct"


def _image_filename(
    qualification_code: str,
    source_group_id: str,
    question_number: int,
    image_url: str,
) -> str:
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
    return (
        f"kakomon_{qualification_code}_{source_group_id}_q{question_number:03d}_"
        f"{digest}{guess_image_extension(image_url)}"
    )


def _save_images(
    urls: Iterable[str],
    *,
    session: requests.Session,
    staged_image_dir: Path,
    qualification_code: str,
    source_group_id: str,
    question_number: int,
) -> list[str]:
    storage_urls: list[str] = []
    for image_url in urls:
        filename = _image_filename(
            qualification_code,
            source_group_id,
            question_number,
            image_url,
        )
        path = staged_image_dir / filename
        if not path.is_file():
            image_bytes = download_image_with_retry(session, image_url)
            if not image_bytes:
                raise ValueError(f"画像として取得できません: {image_url}")
            with path.open("xb") as output:
                output.write(image_bytes)
        storage_urls.append(make_storage_url(filename, qualification_code))
    return storage_urls


def build_source_record(
    parsed: ParsedQuestion,
    *,
    qualification_code: str,
    qualification_name: str,
    output_list_group_id: str,
    session: requests.Session,
    staged_image_dir: Path,
) -> dict[str, Any]:
    source_question_id = make_url_source_question_id(qualification_code, parsed.question_url)
    canonical_key = make_canonical_question_key(
        qualification_code=qualification_code,
        exam_occurrence_id=output_list_group_id,
        question_number=parsed.question_number,
    )
    if not canonical_key:
        raise ValueError(f"canonical keyを生成できません: {parsed.question_url}")
    public_question_id = make_public_question_id(canonical_key)
    source_public_question_id = make_public_question_id(source_question_id)
    question_intent = determine_question_intent(parsed.question_text)
    question_image_storage_urls = _save_images(
        parsed.question_image_urls,
        session=session,
        staged_image_dir=staged_image_dir,
        qualification_code=qualification_code,
        source_group_id=parsed.source_group_id,
        question_number=parsed.question_number,
    )
    choice_image_storage_urls = [
        _save_images(
            urls,
            session=session,
            staged_image_dir=staged_image_dir,
            qualification_code=qualification_code,
            source_group_id=parsed.source_group_id,
            question_number=parsed.question_number,
        )
        for urls in parsed.choice_image_urls
    ]
    snippets = (
        [[item] for item in parsed.explanation_items]
        if len(parsed.explanation_items) == len(parsed.choices)
        else [[] for _ in parsed.choices]
    )
    common_explanation = [] if any(snippets) else list(parsed.explanation_items)
    exam_year_match = re.match(r"(\d{4})", output_list_group_id)
    return {
        "questionBodyText": parsed.question_text,
        "examLabel": " ".join(
            part
            for part in (qualification_name, output_list_group_id, parsed.category)
            if part
        ),
        "questionLabel": f"問{parsed.question_number}",
        "choiceTextList": list(parsed.choices),
        "originalQuestionChoiceImageUrls": choice_image_storage_urls,
        "choiceImageSourceUrlsByChoice": [list(urls) for urls in parsed.choice_image_urls],
        "category": parsed.category or None,
        "sourceCategory": parsed.category or None,
        "sourceTopic": parsed.topic or None,
        "examYear": int(exam_year_match.group(1)) if exam_year_match else None,
        "examOccurrenceId": output_list_group_id,
        "list_group_id": output_list_group_id,
        "source_list_group_id": parsed.source_group_id,
        "question_url": parsed.question_url,
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "source_question_id": source_question_id,
        "source_public_question_id": source_public_question_id,
        "questionSourceSite": source_site_from_url(parsed.question_url),
        "canonical_question_key": canonical_key,
        "question_id_policy_key": "canonical-question-key:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": (
            "{qualification_code}:{exam_occurrence_id}:q{question_number}"
        ),
        "sourceUniqueKeys": [f"{canonical_key}:s{index:02d}" for index in range(1, 6)],
        "questionImageSourceUrls": list(parsed.question_image_urls),
        "questionImageStorageUrls": question_image_storage_urls,
        "questionIntent": question_intent,
        "correctChoiceText": choice_truth_labels(
            choice_count=5,
            correct_choice_numbers=[parsed.correct_choice_number],
            question_intent=question_intent,
        ),
        "explanation_common_prefix": common_explanation,
        "explanation_common_prefix_inferred_correct_choice": parsed.correct_choice_number,
        "explanation_common_summary": [],
        "explanation_choice_snippets": snippets,
        "answer_result_text": f"正解は {parsed.correct_choice_number} です。",
        "answer_result_inferred_correct_choice_numbers": [parsed.correct_choice_number],
    }


def validate_source_records(records: list[dict[str, Any]], *, expected_count: int) -> None:
    if len(records) != expected_count:
        raise ValueError(f"取得件数が一致しません: actual={len(records)} expected={expected_count}")
    canonical_keys: set[str] = set()
    source_ids: set[str] = set()
    for expected_number, record in enumerate(records, start=1):
        choices = record.get("choiceTextList")
        choice_images = record.get("choiceImageSourceUrlsByChoice")
        correct_numbers = record.get("answer_result_inferred_correct_choice_numbers")
        if record.get("questionLabel") != f"問{expected_number}":
            raise ValueError(f"問番号が連続していません: expected={expected_number}")
        if not isinstance(choices, list) or len(choices) != 5:
            raise ValueError(f"選択肢件数が不正です: 問{expected_number}")
        if not isinstance(choice_images, list) or len(choice_images) != 5:
            raise ValueError(f"選択肢画像配列が不正です: 問{expected_number}")
        for index, choice in enumerate(choices):
            if not str(choice or "").strip() and not choice_images[index]:
                raise ValueError(f"本文も画像もない選択肢があります: 問{expected_number} 肢{index + 1}")
        if not isinstance(correct_numbers, list) or len(correct_numbers) != 1:
            raise ValueError(f"正答件数が不正です: 問{expected_number}")
        canonical_keys.add(str(record.get("canonical_question_key") or ""))
        source_ids.add(str(record.get("source_question_id") or ""))
    if "" in canonical_keys or len(canonical_keys) != expected_count:
        raise ValueError("canonical question keyが空又は重複しています")
    if "" in source_ids or len(source_ids) != expected_count:
        raise ValueError("source question IDが空又は重複しています")


def _records_by_source_id(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("question_bodies") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"既存00_sourceの形式が不正です: {path}")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        source_id = str(record.get("source_question_id") or "") if isinstance(record, dict) else ""
        if not source_id or source_id in result:
            raise ValueError(f"既存00_sourceのsource IDが空又は重複しています: {path}")
        result[source_id] = record
    return result


def _publish_staged_images(staged_image_dir: Path, image_dir: Path) -> tuple[int, int, int]:
    new_count = updated_count = unchanged_count = 0
    for staged_path in sorted(staged_image_dir.iterdir()):
        target = image_dir / staged_path.name
        staged_hash = hashlib.sha256(staged_path.read_bytes()).hexdigest()
        if not target.is_file():
            os.replace(staged_path, target)
            new_count += 1
            continue
        target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if target_hash == staged_hash:
            staged_path.unlink()
            unchanged_count += 1
            continue
        os.replace(staged_path, target)
        updated_count += 1
    return new_count, updated_count, unchanged_count


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            temporary_path = Path(output.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="kako-mon.comの過去問を取得する。")
    parser.add_argument("--qualification-code", default=os.environ.get("SCRAPER_QUALIFICATION_CODE", ""))
    parser.add_argument("--qualification-name", default=os.environ.get("SCRAPER_QUALIFICATION_NAME", ""))
    parser.add_argument("--list-url", default=os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL", ""))
    parser.add_argument("--output-list-group-id", default=os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID", ""))
    parser.add_argument("--output-dir", default=os.environ.get("SCRAPER_OUTPUT_DIR", str(Path.cwd() / "output")))
    parser.add_argument(
        "--expected-question-count",
        type=int,
        default=(
            int(os.environ["SCRAPER_EXPECTED_QUESTION_COUNT"])
            if os.environ.get("SCRAPER_EXPECTED_QUESTION_COUNT")
            else None
        ),
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=int(os.environ["SCRAPER_MAX_QUESTIONS"]) if os.environ.get("SCRAPER_MAX_QUESTIONS") else None,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_local_secure_env()
    args = parse_args(argv)
    if not args.qualification_code or not args.qualification_name:
        raise ValueError("qualification codeとnameを指定してください")
    if not args.expected_question_count or args.expected_question_count <= 0:
        raise ValueError("expected question countを正の整数で指定してください")
    list_match = _path_match(args.list_url)
    if list_match.group("number") != "001" or list_match.group("section") != "01":
        raise ValueError(f"list-urlは対象回の問1を指定してください: {args.list_url}")
    source_group_id = list_match.group("source_group")
    site_qualification = list_match.group("site_qualification")
    count = min(args.max_questions or args.expected_question_count, args.expected_question_count)

    json_dir_raw, image_dir_raw = prepare_output_dirs(
        args.output_dir,
        args.qualification_code,
        args.output_list_group_id,
        "00_source",
    )
    json_dir = Path(json_dir_raw)
    image_dir = Path(image_dir_raw)
    output_path = json_dir / f"question_{args.output_list_group_id}.json"
    if count != args.expected_question_count and output_path.exists():
        raise ValueError("既存00_sourceを部分取得で上書きできません")

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".kakomon-", dir=output_root) as temporary_dir:
        staged_image_dir = Path(temporary_dir) / "images"
        staged_image_dir.mkdir()
        evidence_dir = output_root / args.qualification_code / "verification" / "html" / source_group_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        def fetch_record(number: int) -> dict[str, Any]:
            url = question_url(site_qualification, source_group_id, number)
            with create_http_session() as question_session:
                question_session.headers["Connection"] = "close"
                html = fetch_html_text(question_session, url)
                with gzip.open(evidence_dir / f"{number:03d}.html.gz", "wt", encoding="utf-8") as evidence:
                    evidence.write(html)
                parsed = parse_question_page(html, page_url=url)
                record = build_source_record(
                    parsed,
                    qualification_code=args.qualification_code,
                    qualification_name=args.qualification_name,
                    output_list_group_id=args.output_list_group_id,
                    session=question_session,
                    staged_image_dir=staged_image_dir,
                )
            print(f"[FETCH] {source_group_id} {number}/{count} answer={parsed.correct_choice_number}")
            return record

        # Small fixed concurrency; each request retains common delay/retry handling.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            records = list(executor.map(fetch_record, range(1, count + 1)))

        validate_source_records(records, expected_count=count)
        existing = _records_by_source_id(output_path)
        current = {record["source_question_id"]: record for record in records}
        if existing and set(existing) != set(current):
            raise ValueError("既存00_sourceと再取得結果のsource ID集合が一致しません")
        changed_ids = sorted(
            source_id
            for source_id, record in current.items()
            if existing.get(source_id) != record
        )
        new_images, updated_images, unchanged_images = _publish_staged_images(
            staged_image_dir,
            image_dir,
        )
        payload = {
            "list_group_id": args.output_list_group_id,
            "source_list_group_id": source_group_id,
            "question_bodies": records,
        }
        _atomic_write_json(output_path, payload)

    report = {
        "status": "completed",
        "sourceSite": "kako-mon.com",
        "siteQualification": site_qualification,
        "sourceListGroupId": source_group_id,
        "outputListGroupId": args.output_list_group_id,
        "expectedQuestionCount": args.expected_question_count,
        "questionCount": len(records),
        "answerCount": sum(bool(record["answer_result_inferred_correct_choice_numbers"]) for record in records),
        "choiceCount": sum(len(record["choiceTextList"]) for record in records),
        "questionImageCount": sum(len(record["questionImageSourceUrls"]) for record in records),
        "choiceImageCount": sum(
            sum(len(images) for images in record["choiceImageSourceUrlsByChoice"])
            for record in records
        ),
        "newSourceRecordCount": len(records) if not existing else 0,
        "updatedSourceRecordCount": len(changed_ids) if existing else 0,
        "changedSourceQuestionIds": changed_ids if existing else [],
        "newImageCount": new_images,
        "updatedImageCount": updated_images,
        "verifiedImageCount": unchanged_images,
        "sourceFile": str(output_path),
        "sourceFileSha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
    report_path = (
        Path(args.output_dir)
        / "question_review_console"
        / "scrape_runs"
        / args.qualification_code
        / f"{args.output_list_group_id}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(report_path, report)
    print(f"[DONE] {args.output_list_group_id}: {len(records)}問 -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
