from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from scripts.scrape.common import (
    create_http_session,
    download_and_save_images,
    extract_image_urls_from_element,
    load_local_secure_env,
    make_public_question_id,
    make_storage_url,
    normalize_inline_text,
    normalize_question_body_text,
    prepare_output_dirs,
    save_question_body_chunks,
    slow_down,
)


QUALIFICATION_CODE = "kougai"
QUALIFICATION_NAME = "公害防止管理者"
LIST_FIRST_PAGE_URL = "https://yaku-tik.com/kougai/category/kako/kako-r7/"
JSON_SUBDIR_NAME = "00_source"
MAX_QUESTIONS: int | None = None
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_LIST_GROUP_ID: str | None = None
IMAGE_OUTPUT_DIR: str | None = None

FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
FULLWIDTH_ALPHA_TRANS = str.maketrans("ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ", "abcdefghijklmnopqrstuvwxyz")
CIRCLED_NUMBER_MAP = {
    "①": 1,
    "②": 2,
    "③": 3,
    "④": 4,
    "⑤": 5,
    "⑥": 6,
    "⑦": 7,
    "⑧": 8,
    "⑨": 9,
    "⑩": 10,
    "⑴": 1,
    "⑵": 2,
    "⑶": 3,
    "⑷": 4,
    "⑸": 5,
    "⑹": 6,
    "⑺": 7,
    "⑻": 8,
    "⑼": 9,
    "⑽": 10,
}
NUMBER_TO_ALPHA = [chr(ord("a") + i) for i in range(26)]
BLOCK_TAGS = {
    "p",
    "div",
    "li",
    "ol",
    "ul",
    "table",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "blockquote",
}
QUALIFICATION_TEXT_SUBJECTS = {
    "1": "公害総論",
    "2": "大気概論",
    "3": "大気特論",
    "4": "ばいじん・粉じん特論",
    "5": "大気有害物質特論",
    "6": "大規模大気特論",
}
QUALIFICATION_TEXT_BLANK_IMAGE_LABELS = {
    "sikaku": "□",
    "sikakua": "ア",
    "sikakui": "イ",
    "sikakuu": "ウ",
    "sikakue": "エ",
    "sikakuo": "オ",
    "sikakuka": "カ",
    "sikakuki": "キ",
    "sikakuku": "ク",
    "sikakuke": "ケ",
    "sikakuko": "コ",
}


@dataclass(frozen=True)
class ParsedPage:
    question: dict
    source_image_urls: list[str]


@dataclass(frozen=True)
class CombinationData:
    blank_labels: list[str]
    candidate_terms: dict[str, str]
    rows: list[list[str]]
    row_numbers: list[int]


def apply_runtime_overrides_from_env() -> None:
    global QUALIFICATION_CODE
    global QUALIFICATION_NAME
    global LIST_FIRST_PAGE_URL
    global JSON_SUBDIR_NAME
    global MAX_QUESTIONS
    global OUTPUT_DIR
    global OUTPUT_LIST_GROUP_ID

    qualification_code = os.environ.get("SCRAPER_QUALIFICATION_CODE")
    qualification_name = os.environ.get("SCRAPER_QUALIFICATION_NAME")
    list_first_page_url = os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL")
    json_subdir_name = os.environ.get("SCRAPER_JSON_SUBDIR_NAME")
    max_questions = os.environ.get("SCRAPER_MAX_QUESTIONS")
    output_dir = os.environ.get("SCRAPER_OUTPUT_DIR")
    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID")

    if qualification_code:
        QUALIFICATION_CODE = qualification_code
    if qualification_name:
        QUALIFICATION_NAME = qualification_name
    if list_first_page_url:
        LIST_FIRST_PAGE_URL = list_first_page_url
    if json_subdir_name:
        JSON_SUBDIR_NAME = json_subdir_name
    if max_questions is not None:
        MAX_QUESTIONS = int(max_questions) if max_questions else None
    if output_dir:
        OUTPUT_DIR = output_dir
    if output_list_group_id:
        OUTPUT_LIST_GROUP_ID = output_list_group_id


def fetch_html_text(http_session, target_url: str) -> str:
    for retry_index in range(3):
        try:
            slow_down(0.5, 0.5)
            response = http_session.get(target_url, timeout=20)
            response.raise_for_status()
            if "qualification-text.com" in target_url:
                response.encoding = response.apparent_encoding or response.encoding or "utf-8"
            return response.text
        except Exception as fetch_error:  # noqa: PERF203
            print(f"[WARN] fetch failed ({target_url}): {fetch_error}")
            if retry_index == 2:
                raise
            slow_down(1.5, 1.5)
    raise RuntimeError("Unexpected fetch retry state")


def normalize_digits(text: str) -> str:
    return (text or "").translate(FULLWIDTH_DIGIT_TRANS)


def normalize_alpha_label(text: str) -> str:
    normalized = (text or "").translate(FULLWIDTH_ALPHA_TRANS).strip().lower()
    normalized = normalized.strip("()（）:：.．、, ")
    return normalized


def clean_text(text: str) -> str:
    normalized = (text or "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def image_label_text(img: Tag) -> str:
    alt = str(img.get("alt") or "").strip()
    if alt:
        return alt
    stem = Path(str(img.get("src") or "")).stem.lower()
    return QUALIFICATION_TEXT_BLANK_IMAGE_LABELS.get(stem, "")


def render_text(node: Tag | NavigableString | None) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if node.name in {"script", "style", "iframe"}:
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "img":
        return image_label_text(node)

    rendered = "".join(render_text(child) for child in node.children)
    if node.name in BLOCK_TAGS:
        return f"{rendered}\n"
    return rendered


def direct_child_tags(parent: Tag) -> list[Tag]:
    return [child for child in parent.children if isinstance(child, Tag)]


def node_text(node: Tag | NavigableString | None) -> str:
    return clean_text(render_text(node))


def nodes_text(nodes: list[Tag]) -> str:
    return clean_text("\n".join(node_text(node) for node in nodes if node_text(node)))


def is_heading_noise_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_inline_text(text or ""))
    return compact in {"問題", "問", "解説", "解答"} or "解答（こちらをクリック）" in compact


def is_candidate_terms_node(node: Tag) -> bool:
    return bool(parse_candidate_terms_from_text(node_text(node)))


def parse_choice_number_token(text: str) -> int | None:
    stripped = normalize_digits((text or "").strip())
    if stripped in CIRCLED_NUMBER_MAP:
        return CIRCLED_NUMBER_MAP[stripped]
    if len(stripped) == 1 and stripped in CIRCLED_NUMBER_MAP:
        return CIRCLED_NUMBER_MAP[stripped]
    match = re.search(r"[（(]?\s*(\d{1,2})\s*[)）]?", stripped)
    if match:
        return int(match.group(1))
    return None


def parse_correct_answer_numbers(text: str) -> list[int]:
    normalized = normalize_digits(text or "")
    if re.fullmatch(r"\s*\d{1,2}\s*", normalized):
        return [int(normalized.strip())]
    circled = [value for marker, value in CIRCLED_NUMBER_MAP.items() if marker in normalized]
    if circled:
        return unique_ints(circled)

    scoped_match = re.search(r"(?:正解|解答)[^0-9０-９（(]*[（(]?\s*([0-9０-９,\s、]+)\s*[)）]?", normalized)
    if scoped_match:
        return unique_ints(int(part) for part in re.findall(r"\d+", scoped_match.group(1)))

    paren_numbers = [int(value) for value in re.findall(r"[（(]\s*(\d{1,2})\s*[)）]", normalized)]
    if paren_numbers:
        return unique_ints(paren_numbers[:1])
    return []


def unique_ints(values) -> list[int]:
    result: list[int] = []
    for value in values:
        number = int(value)
        if number not in result:
            result.append(number)
    return result


def build_answer_result_text(answer_numbers: list[int]) -> str:
    if not answer_numbers:
        return ""
    joined = ", ".join(str(number) for number in answer_numbers)
    return f"正解は {joined} です。"


def determine_question_intent(question_text: str) -> str | None:
    normalized = normalize_inline_text(question_text)
    if not normalized:
        return None
    if re.search(r"(ていない|でない|ではない|しない|該当しない|含まれない).*ものはどれか", normalized):
        return "select_incorrect"
    if re.search(r"(誤っている|誤り|不適当|不適切|正しくない|該当しない|含まれない|あてはまらない|当てはまらない).*?(もの|の|組合せ)?は?どれか", normalized):
        return "select_incorrect"
    incorrect_tokens = [
        "誤っているもの",
        "誤っている組合せ",
        "誤りを含むもの",
        "誤りであるもの",
        "誤りはどれか",
        "不適当なもの",
        "不適切なもの",
        "適切でないもの",
        "正しくないもの",
        "該当しないもの",
        "含まれないもの",
        "なっていないもの",
        "ではないもの",
        "あてはまらないもの",
        "当てはまらないもの",
    ]
    if any(token in normalized for token in incorrect_tokens):
        return "select_incorrect"

    correct_tokens = [
        "正しいもの",
        "正しい組合せ",
        "適当なもの",
        "適切なもの",
        "妥当なもの",
        "近いもの",
        "値はどれか",
        "量はどれか",
        "率はどれか",
        "いくらか",
        "いくらになるか",
        "何倍か",
        "求めよ",
        "算出せよ",
        "計算せよ",
    ]
    if any(token in normalized for token in correct_tokens):
        return "select_correct"
    if re.search(r"何.*(よいか|なるか|必要か|求められるか)", normalized):
        return "select_correct"
    if re.search(r"(もの|の|組合せ)?は?どれか", normalized):
        return "select_correct"
    return None


def build_correct_choice_text(
    *,
    choice_count: int,
    answer_numbers: list[int],
    question_intent: str,
) -> list[str]:
    if question_intent not in {"select_correct", "select_incorrect"}:
        raise ValueError(f"questionIntent を推定できません: {question_intent}")
    if not answer_numbers:
        raise ValueError("正解番号を取得できません")
    if any(number < 1 or number > choice_count for number in answer_numbers):
        raise ValueError(f"正解番号が選択肢数の範囲外です: answers={answer_numbers}, choices={choice_count}")

    if question_intent == "select_incorrect":
        labels = ["正しい"] * choice_count
        for number in answer_numbers:
            labels[number - 1] = "間違い"
        return labels

    labels = ["間違い"] * choice_count
    for number in answer_numbers:
        labels[number - 1] = "正しい"
    return labels


def era_token_to_year(token: str) -> int | None:
    normalized = normalize_digits(token or "").strip()
    match = re.match(r"([RrHh]|令和|平成)\s*(\d{1,2})", normalized)
    if not match:
        return None
    era, year_text = match.groups()
    year = int(year_text)
    if era in {"R", "r", "令和"}:
        return 2018 + year
    if era in {"H", "h", "平成"}:
        return 1988 + year
    return None


def era_token_to_display(token: str) -> str:
    normalized = normalize_digits(token or "").strip()
    match = re.match(r"([RrHh]|令和|平成)\s*(\d{1,2})", normalized)
    if not match:
        return normalized
    era, year_text = match.groups()
    era_name = "令和" if era in {"R", "r", "令和"} else "平成"
    return f"{era_name}{int(year_text)}"


def extract_source_token_from_url(url: str) -> str | None:
    match = re.search(r"/([rh]\d+)-", url, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.search(r"/entry/(R\d+)(?:-\d+-\d+)?/?$", url)
    if match:
        return match.group(1)
    match = re.search(r"/((?:r|h)\d{2})", url, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def make_exam_label(era_token: str | None, exam_year: int | None) -> str:
    if era_token and exam_year:
        return f"{QUALIFICATION_NAME} {era_token_to_display(era_token)}年度（{exam_year}年）"
    if exam_year:
        return f"{QUALIFICATION_NAME} {exam_year}年度"
    return QUALIFICATION_NAME


def infer_source_kind(url: str) -> str:
    host = urlparse(url).netloc
    if "yaku-tik.com" in host:
        return "yaku-tik"
    if "qualification-text.com" in host:
        return "qualification-text"
    if "zoron.hatenablog.com" in host:
        return "zoron"
    raise ValueError(f"未対応の公害防止管理者ソースです: {url}")


def make_source_question_id(source_kind: str, url: str) -> str:
    parsed = urlparse(url)
    path_key = parsed.path.strip("/").replace("/", ":")
    return f"kougai:{source_kind}:{path_key}"


def make_question_dict(
    *,
    source_kind: str,
    question_url: str,
    exam_year: int | None,
    era_token: str | None,
    subject: str,
    question_number: int,
    question_body_text: str,
    original_question_body_text: str,
    choice_text_list: list[str],
    correct_choice_text: list[str],
    answer_numbers: list[int],
    question_intent: str,
    explanation_text: list[str],
    source_list_group_id: str | None,
    transform_mode: str,
) -> dict:
    if not choice_text_list:
        raise ValueError(f"選択肢が空です: {question_url}")
    if len(choice_text_list) != len(correct_choice_text):
        raise ValueError(f"選択肢数と正誤ラベル数が一致しません: {question_url}")
    if len(explanation_text) not in {0, len(choice_text_list)}:
        raise ValueError(f"解説数と選択肢数が一致しません: {question_url}")

    source_question_id = make_source_question_id(source_kind, question_url)
    public_question_id = make_public_question_id(source_question_id)
    correct_numbers = [idx + 1 for idx, label in enumerate(correct_choice_text) if label == "正しい"]

    return {
        "questionBodyText": normalize_question_body_text(question_body_text),
        "originalQuestionBodyText": normalize_question_body_text(original_question_body_text),
        "examLabel": make_exam_label(era_token, exam_year),
        "qualificationName": QUALIFICATION_NAME,
        "questionLabel": f"{subject} 問{question_number}",
        "questionType": "true_false",
        "choiceTextList": choice_text_list,
        "originalQuestionChoiceImageUrls": [[] for _ in choice_text_list],
        "category": subject,
        "examYear": exam_year,
        "list_group_id": OUTPUT_LIST_GROUP_ID or (str(exam_year) if exam_year else source_list_group_id),
        "source_list_group_id": source_list_group_id,
        "question_url": question_url,
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "question_id_policy_key": "kougai:hmac-source-key:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": "kougai:{source_kind}:{url_path}",
        "questionImageStorageUrls": [],
        "questionIntent": question_intent,
        "correctChoiceText": correct_choice_text,
        "explanationText": explanation_text or ["" for _ in choice_text_list],
        "answer_result_text": build_answer_result_text(answer_numbers),
        "answer_result_inferred_correct_choice_numbers": answer_numbers,
        "source_question_id": source_question_id,
        "questionSourceSite": source_kind,
        "sourceTransformMode": transform_mode,
        "sourceCorrectChoiceNumbers": answer_numbers,
        "sourceTrueChoiceNumbers": correct_numbers,
    }


def validate_question_body(question: dict) -> None:
    url = str(question.get("question_url") or "")
    choices = question.get("choiceTextList")
    correctness = question.get("correctChoiceText")
    answer_numbers = question.get("answer_result_inferred_correct_choice_numbers")
    intent = question.get("questionIntent")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"choiceTextList が空です: {url}")
    if not isinstance(correctness, list) or len(correctness) != len(choices):
        raise ValueError(f"correctChoiceText が選択肢数と一致しません: {url}")
    if any(label not in {"正しい", "間違い"} for label in correctness):
        raise ValueError(f"correctChoiceText に不正な値があります: {url}")
    if intent not in {"select_correct", "select_incorrect"}:
        raise ValueError(f"questionIntent が不正です: {url}")
    if not isinstance(answer_numbers, list) or not answer_numbers:
        raise ValueError(f"正解番号が空です: {url}")
    if any(not isinstance(number, int) or number < 1 or number > len(choices) for number in answer_numbers):
        raise ValueError(f"正解番号が選択肢数の範囲外です: {url}")
    if not str(question.get("answer_result_text") or "").strip():
        raise ValueError(f"answer_result_text が空です: {url}")
    if not str(question.get("questionBodyText") or "").strip():
        raise ValueError(f"questionBodyText が空です: {url}")


def parse_candidate_terms_from_ol(ol: Tag) -> dict[str, str]:
    terms: dict[str, str] = {}
    for index, li in enumerate(ol.find_all("li", recursive=False)):
        if index >= len(NUMBER_TO_ALPHA):
            break
        term = node_text(li)
        if term:
            terms[NUMBER_TO_ALPHA[index]] = term
    return terms


def parse_candidate_terms_from_text(text: str) -> dict[str, str]:
    terms: dict[str, str] = {}
    normalized = text.translate(FULLWIDTH_ALPHA_TRANS)
    for label, term in re.findall(r"([a-z])\s*[：:]\s*([^a-z\n]+?)(?=(?:\s+[a-z]\s*[：:])|$|\n)", normalized):
        cleaned = clean_text(term)
        if cleaned:
            terms[label] = cleaned
    return terms


def parse_blank_labels(text: str) -> list[str]:
    labels = re.findall(r"[ア-ンＡ-ＺA-Z]", text or "")
    result: list[str] = []
    for label in labels:
        normalized = label.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def parse_alpha_tokens(text: str) -> list[str]:
    normalized = (text or "").translate(FULLWIDTH_ALPHA_TRANS).lower()
    return re.findall(r"\b([a-z])\b", normalized)


def parse_yakutik_combination(question_nodes: list[Tag]) -> CombinationData | None:
    candidate_ol: Tag | None = None
    for node in question_nodes:
        if node.name != "ol":
            continue
        style = str(node.get("style") or "")
        if "lower-alpha" in style:
            candidate_ol = node
            break
    if candidate_ol is None:
        return None

    candidate_terms = parse_candidate_terms_from_ol(candidate_ol)
    if not candidate_terms:
        return None

    siblings = question_nodes[question_nodes.index(candidate_ol) + 1 :]
    header_labels: list[str] = []
    row_ol: Tag | None = None
    for node in siblings:
        if node.name == "ul" and not header_labels:
            header_labels = parse_blank_labels(node_text(node))
        elif node.name == "ol" and header_labels:
            row_ol = node
            break
    if row_ol is None or not header_labels:
        return None

    rows: list[list[str]] = []
    row_numbers: list[int] = []
    for index, li in enumerate(row_ol.find_all("li", recursive=False), start=1):
        tokens = parse_alpha_tokens(node_text(li))
        if tokens:
            rows.append(tokens)
            row_numbers.append(index)
    if not rows:
        return None
    return CombinationData(header_labels, candidate_terms, rows, row_numbers)


def parse_zoron_combination(problem_nodes: list[Tag]) -> CombinationData | None:
    problem_text = nodes_text(problem_nodes)
    candidate_terms = parse_candidate_terms_from_text(problem_text)
    if not candidate_terms:
        return None

    table = next((node for node in problem_nodes if node.name == "table"), None)
    if table is None:
        return None

    rows: list[list[str]] = []
    row_numbers: list[int] = []
    blank_labels: list[str] = []
    for row_index, tr in enumerate(table.find_all("tr"), start=1):
        cells = [node_text(cell) for cell in tr.find_all(["td", "th"], recursive=False)]
        if not cells:
            continue
        if row_index == 1:
            blank_labels = [cell for cell in cells[1:] if cell]
            continue
        cells = [cell for cell in cells if cell != ""]
        row_number = parse_choice_number_token(cells[0])
        tokens = [normalize_alpha_label(cell) for cell in cells[1:]]
        tokens = [token for token in tokens if token]
        if row_number is not None and tokens:
            rows.append(tokens)
            row_numbers.append(row_number)
    if not blank_labels or not rows:
        return None
    return CombinationData(blank_labels, candidate_terms, rows, row_numbers)


def build_pair_choices(
    combination: CombinationData,
    *,
    correct_answer_number: int,
    full_explanation: str,
) -> tuple[list[str], list[str], list[int], list[str]]:
    if correct_answer_number not in combination.row_numbers:
        raise ValueError(f"正解行が組合せ表に存在しません: {correct_answer_number}")
    correct_row = combination.rows[combination.row_numbers.index(correct_answer_number)]
    if len(correct_row) != len(combination.blank_labels):
        raise ValueError("正解行の列数と空欄ラベル数が一致しません")

    correct_by_blank = dict(zip(combination.blank_labels, correct_row))
    choices: list[str] = []
    labels: list[str] = []
    explanations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in combination.rows:
        if len(row) != len(combination.blank_labels):
            raise ValueError("組合せ表の列数が空欄ラベル数と一致しません")
        for blank_label, alpha_label in zip(combination.blank_labels, row):
            term = combination.candidate_terms.get(alpha_label)
            if not term:
                raise ValueError(f"候補語句が見つかりません: {alpha_label}")
            key = (blank_label, term)
            if key in seen:
                continue
            seen.add(key)
            choice = f"{blank_label}：{term}"
            is_correct = correct_by_blank.get(blank_label) == alpha_label
            correct_term = combination.candidate_terms[correct_by_blank[blank_label]]
            choices.append(choice)
            labels.append("正しい" if is_correct else "間違い")
            if is_correct:
                explanations.append(f"{choice} は正しい対応です。\n{full_explanation}".strip())
            else:
                explanations.append(
                    f"{choice} は誤りです。{blank_label}に入る正しい語句は「{correct_term}」です。\n{full_explanation}".strip()
                )

    answer_numbers = [index + 1 for index, label in enumerate(labels) if label == "正しい"]
    if len(answer_numbers) != len(combination.blank_labels):
        raise ValueError("pair 化した正解数が空欄数と一致しません")
    return choices, labels, answer_numbers, explanations


def format_candidate_terms(candidate_terms: dict[str, str]) -> str:
    if not candidate_terms:
        return ""
    return "\n".join(f"{label}：{term}" for label, term in candidate_terms.items())


def original_choices_from_combination(combination: CombinationData) -> list[str]:
    choices: list[str] = []
    header = "　".join(combination.blank_labels)
    for row_number, row in zip(combination.row_numbers, combination.rows):
        values = "　".join(row)
        choices.append(f"({row_number}) {header}\n{values}")
    return choices


def parse_choices_from_ordered_list(ol: Tag) -> list[str]:
    choices: list[str] = []
    for index, li in enumerate(ol.find_all("li", recursive=False), start=1):
        text = node_text(li)
        if text:
            choices.append(f"({index}) {text}")
    return choices


def parse_inline_numbered_choices(text: str) -> tuple[str, list[str]]:
    source = clean_text(text)
    matches = list(re.finditer(r"[（(]\s*([0-9０-９]{1,2})\s*[)）]", source))
    if len(matches) < 2:
        return normalize_question_body_text(source), []

    numbers = [int(normalize_digits(match.group(1))) for match in matches]
    runs: list[tuple[int, int]] = []
    for start_index, number in enumerate(numbers):
        if number != 1:
            continue
        expected = 1
        end_index = start_index
        while end_index < len(numbers) and numbers[end_index] == expected:
            expected += 1
            end_index += 1
        if expected > 3:
            runs.append((start_index, end_index))
    if not runs:
        return normalize_question_body_text(source), []

    start_index, end_index = max(runs, key=lambda item: (item[1] - item[0], item[0]))
    stem = normalize_question_body_text(source[: matches[start_index].start()])
    choices: list[str] = []
    for index in range(start_index, end_index):
        segment_start = matches[index].end()
        segment_end = matches[index + 1].start() if index + 1 < end_index else len(source)
        body = normalize_question_body_text(source[segment_start:segment_end])
        choices.append(f"({numbers[index]}) {body}".strip())
    return stem, choices


def parse_choices_from_lines(text: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in clean_text(text).splitlines() if line.strip()]
    stem_lines: list[str] = []
    choices: list[str] = []
    current_marker: str | None = None
    current_lines: list[str] = []

    def flush_choice() -> None:
        nonlocal current_marker, current_lines
        if current_marker is not None:
            body = normalize_question_body_text("\n".join(current_lines))
            choices.append(f"({current_marker}) {body}".strip())
        current_marker = None
        current_lines = []

    for line in lines:
        marker_match = re.match(r"^[（(]\s*(\d{1,2})\s*[)）]\s*(.*)$", normalize_digits(line))
        circled_match = re.match(r"^([①-⑩⑴-⑽])\s*(.*)$", line)
        marker: str | None = None
        rest = ""
        if marker_match:
            marker = marker_match.group(1)
            rest = marker_match.group(2)
        elif circled_match:
            marker_num = parse_choice_number_token(circled_match.group(1))
            marker = str(marker_num) if marker_num is not None else None
            rest = circled_match.group(2)

        if marker is not None:
            flush_choice()
            current_marker = marker
            if rest.strip():
                current_lines.append(rest.strip())
            continue

        if current_marker is None:
            stem_lines.append(line)
        else:
            current_lines.append(line)

    flush_choice()
    if len(choices) < 2:
        inline_stem, inline_choices = parse_inline_numbered_choices(text)
        if inline_choices:
            return inline_stem, inline_choices
    return normalize_question_body_text("\n".join(stem_lines)), choices


def infer_image_choice_count(explanation_text: str, answer_numbers: list[int]) -> int | None:
    normalized = normalize_digits(explanation_text or "")
    explanation_numbers = [
        int(value)
        for value in re.findall(r"[（(]\s*(\d{1,2})\s*[)）]\s*は", normalized)
    ]
    if len(set(explanation_numbers)) < 2:
        return None
    numbers = explanation_numbers + answer_numbers
    if not numbers:
        return None
    count = max(numbers)
    if 2 <= count <= 10:
        return count
    return None


def make_image_placeholder_choices(choice_count: int) -> list[str]:
    return [f"({index}) 画像内の選択肢{index}" for index in range(1, choice_count + 1)]


def nodes_have_images(nodes: list[Tag]) -> bool:
    return any(node.name == "img" or node.find("img") is not None for node in nodes)


def infer_default_image_choice_count(nodes: list[Tag], answer_numbers: list[int]) -> int | None:
    if not nodes_have_images(nodes) or not answer_numbers:
        return None
    if max(answer_numbers) <= 5:
        return 5
    return None


def infer_form_choice_count(form: Tag) -> int | None:
    numbers: list[int] = []
    for radio in form.find_all("input", attrs={"type": "radio"}):
        value = str(radio.get("value") or "").strip()
        if not value:
            continue
        try:
            number = int(normalize_digits(value))
        except ValueError:
            continue
        if number not in numbers:
            numbers.append(number)
    if len(numbers) >= 2 and numbers == list(range(1, len(numbers) + 1)):
        return len(numbers)
    return None


def parse_underlined_choices(main: Tag) -> list[str]:
    choices: list[str] = []
    targets: list[Tag] = []
    for span in main.find_all(class_="under"):
        targets.append(span)
    for underline in main.find_all("u"):
        targets.append(underline)

    for span in targets:
        previous_text = ""
        for previous in span.previous_elements:
            if isinstance(previous, NavigableString):
                previous_text = str(previous) + previous_text
                if len(previous_text) > 20:
                    break
        numbers = re.findall(r"[（(](\d{1,2})[)）]", normalize_digits(previous_text))
        if not numbers:
            continue
        number = numbers[-1]
        choice = f"({number}) {node_text(span)}"
        if choice not in choices:
            choices.append(choice)
    return choices


def split_zoron_entry_sections(entry_content: Tag) -> dict[str, list[Tag]]:
    sections: dict[str, list[Tag]] = {}
    current_key: str | None = None
    for child in direct_child_tags(entry_content):
        if child.name in {"h2", "h3", "h4"}:
            heading = normalize_inline_text(node_text(child))
            if heading in {"問題", "解答", "解説"}:
                current_key = heading
                sections.setdefault(current_key, [])
                continue
        if current_key:
            sections[current_key].append(child)
    return sections


def longest_entry_content(soup: BeautifulSoup) -> Tag:
    candidates = soup.select(".entry-content")
    if not candidates:
        raise ValueError("entry-content が見つかりません")
    return max(candidates, key=lambda node: len(node_text(node)))


def parse_yakutik_question_page(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title_text = node_text(soup.select_one("h1.entry-title") or soup.title)
    meta_match = re.search(r"([RH]\d+)年\s+(.+?)\s+問\s*(\d+)", normalize_digits(title_text), flags=re.IGNORECASE)
    if not meta_match:
        raise ValueError(f"yaku-tik のタイトルから年度・科目・問番号を取得できません: {url}")
    era_token, subject, question_number_text = meta_match.groups()
    exam_year = era_token_to_year(era_token)
    question_number = int(question_number_text)

    content = soup.select_one("article .entry-content")
    if content is None:
        raise ValueError(f"yaku-tik の本文が見つかりません: {url}")
    children = direct_child_tags(content)
    answer_index = next(
        (
            index
            for index, child in enumerate(children)
            if "blank-box" in (child.get("class") or []) and "正解" in node_text(child)
        ),
        None,
    )
    if answer_index is None:
        raise ValueError(f"yaku-tik の正解欄が見つかりません: {url}")

    question_nodes = children[:answer_index]
    answer_node = children[answer_index]
    explanation_nodes = children[answer_index + 1 :]
    answer_numbers = parse_correct_answer_numbers(node_text(answer_node))
    if len(answer_numbers) != 1:
        raise ValueError(f"yaku-tik の正解番号を一意に取得できません: {url}")

    combination = parse_yakutik_combination(question_nodes)
    question_text_raw = nodes_text(question_nodes)
    explanation_text_raw = nodes_text(
        [node for node in explanation_nodes if "解説" not in normalize_inline_text(node_text(node))]
    )
    question_intent = determine_question_intent(question_text_raw)

    transform_mode = "original_choice_true_false"
    if combination and question_intent == "select_correct":
        stem_nodes = question_nodes[: question_nodes.index(next(node for node in question_nodes if node.name == "ol" and "lower-alpha" in str(node.get("style") or "")))]
        stem = nodes_text(
            [node for node in stem_nodes if not is_heading_noise_text(node_text(node))]
        )
        candidate_text = format_candidate_terms(combination.candidate_terms)
        question_body_text = "\n\n".join(
            part
            for part in [
                stem,
                f"候補語句:\n{candidate_text}" if candidate_text else "",
                "次の空欄と語句の対応が正しいか判定してください。",
            ]
            if part
        )
        choices, correctness, true_answer_numbers, explanations = build_pair_choices(
            combination,
            correct_answer_number=answer_numbers[0],
            full_explanation=explanation_text_raw,
        )
        answer_numbers_for_output = true_answer_numbers
        transform_mode = "blank_pair_true_false"
    else:
        if combination:
            choices = original_choices_from_combination(combination)
            stem = question_text_raw
        else:
            candidate_ol = next((node for node in question_nodes if node.name == "ol"), None)
            if candidate_ol is None:
                underline_container = BeautifulSoup("<div></div>", "html.parser").div
                for node in question_nodes:
                    underline_container.append(BeautifulSoup(str(node), "html.parser"))
                choices = parse_underlined_choices(underline_container)
                stem = question_text_raw
                if not choices:
                    stem, choices = parse_choices_from_lines(question_text_raw)
                if not choices:
                    image_choice_count = infer_image_choice_count(explanation_text_raw, answer_numbers)
                    if image_choice_count:
                        choices = make_image_placeholder_choices(image_choice_count)
                        stem = "\n\n".join(
                            part
                            for part in [
                                stem,
                                "選択肢は問題画像を参照してください。",
                            ]
                            if part
                        )
            else:
                choices = parse_choices_from_ordered_list(candidate_ol)
                stem = nodes_text(question_nodes[: question_nodes.index(candidate_ol)])
        if question_intent is None:
            raise ValueError(f"yaku-tik の問題意図を推定できません: {url}")
        question_body_text = stem
        correctness = build_correct_choice_text(
            choice_count=len(choices),
            answer_numbers=answer_numbers,
            question_intent=question_intent,
        )
        explanations = [explanation_text_raw for _ in choices]
        answer_numbers_for_output = answer_numbers

    question = make_question_dict(
        source_kind="yaku-tik",
        question_url=url,
        exam_year=exam_year,
        era_token=era_token,
        subject=subject,
        question_number=question_number,
        question_body_text=question_body_text,
        original_question_body_text=question_text_raw,
        choice_text_list=choices,
        correct_choice_text=correctness,
        answer_numbers=answer_numbers_for_output,
        question_intent=question_intent or "select_correct",
        explanation_text=explanations,
        source_list_group_id=extract_source_token_from_url(url),
        transform_mode=transform_mode,
    )
    image_urls = extract_image_urls_from_nodes(question_nodes, url)
    return ParsedPage(question=question, source_image_urls=image_urls)


def parse_zoron_question_page(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(".entry-title")
    title_text = node_text(title_node or soup.title)
    meta_match = re.search(r"(R\d+)年\s+(.+?)\s+問\s*(\d+)", normalize_digits(title_text), flags=re.IGNORECASE)
    if not meta_match:
        raise ValueError(f"zoron のタイトルから年度・科目・問番号を取得できません: {url}")
    era_token, subject, question_number_text = meta_match.groups()
    exam_year = era_token_to_year(era_token)
    question_number = int(question_number_text)

    content = longest_entry_content(soup)
    sections = split_zoron_entry_sections(content)
    problem_nodes = sections.get("問題") or []
    answer_nodes = sections.get("解答") or []
    explanation_nodes = sections.get("解説") or []
    if not problem_nodes or not answer_nodes:
        raise ValueError(f"zoron の問題/解答セクションが見つかりません: {url}")
    problem_text = nodes_text(problem_nodes)
    answer_numbers = parse_correct_answer_numbers(nodes_text(answer_nodes))
    if len(answer_numbers) != 1:
        raise ValueError(f"zoron の正解番号を一意に取得できません: {url}")
    explanation_text_raw = nodes_text(explanation_nodes)
    question_intent = determine_question_intent(problem_text)
    combination = parse_zoron_combination(problem_nodes)

    transform_mode = "original_choice_true_false"
    if combination and question_intent == "select_correct":
        table = next(node for node in problem_nodes if node.name == "table")
        stem_nodes = [
            node
            for node in problem_nodes
            if node is not table
            and not is_heading_noise_text(node_text(node))
            and not is_candidate_terms_node(node)
        ]
        stem = nodes_text(stem_nodes)
        candidate_text = format_candidate_terms(combination.candidate_terms)
        question_body_text = "\n\n".join(
            part
            for part in [
                stem,
                f"候補語句:\n{candidate_text}" if candidate_text else "",
                "次の空欄と語句の対応が正しいか判定してください。",
            ]
            if part
        )
        choices, correctness, true_answer_numbers, explanations = build_pair_choices(
            combination,
            correct_answer_number=answer_numbers[0],
            full_explanation=explanation_text_raw,
        )
        answer_numbers_for_output = true_answer_numbers
        transform_mode = "blank_pair_true_false"
    else:
        if question_intent is None:
            raise ValueError(f"zoron の問題意図を推定できません: {url}")
        stem, choices = parse_zoron_regular_choices(problem_nodes)
        if not choices:
            image_choice_count = infer_image_choice_count(explanation_text_raw, answer_numbers)
            if image_choice_count is None:
                image_choice_count = infer_default_image_choice_count(problem_nodes, answer_numbers)
            if image_choice_count:
                choices = make_image_placeholder_choices(image_choice_count)
                stem = "\n\n".join(
                    part
                    for part in [
                        problem_text,
                        "選択肢は問題画像を参照してください。",
                    ]
                    if part
                )
            else:
                raise ValueError(f"zoron の選択肢を取得できません: {url}")
        correctness = build_correct_choice_text(
            choice_count=len(choices),
            answer_numbers=answer_numbers,
            question_intent=question_intent,
        )
        question_body_text = stem
        explanations = [explanation_text_raw for _ in choices]
        answer_numbers_for_output = answer_numbers

    question = make_question_dict(
        source_kind="zoron",
        question_url=url,
        exam_year=exam_year,
        era_token=era_token,
        subject=subject,
        question_number=question_number,
        question_body_text=question_body_text,
        original_question_body_text=problem_text,
        choice_text_list=choices,
        correct_choice_text=correctness,
        answer_numbers=answer_numbers_for_output,
        question_intent=question_intent or "select_correct",
        explanation_text=explanations,
        source_list_group_id=extract_source_token_from_url(url),
        transform_mode=transform_mode,
    )
    image_urls = extract_image_urls_from_nodes(problem_nodes, url)
    return ParsedPage(question=question, source_image_urls=image_urls)


def parse_zoron_regular_choices(problem_nodes: list[Tag]) -> tuple[str, list[str]]:
    table = next((node for node in problem_nodes if node.name == "table"), None)
    if table is not None:
        rows = []
        for tr in table.find_all("tr"):
            cells = [node_text(cell) for cell in tr.find_all(["td", "th"], recursive=False)]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue
            number = parse_choice_number_token(cells[0])
            if number is None:
                continue
            rows.append(f"({number}) " + " / ".join(cells[1:]))
        stem = nodes_text([node for node in problem_nodes if node is not table])
        if rows:
            return stem, rows
    ol = next((node for node in problem_nodes if node.name == "ol"), None)
    if ol is not None:
        choices = parse_choices_from_ordered_list(ol)
        stem = nodes_text([node for node in problem_nodes if node is not ol])
        if choices:
            return stem, choices
    return parse_choices_from_lines(nodes_text(problem_nodes))


def parse_qualification_text_question_page(html: str, url: str, http_session) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main")
    if main is None:
        raise ValueError(f"qualification-text の #main が見つかりません: {url}")

    h2_text = node_text(main.find("h2"))
    year_match = re.search(r"(令和|平成)\s*(\d{1,2})年\s*[（(]\s*((?:19|20)\d{2})\s*[)）]\s*(.+?)(?:\s+問\s*(\d+))?$", normalize_digits(h2_text))
    url_match = re.search(r"/((?:r|h)\d{2})kako(\d)-(\d{2})\.php$", url, flags=re.IGNORECASE)
    if not year_match or not url_match:
        raise ValueError(f"qualification-text の年度・科目・問番号を取得できません: {url}")
    era_name, era_year, western_year, subject_from_title, question_number_from_title = year_match.groups()
    era_token = ("R" if era_name == "令和" else "H") + str(int(era_year))
    exam_year = int(western_year)
    subject_number = url_match.group(2)
    question_number = int(question_number_from_title or url_match.group(3))
    subject = subject_from_title.replace("過去問", "").strip() or QUALIFICATION_TEXT_SUBJECTS.get(subject_number, "")

    form = main.find("form")
    if form is None:
        raise ValueError(f"qualification-text の form が見つかりません: {url}")
    answer_input = form.find("input", attrs={"name": "ncAnswers"})
    answer_numbers = parse_correct_answer_numbers(str(answer_input.get("value") if answer_input else ""))
    if len(answer_numbers) != 1:
        raise ValueError(f"qualification-text の正解番号を一意に取得できません: {url}")

    question_p = form.find_previous("p")
    if question_p is None:
        raise ValueError(f"qualification-text の問題本文 p が見つかりません: {url}")
    problem_text = node_text(question_p)
    question_intent = determine_question_intent(problem_text)
    if question_intent is None:
        raise ValueError(f"qualification-text の問題意図を推定できません: {url}")

    choices = parse_underlined_choices(main)
    stem = problem_text
    if not choices:
        stem, choices = parse_choices_from_lines(problem_text)
    if not choices:
        image_choice_count = infer_form_choice_count(form) if nodes_have_images([question_p]) else None
        if image_choice_count:
            choices = make_image_placeholder_choices(image_choice_count)
            stem = "\n\n".join(
                part
                for part in [
                    stem,
                    "選択肢は問題画像を参照してください。",
                ]
                if part
            )
        else:
            raise ValueError(f"qualification-text の選択肢を取得できません: {url}")

    explanation_text_raw = fetch_qualification_explanation_text(form, url, http_session)
    correctness = build_correct_choice_text(
        choice_count=len(choices),
        answer_numbers=answer_numbers,
        question_intent=question_intent,
    )
    explanations = [explanation_text_raw for _ in choices]

    question = make_question_dict(
        source_kind="qualification-text",
        question_url=url,
        exam_year=exam_year,
        era_token=era_token,
        subject=subject,
        question_number=question_number,
        question_body_text=stem,
        original_question_body_text=problem_text,
        choice_text_list=choices,
        correct_choice_text=correctness,
        answer_numbers=answer_numbers,
        question_intent=question_intent,
        explanation_text=explanations,
        source_list_group_id=extract_source_token_from_url(url),
        transform_mode="original_choice_true_false",
    )
    image_urls = extract_image_urls_from_nodes([question_p], url)
    return ParsedPage(question=question, source_image_urls=image_urls)


def fetch_qualification_explanation_text(form: Tag, question_url: str, http_session) -> str:
    action = str(form.get("action") or "").strip()
    if not action:
        return ""
    explanation_url = urljoin(question_url, action)
    html = fetch_html_text(http_session, explanation_url)
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main")
    if main is None:
        return ""
    h5 = main.find("h5", string=lambda value: value and "解答" in value)
    if h5 is None:
        return node_text(main)
    collected: list[Tag] = []
    node = h5.find_next_sibling()
    while node is not None:
        if isinstance(node, Tag):
            if node.name == "table":
                break
            if node.name == "a" and "次の問題" in node_text(node):
                break
            collected.append(node)
        node = node.find_next_sibling()
    return nodes_text(collected)


def extract_image_urls_from_nodes(nodes: list[Tag], base_url: str) -> list[str]:
    image_urls: list[str] = []
    for node in nodes:
        for image_url in extract_image_urls_from_element(node, base_url):
            if image_url not in image_urls:
                image_urls.append(image_url)
    return image_urls


def parse_question_page(html: str, url: str, http_session) -> ParsedPage:
    source_kind = infer_source_kind(url)
    if source_kind == "yaku-tik":
        return parse_yakutik_question_page(html, url)
    if source_kind == "zoron":
        return parse_zoron_question_page(html, url)
    if source_kind == "qualification-text":
        return parse_qualification_text_question_page(html, url, http_session)
    raise ValueError(f"未対応ソースです: {source_kind}")


def discover_question_urls(http_session, start_url: str) -> list[str]:
    source_kind = infer_source_kind(start_url)
    if source_kind == "yaku-tik":
        return discover_yakutik_question_urls(http_session, start_url)
    if source_kind == "zoron":
        return discover_zoron_question_urls(http_session, start_url)
    if source_kind == "qualification-text":
        return discover_qualification_text_question_urls(http_session, start_url)
    raise ValueError(f"未対応ソースです: {source_kind}")


def dedupe_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        stripped = url.rstrip("/")
        normalized = stripped if re.search(r"\.[A-Za-z0-9]+$", urlparse(stripped).path) else stripped + "/"
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def discover_yakutik_question_urls(http_session, start_url: str) -> list[str]:
    if re.search(r"/kougai/[rh]\d+-[^/]+-\d+/?$", start_url, flags=re.IGNORECASE):
        return [start_url.rstrip("/") + "/"]

    html = fetch_html_text(http_session, start_url)
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("article .entry-content") or soup
    post_links = yakutik_post_links(content, start_url)
    if post_links:
        return dedupe_preserve_order(post_links)

    subject_links = yakutik_subject_category_links(content, start_url)
    discovered: list[str] = []
    for subject_url in subject_links:
        subject_html = fetch_html_text(http_session, subject_url)
        subject_soup = BeautifulSoup(subject_html, "html.parser")
        subject_content = subject_soup.select_one("article .entry-content") or subject_soup
        discovered.extend(yakutik_post_links(subject_content, subject_url))
    return dedupe_preserve_order(discovered)


def yakutik_subject_category_links(content: Tag | BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    for anchor in content.select("a[href]"):
        href = urljoin(base_url, str(anchor.get("href") or ""))
        if re.search(r"/category/.*/[rh]\d+-[^/]+/?$", href, flags=re.IGNORECASE):
            links.append(href.rstrip("/") + "/")
    return dedupe_preserve_order(links)


def yakutik_post_links(content: Tag | BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    for anchor in content.select("a[href]"):
        href = urljoin(base_url, str(anchor.get("href") or ""))
        if re.search(r"/kougai/[rh]\d+-[^/]+-\d+/?$", href, flags=re.IGNORECASE):
            links.append(href.rstrip("/") + "/")
    return dedupe_preserve_order(links)


def discover_zoron_question_urls(http_session, start_url: str) -> list[str]:
    if re.search(r"/entry/R\d+-\d+-\d+/?$", start_url):
        return [start_url.rstrip("/")]
    start_match = re.search(r"/entry/(R\d+)/?$", start_url)
    start_era_token = start_match.group(1) if start_match else None
    html = fetch_html_text(http_session, start_url)
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        href = urljoin(start_url, str(anchor.get("href") or ""))
        match = re.search(r"/entry/(R\d+)-\d+-\d+/?$", href)
        if match and (start_era_token is None or match.group(1) == start_era_token):
            links.append(href.rstrip("/"))
    return dedupe_preserve_order(links)


def discover_qualification_text_question_urls(http_session, start_url: str) -> list[str]:
    if re.search(r"/(?:r|h)\d{2}kako\d-\d{2}\.php$", start_url, flags=re.IGNORECASE) and not start_url.endswith("a.php"):
        return [start_url]
    html = fetch_html_text(http_session, start_url)
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        href = urljoin(start_url, str(anchor.get("href") or ""))
        if re.search(r"/(?:r|h)\d{2}kako\d-\d{2}\.php$", href, flags=re.IGNORECASE) and not href.endswith("a.php"):
            links.append(href)
    return dedupe_preserve_order(links)


def infer_output_list_group_id(start_url: str) -> str | None:
    source_token = extract_source_token_from_url(start_url)
    if source_token:
        year = era_token_to_year(source_token)
        if year:
            return str(year)
    return None


def sanitize_filename_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_") or "question"


def attach_downloaded_images(http_session, parsed_page: ParsedPage) -> dict:
    question = dict(parsed_page.question)
    if not parsed_page.source_image_urls or not IMAGE_OUTPUT_DIR:
        return question
    source_key = str(question.get("source_question_id") or question.get("public_question_id") or "question")
    filenames = download_and_save_images(
        http_session,
        parsed_page.source_image_urls,
        sanitize_filename_token(source_key),
        base_dir=IMAGE_OUTPUT_DIR,
    )
    question["questionImageStorageUrls"] = [
        make_storage_url(filename, QUALIFICATION_CODE) for filename in filenames
    ]
    question["sourceQuestionImageUrls"] = parsed_page.source_image_urls
    return question


def main() -> int:
    global IMAGE_OUTPUT_DIR

    load_local_secure_env()
    apply_runtime_overrides_from_env()
    http_session = create_http_session()

    output_list_group_id = OUTPUT_LIST_GROUP_ID or infer_output_list_group_id(LIST_FIRST_PAGE_URL)
    json_output_dir, image_output_dir = prepare_output_dirs(
        OUTPUT_DIR,
        QUALIFICATION_CODE,
        output_list_group_id,
        JSON_SUBDIR_NAME,
    )
    IMAGE_OUTPUT_DIR = image_output_dir

    question_urls = discover_question_urls(http_session, LIST_FIRST_PAGE_URL)
    if MAX_QUESTIONS is not None:
        question_urls = question_urls[:MAX_QUESTIONS]
    if not question_urls:
        raise RuntimeError(f"問題 URL が見つかりません: {LIST_FIRST_PAGE_URL}")

    question_bodies: list[dict] = []
    mode_counts: dict[str, int] = {}
    for index, question_url in enumerate(question_urls, start=1):
        print(f"[FETCH] ({index}/{len(question_urls)}) {question_url}")
        html = fetch_html_text(http_session, question_url)
        parsed_page = parse_question_page(html, question_url, http_session)
        question = attach_downloaded_images(http_session, parsed_page)
        validate_question_body(question)
        mode = str(question.get("sourceTransformMode") or "unknown")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        question_bodies.append(question)

    saved_paths = save_question_body_chunks(
        json_output_dir,
        output_list_group_id,
        question_bodies,
        chunk_size=25,
    )
    print(
        f"[DONE] qualification={QUALIFICATION_CODE} list_group_id={output_list_group_id} "
        f"questions={len(question_bodies)} saved={len(saved_paths)} modes={mode_counts}"
    )
    for path in saved_paths:
        print(f"[SAVED] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
