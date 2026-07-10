import json
import os
import sys
import glob  # Added
import re
import concurrent.futures
from dataclasses import dataclass
from typing import List, Set, Tuple
from urllib.parse import (
    urlparse,
    urlunparse,
    parse_qs,
    urlencode,
    urljoin,
    quote,
)

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from scripts.scrape.common import (
    create_http_session as common_create_http_session,
    download_and_save_images as common_download_and_save_images,
    make_canonical_question_key as common_make_canonical_question_key,
    make_canonical_statement_keys as common_make_canonical_statement_keys,
    extract_image_urls_from_element as common_extract_image_urls_from_element,
    fetch_html_text as common_fetch_html_text,
    load_local_secure_env as common_load_local_secure_env,
    make_public_question_id as common_make_public_question_id,
    make_storage_url as common_make_storage_url,
    make_url_source_question_id as common_make_url_source_question_id,
    normalize_inline_text as common_normalize_inline_text,
    normalize_question_body_text as common_normalize_question_body_text,
    save_question_body_chunks,
    slow_down as common_slow_down,
    source_site_from_url as common_source_site_from_url,
)


# 1問だけ試す場合はこちらを使う（LIST_FIRST_PAGE_URL が空のときに利用）
TARGET_URL = None
# 特定のページ番号（1, 2, ...）を指定して、そのページの25問を取得・更新する場合に設定
# ※ サイトの仕様で1ページ25問の場合、2を指定すると26〜50問目を取得して更新します。
TARGET_LIST_PAGE_NUMBER = None

# 既存のJSONファイルを更新するモード（TARGET_URL指定必須）
UPDATE_JSON_MODE = False

# ユーザーが設定する資格コード（UI側で管理）
# 公認心理師：kounin-shinrishi、二級建築士：2nd-class-kenchikushi、
# 介護福祉士：kaigofukushi、給水装置工事主任技術者：kyusuikouji-shunin
QUALIFICATION_CODE = "kyusuikouji-shunin"
# 資格名（examSource等に使用）
QUALIFICATION_NAME = "給水装置工事主任技術者"

# 問題一覧の1ページ目のURLを指定する
LIST_FIRST_PAGE_URL = "https://kyuukou.kakomonn.com/list1/77001?page=1"

# JSON出力を配置するサブディレクトリ名（list_group_id 配下）
JSON_SUBDIR_NAME = "00_source"

# 取得する問題数の上限（None の場合はすべて取得）
# テスト実行時は例: MAX_QUESTIONS = 5 などに変更
MAX_QUESTIONS = None

# JSON/画像の出力ルート
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# Firebase Storage の公開 URL 用ベース
FIREBASE_STORAGE_BASE_URL = "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o"
FIREBASE_STORAGE_PATH_PREFIX = f"question_images/official/{QUALIFICATION_CODE}/"

# question_id から公開用ID（public_question_id）を生成するための秘密キー。
# GitHub に値を載せないため、環境変数から読み込む。
QUESTION_ID_SECRET_KEY_ENV = "QUESTION_ID_SECRET_KEY"

# 画像保存先ディレクトリ（main 内で実際のパスを設定）
IMAGE_OUTPUT_DIR = None

# 設定ファイルパス
CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "qualification_rules.json")

# ローカルPC専用の秘密情報設定ファイル（Git管理外）
LOCAL_SECURE_ENV_PATH = os.path.expanduser("~/.config/exam_scraper/secure.env")


def load_local_secure_env(env_file_path: str = LOCAL_SECURE_ENV_PATH) -> None:
    common_load_local_secure_env(env_file_path)


def apply_runtime_overrides_from_env() -> None:
    """
    実行時だけ資格設定や出力先を差し替えたい場合に、環境変数から上書きする。
    既定値は維持しつつ、外部ランナーから複数資格を回せるようにする。
    """
    global QUALIFICATION_CODE
    global QUALIFICATION_NAME
    global LIST_FIRST_PAGE_URL
    global JSON_SUBDIR_NAME
    global MAX_QUESTIONS
    global OUTPUT_DIR
    global FIREBASE_STORAGE_PATH_PREFIX

    qualification_code = os.environ.get("SCRAPER_QUALIFICATION_CODE")
    qualification_name = os.environ.get("SCRAPER_QUALIFICATION_NAME")
    list_first_page_url = os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL")
    json_subdir_name = os.environ.get("SCRAPER_JSON_SUBDIR_NAME")
    max_questions = os.environ.get("SCRAPER_MAX_QUESTIONS")
    output_dir = os.environ.get("SCRAPER_OUTPUT_DIR")

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

    FIREBASE_STORAGE_PATH_PREFIX = f"question_images/official/{QUALIFICATION_CODE}/"


def make_public_question_id(question_id: int | str) -> str:
    return common_make_public_question_id(question_id)


def make_canonical_question_key(
    *,
    exam_occurrence_id: str | None,
    exam_year: int | None,
    question_label: str | None,
    section_code: str | None = None,
) -> str | None:
    return common_make_canonical_question_key(
        qualification_code=QUALIFICATION_CODE,
        exam_occurrence_id=exam_occurrence_id,
        exam_year=exam_year,
        question_label=question_label,
        section_code=section_code,
    )


def make_canonical_statement_keys(
    canonical_question_key: str | None,
    statement_count: int,
) -> list[str]:
    return common_make_canonical_statement_keys(canonical_question_key, statement_count)


def extract_1st_class_kenchikushi_section_code(exam_label: str | None) -> str | None:
    if not exam_label:
        return None

    if any(k in exam_label for k in ["学科1", "学科Ⅰ", "計画"]):
        return "gakka1-keikaku"
    if any(k in exam_label for k in ["学科2", "学科Ⅱ", "環境・設備"]):
        return "gakka2-kankyo-setsubi"
    if any(k in exam_label for k in ["学科3", "学科Ⅲ", "法規"]):
        return "gakka3-houki"
    if any(k in exam_label for k in ["学科4", "学科Ⅳ", "構造"]):
        return "gakka4-kouzou"
    if any(k in exam_label for k in ["学科5", "学科Ⅴ", "施工"]):
        return "gakka5-sekou"
    return None


def extract_list_group_id_from_url(list_page_url: str) -> str | None:
    """
    LIST_FIRST_PAGE_URL から「71013」のようなグループIDを取り出す。
    例: "https://.../list1/71013?page=1" -> "71013"
    ページ番号などクエリは無視し、パス末尾の数値部分だけを返す。
    """
    if not list_page_url:
        return None

    parsed = urlparse(list_page_url)
    path = parsed.path.rstrip("/")  # "/list1/71013"
    parts = path.split("/")         # ["", "list1", "71013"] 想定

    if parts and parts[-1].isdigit():
        return parts[-1]

    return None


# ==========
# のんびりアクセス用ヘルパ
# ==========

def slow_down(base_sec: float = 2.0, jitter_sec: float = 1.0) -> None:
    common_slow_down(base_sec, jitter_sec)


@dataclass
class ExplanationData:
    explanation_index_label: str      # "01", "02", "03" など
    explanation_body_text: str        # 解説本文
    image_filenames: List[str]        # この解説に紐づく画像ファイル名一覧（ローカルのファイル名）


@dataclass
class AnswerResultData:
    answer_result_text: str
    answer_result_html: str
    selected_choice_numbers: List[int]
    is_selected_choice_correct: bool | None
    inferred_correct_choice_numbers: List[int]


@dataclass
class QuestionData:
    question_url: str
    question_id: int | str

    exam_label: str                   # 例: "令和5年度（2023年） 午後"
    question_label: str               # 例: "問1 (一般問題 問1)"

    question_body_text: str           # 問題文
    choice_text_list: List[str]       # 設問文（選択肢） "選択肢1.～～" 形式
    correct_choice_numbers: List[int] # 正解の選択肢番号（1始まり）
    answer_result_data: AnswerResultData | None

    explanations: List[ExplanationData]

    question_image_filenames: List[str]  # 問題文の図などの画像ファイル名一覧（ローカルのファイル名）
    choice_image_filenames_by_choice: List[List[str]]  # 選択肢ごとの画像ファイル名一覧（choice index対応）
    source_question_id: str | None = None


def create_http_session() -> requests.Session:
    return common_create_http_session()


def fetch_html_text(http_session: requests.Session, target_url: str) -> str:
    return common_fetch_html_text(http_session, target_url)


# ==========
# 画像関連
# ==========

def guess_image_extension(image_url: str) -> str:
    from scripts.scrape.common import guess_image_extension as common_guess_image_extension

    return common_guess_image_extension(image_url)


def download_image_with_retry(
    http_session: requests.Session,
    image_url: str,
    max_retry: int = 3,
) -> bytes | None:
    for retry_index in range(max_retry):
        try:
            # 画像取得もゆっくり（少し短く）
            slow_down(0.2, 0.3)
            response = http_session.get(image_url, timeout=10)
            response.raise_for_status()
            return response.content
        except Exception as fetch_error:  # noqa: PERF203
            print(f"[WARN] image fetch failed ({image_url}): {fetch_error}")
            if retry_index == max_retry - 1:
                return None
            slow_down(1.0, 1.0)
    return None


def make_storage_url(filename: str) -> str:
    return common_make_storage_url(filename, QUALIFICATION_CODE)


def _download_and_save_single_image(
    http_session: requests.Session,
    image_url: str,
    base_dir: str,
    filename_prefix: str,
    index: int,
) -> Tuple[int, str | None]:
    """
    download_and_save_images のためのヘルパー関数（並列実行用）
    """
    image_bytes = download_image_with_retry(http_session, image_url)
    if image_bytes is None:
        return index, None

    ext = guess_image_extension(image_url)
    filename = f"{filename_prefix}_img{index:02d}{ext}"
    file_path = os.path.join(base_dir, filename)

    try:
        with open(file_path, "wb") as fout:
            fout.write(image_bytes)
        return index, filename
    except Exception as save_error:
        print(f"[WARN] failed to save image ({file_path}): {save_error}")
        return index, None


def download_and_save_images(
    http_session: requests.Session,
    image_url_list: List[str],
    filename_prefix: str,
) -> List[str]:
    global IMAGE_OUTPUT_DIR
    return common_download_and_save_images(
        http_session,
        image_url_list,
        filename_prefix,
        base_dir=IMAGE_OUTPUT_DIR or ".",
    )


def extract_image_urls_from_element(element, base_url: str) -> List[str]:
    return common_extract_image_urls_from_element(element, base_url)


# ==========
# パース用ヘルパ
# ==========

def parse_exam_labels(html_soup: BeautifulSoup) -> Tuple[str, str]:
    """
    ページの <title> から
    - exam_label: "令和5年度（2023年） 午後"
    - question_label: "問1 (一般問題 問1)"
    をゆるく取得する。
    """
    import re

    exam_label = ""
    question_label = ""

    title_tag = html_soup.find("title")
    if not title_tag:
        return exam_label, question_label

    title_text = title_tag.get_text(strip=True)

    # 末尾のサイト名「 - 過去問ドットコム」などを削る
    for sep in [" - ", "｜", " | "]:
        if sep in title_text:
            title_text = title_text.split(sep, 1)[0]
            break

    # 「過去問」以降の部分を取り出す
    middle_text = title_text
    m = re.search(r"過去問(.+)", title_text)
    if m:
        middle_text = m.group(1).strip()

    # 例:
    # "令和5年度（2023年） 午後 一般問題 問1"
    parts = middle_text.split()
    if not parts:
        return exam_label, question_label

    # 「問」で始まるトークン以降を問題ラベルとみなす
    q_start_index = None
    for i, part in enumerate(parts):
        if part.startswith("問"):
            q_start_index = i
            break

    if q_start_index is None:
        exam_label = middle_text.strip()
        question_label = ""
    else:
        exam_label = " ".join(parts[:q_start_index]).strip()
        question_label = " ".join(parts[q_start_index:]).strip()

    return exam_label, question_label


FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
JAPANESE_ERA_START_YEAR = {
    "令和": 2019,
    "平成": 1989,
    "昭和": 1926,
    "大正": 1912,
    "明治": 1868,
}


def normalize_digit_text(value: str) -> str:
    return (value or "").translate(FULLWIDTH_DIGIT_TRANS)


def parse_japanese_era_year(era_name: str, year_token: str) -> int | None:
    """
    和暦年を西暦へ変換する。
    例:
      - 令和5年 -> 2023
      - 平成元年 -> 1989
    """
    base_year = JAPANESE_ERA_START_YEAR.get(era_name)
    if base_year is None:
        return None

    normalized_year = normalize_digit_text(year_token).strip()
    if normalized_year == "元":
        era_year = 1
    elif normalized_year.isdigit():
        era_year = int(normalized_year)
    else:
        return None

    if era_year <= 0:
        return None

    return base_year + era_year - 1


def extract_exam_year_value(exam_label: str) -> int | None:
    """
    exam_label から examYear 用の西暦年を抽出する。
    西暦表記を優先し、なければ和暦を西暦へ変換する。
    """
    if not exam_label:
        return None

    normalized_label = normalize_digit_text(exam_label)

    western_year_match = re.search(r"((?:19|20)\d{2})\s*年(?:度)?", normalized_label)
    if western_year_match:
        return int(western_year_match.group(1))

    japanese_year_match = re.search(
        r"(令和|平成|昭和|大正|明治)\s*(元|[0-9０-９]+)\s*年(?:度)?",
        normalized_label,
    )
    if japanese_year_match:
        return parse_japanese_era_year(
            japanese_year_match.group(1),
            japanese_year_match.group(2),
        )

    return None


def extract_exam_month_value(exam_label: str) -> int | None:
    """
    exam_label から試験開催月を抽出する。年内複数回開催の識別に使う。
    """
    if not exam_label:
        return None

    month_match = re.search(r"([0-9０-９]{1,2})\s*月", exam_label)
    if not month_match:
        return None

    month = int(normalize_digit_text(month_match.group(1)))
    if 1 <= month <= 12:
        return month

    return None


def extract_exam_round_value(exam_label: str) -> int | None:
    """
    exam_label から「第N回」の回次を抽出する。
    """
    if not exam_label:
        return None

    round_match = re.search(r"第\s*([0-9０-９]+)\s*回", exam_label)
    if not round_match:
        return None

    return int(normalize_digit_text(round_match.group(1)))


def extract_exam_session_code(exam_label: str) -> str | None:
    """
    exam_label から共通セッションコードを抽出する。
    """
    if not exam_label:
        return None

    upper_label = normalize_digit_text(exam_label).upper()
    if "午前" in exam_label or re.search(r"\bAM\b", upper_label):
        return "am"
    if "午後" in exam_label or re.search(r"\bPM\b", upper_label):
        return "pm"
    if "前期" in exam_label:
        return "term1"
    if "後期" in exam_label:
        return "term2"
    if "上期" in exam_label:
        return "half1"
    if "下期" in exam_label:
        return "half2"
    if "春期" in exam_label:
        return "spring"
    if "秋期" in exam_label:
        return "autumn"
    return None


def extract_exam_variant_code(exam_label: str) -> str | None:
    """
    exam_label から追加試験などの特別な開催区分を抽出する。
    """
    if not exam_label:
        return None

    if "追加試験" in exam_label:
        return "extra"
    if "追試験" in exam_label or "追試" in exam_label:
        return "makeup"
    if "再試験" in exam_label or "再試" in exam_label:
        return "retest"
    return None


def build_exam_occurrence_id(exam_label: str) -> str | None:
    """
    exam_label から question 単位で保持する examOccurrenceId を生成する。

    ルール:
      - 年: YYYY
      - 月: YYYY-MM
      - 回次: YYYY-rN
      - セッション: am / pm / term1 / term2 / half1 / half2 / spring / autumn
      - 特別開催: extra / makeup / retest

    例:
      - "令和7年（2025年） 学科1（建築計画）" -> "2025"
      - "令和7年（2025年）10月 午後" -> "2025-10-pm"
      - "第8回（2025年） 午前" -> "2025-r8-am"
      - "第1回 追加試験（2018年） 午後" -> "2018-r1-extra-pm"
    """
    if not exam_label:
        return None

    parts: List[str] = []

    exam_year = extract_exam_year_value(exam_label)
    exam_month = extract_exam_month_value(exam_label)
    exam_round = extract_exam_round_value(exam_label)
    exam_variant = extract_exam_variant_code(exam_label)
    exam_session = extract_exam_session_code(exam_label)

    if exam_year is not None:
        parts.append(str(exam_year))
    if exam_month is not None:
        parts.append(f"{exam_month:02d}")
    if exam_round is not None:
        parts.append(f"r{exam_round}")
    if exam_variant:
        parts.append(exam_variant)
    if exam_session:
        parts.append(exam_session)

    if not parts:
        return None

    return "-".join(parts)


def extract_exam_year_text(exam_label: str) -> str | None:
    """
    exam_label から examYear 用の年月文字列を抽出する。
    例:
      - "令和7年（2025年）10月" -> "2025年10月"
      - "令和5年度（2023年） 午後" -> "2023年"
    """
    exam_year = extract_exam_year_value(exam_label)
    if exam_year is None:
        return None

    exam_month = extract_exam_month_value(exam_label)
    if exam_month is not None:
        return f"{exam_year}年{exam_month}月"

    return f"{exam_year}年"


SUPERSCRIPT_MAP = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "+": "⁺",
    "-": "⁻",
    "=": "⁼",
    "(": "⁽",
    ")": "⁾",
    "a": "ᵃ",
    "b": "ᵇ",
    "c": "ᶜ",
    "d": "ᵈ",
    "e": "ᵉ",
    "f": "ᶠ",
    "g": "ᵍ",
    "h": "ʰ",
    "i": "ⁱ",
    "j": "ʲ",
    "k": "ᵏ",
    "l": "ˡ",
    "m": "ᵐ",
    "n": "ⁿ",
    "o": "ᵒ",
    "p": "ᵖ",
    "r": "ʳ",
    "s": "ˢ",
    "t": "ᵗ",
    "u": "ᵘ",
    "v": "ᵛ",
    "w": "ʷ",
    "x": "ˣ",
    "y": "ʸ",
    "z": "ᶻ",
}

SUBSCRIPT_MAP = {
    "0": "₀",
    "1": "₁",
    "2": "₂",
    "3": "₃",
    "4": "₄",
    "5": "₅",
    "6": "₆",
    "7": "₇",
    "8": "₈",
    "9": "₉",
    "+": "₊",
    "-": "₋",
    "=": "₌",
    "(": "₍",
    ")": "₎",
    "a": "ₐ",
    "b": "ᵦ",
    "e": "ₑ",
    "h": "ₕ",
    "i": "ᵢ",
    "j": "ⱼ",
    "k": "ₖ",
    "l": "ₗ",
    "m": "ₘ",
    "n": "ₙ",
    "o": "ₒ",
    "p": "ₚ",
    "r": "ᵣ",
    "s": "ₛ",
    "t": "ₜ",
    "u": "ᵤ",
    "v": "ᵥ",
    "x": "ₓ",
    "y": "ᵧ",
}


def to_superscript(text: str) -> str:
    return "".join(SUPERSCRIPT_MAP.get(ch, SUPERSCRIPT_MAP.get(ch.lower(), ch)) for ch in text)


def to_subscript(text: str) -> str:
    return "".join(SUBSCRIPT_MAP.get(ch, SUBSCRIPT_MAP.get(ch.lower(), ch)) for ch in text)


def extract_text_with_subsup(element: Tag | NavigableString) -> str:
    """
    Extract text while preserving <sub>/<sup> as Unicode sub/superscripts.
    """
    block_tags = {"p", "div", "tr", "td", "th", "li"}

    def render(node: Tag | NavigableString) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if isinstance(node, Tag):
            name = node.name.lower()
            if name == "br":
                return "\n"
            if name == "sub":
                inner = "".join(render(child) for child in node.children).strip()
                return to_subscript(inner)
            if name == "sup":
                inner = "".join(render(child) for child in node.children).strip()
                return to_superscript(inner)
            text = "".join(render(child) for child in node.children)
            if name in block_tags:
                return text + "\n"
            return text
        return ""

    return render(element)


def collect_choice_texts_and_image_urls(
    list_elements: List[Tag],
    base_url: str,
) -> Tuple[List[str], List[List[str]], Set[str]]:
    """
    複数の <ul>/<ol> から choice index 対応の
    - 選択肢テキスト
    - 選択肢画像URL一覧（選択肢ごと）
    を抽出し、問題画像から除外するためのURL集合も返す。
    """
    choice_text_list: List[str] = []
    choice_image_url_list_by_choice: List[List[str]] = []
    choice_image_url_set: Set[str] = set()

    for list_element in list_elements:
        for list_item in list_element.find_all("li"):
            choice_text = normalize_inline_text(extract_text_with_subsup(list_item))
            choice_image_url_list = extract_image_urls_from_element(list_item, base_url)

            # テキストが空でも、画像があれば選択肢として扱う
            if not choice_text and not choice_image_url_list:
                continue

            choice_text_list.append(choice_text if choice_text else "")
            choice_image_url_list_by_choice.append(choice_image_url_list)
            for image_url in choice_image_url_list:
                choice_image_url_set.add(image_url)

    return choice_text_list, choice_image_url_list_by_choice, choice_image_url_set


def download_choice_images_by_choice(
    http_session: requests.Session,
    choice_image_url_list_by_choice: List[List[str]],
    public_qid: str,
) -> List[List[str]]:
    """
    選択肢ごとの画像URLを保存し、choice index 対応のファイル名配列を返す。
    """
    choice_image_filenames_by_choice: List[List[str]] = []
    for choice_index, image_url_list in enumerate(choice_image_url_list_by_choice, start=1):
        if not image_url_list:
            choice_image_filenames_by_choice.append([])
            continue
        filename_prefix = f"q{public_qid}_ch{choice_index:02d}"
        image_filenames = download_and_save_images(
            http_session,
            image_url_list,
            filename_prefix,
        )
        choice_image_filenames_by_choice.append(image_filenames)
    return choice_image_filenames_by_choice


def extract_question_body_and_choices(
    html_soup: BeautifulSoup,
    http_session: requests.Session,
    question_id: int,
    base_url: str,
) -> Tuple[str, List[str], List[str], List[List[str]]]:
    """
    「問題」部分（.sect_problem .problem_detail）から
    - 問題文
    - 選択肢（設問文）
    - 問題文内の画像ファイル名一覧
    を抽出する。
    """
    # まずは .sect_problem .problem_detail を優先して見る
    problem_detail = html_soup.select_one(".sect_problem .problem_detail")

    question_image_filenames: List[str] = []
    question_body_text = ""
    choice_text_list: List[str] = []
    choice_image_filenames_by_choice: List[List[str]] = []

    # question_id から公開用 ID を生成（画像ファイル名などに利用）
    public_qid = make_public_question_id(question_id)

    if problem_detail is not None:
        # 問題文
        ttl_element = problem_detail.select_one(".ttl")
        if ttl_element is not None:
            question_body_text = extract_text_with_subsup(ttl_element)
        else:
            # .ttl がない場合は、直下のテキスト要素から問題文らしき部分をまとめる
            body_lines: List[str] = []
            for child in problem_detail.children:
                child_name = getattr(child, "name", None)
                # 選択肢リストは除外
                if child_name in ("ul", "ol"):
                    continue
                get_text_function = getattr(child, "get_text", None)
                if get_text_function is None:
                    continue
                text = extract_text_with_subsup(child)
                if text:
                    body_lines.append(text)
            question_body_text = "\n".join(body_lines)
        question_body_text = normalize_question_body_text(question_body_text)

        # 選択肢
        # class="list" を優先
        list_elements = problem_detail.select("ul.list, ol.list")
        if not list_elements:
            list_elements = problem_detail.select("ul, ol")

        (
            choice_text_list,
            choice_image_url_list_by_choice,
            choice_image_url_set,
        ) = collect_choice_texts_and_image_urls(list_elements, base_url)
        choice_image_filenames_by_choice = download_choice_images_by_choice(
            http_session,
            choice_image_url_list_by_choice,
            public_qid,
        )

        # 問題本文側の画像のみ保存（選択肢 li 内の画像は除外）
        image_url_list = [
            image_url
            for image_url in extract_image_urls_from_element(problem_detail, base_url)
            if image_url not in choice_image_url_set
        ]
        filename_prefix = f"q{public_qid}_q"
        question_image_filenames = download_and_save_images(
            http_session,
            image_url_list,
            filename_prefix,
        )
        return (
            question_body_text,
            choice_text_list,
            question_image_filenames,
            choice_image_filenames_by_choice,
        )

    # 上記で取得できなかった場合は、従来の「問題」見出しベースの処理をフォールバックとして使う
    question_heading = html_soup.find(
        lambda tag: tag.name in ("h2", "h3")
        and tag.get_text(strip=True) == "問題"
    )
    if not question_heading:
        return "", [], [], []

    question_text_lines: List[str] = []
    choice_text_list = []
    question_image_url_list: List[str] = []
    choice_image_url_list_by_choice: List[List[str]] = []

    ignore_keywords_for_question = [
        "選択肢",
        "通常選択肢",
        "ランダム選択肢",
        "文字 の大きさ",
        "解答する",
        "第一種電気工事士試験",
        "訂正依頼・報告はこちら",
    ]

    has_seen_choice_list = False

    for sibling_element in question_heading.next_siblings:
        sibling_name = getattr(sibling_element, "name", None)

        # 解説の見出しに到達したら終了
        if sibling_name in ("h1", "h2", "h3"):
            break

        # 「解答する」ボタン付近で終わりにする
        get_text_function = getattr(sibling_element, "get_text", None)
        if get_text_function is not None:
            sibling_text = normalize_inline_text(
                extract_text_with_subsup(sibling_element)
            )
            if "解答する" in sibling_text:
                break

        # 選択肢 (ul / ol)
        if sibling_name in ("ul", "ol"):
            has_seen_choice_list = True
            (
                sibling_choice_texts,
                sibling_choice_images,
                _,
            ) = collect_choice_texts_and_image_urls([sibling_element], base_url)
            choice_text_list.extend(sibling_choice_texts)
            choice_image_url_list_by_choice.extend(sibling_choice_images)
            # 選択肢の画像は questionImageStorageUrls に含めない
            continue

        # 問題文候補
        if not has_seen_choice_list and get_text_function is not None:
            sibling_text = normalize_inline_text(
                extract_text_with_subsup(sibling_element)
            )
            if not sibling_text:
                continue
            if any(keyword in sibling_text for keyword in ignore_keywords_for_question):
                continue
            question_text_lines.append(sibling_text)
            question_image_url_list.extend(
                extract_image_urls_from_element(sibling_element, base_url)
            )

    # 選択肢の末尾に「1」「2」「3」「4」のような番号だけの行があるので削除
    while choice_text_list and choice_text_list[-1].strip().isdigit():
        choice_text_list.pop()
        if choice_image_url_list_by_choice:
            choice_image_url_list_by_choice.pop()

    # 問題文の重複行を削って結合
    unique_question_lines: List[str] = []
    seen_lines: Set[str] = set()
    for text_line in question_text_lines:
        if text_line not in seen_lines:
            seen_lines.add(text_line)
            unique_question_lines.append(text_line)

    question_body_text = normalize_question_body_text("\n".join(unique_question_lines))

    # 画像を保存
    filename_prefix = f"q{public_qid}_q"
    question_image_filenames = download_and_save_images(
        http_session,
        question_image_url_list,
        filename_prefix,
    )

    choice_image_filenames_by_choice = download_choice_images_by_choice(
        http_session,
        choice_image_url_list_by_choice,
        public_qid,
    )

    return (
        question_body_text,
        choice_text_list,
        question_image_filenames,
        choice_image_filenames_by_choice,
    )


def normalize_question_body_text(text: str) -> str:
    return common_normalize_question_body_text(text)


def normalize_inline_text(text: str) -> str:
    return common_normalize_inline_text(text)


def extract_hidden_input_value(
    html_soup: BeautifulSoup,
    *,
    input_id: str | None = None,
    input_name: str | None = None,
) -> str:
    """
    hidden input の value を取得する。見つからない場合は空文字を返す。
    """
    target = None
    if input_id:
        target = html_soup.find("input", id=input_id)
    if target is None and input_name:
        target = html_soup.find("input", attrs={"name": input_name})
    if target is None:
        return ""
    return (target.get("value") or "").strip()


def extract_answer_input_numbers(html_soup: BeautifulSoup) -> List[int]:
    """
    回答用 radio input（name="intAnswerData"）から選択肢番号を抽出する。
    """
    numbers: List[int] = []
    for radio_input in html_soup.select('input[name="intAnswerData"]'):
        value = (radio_input.get("value") or "").strip()
        if value.isdigit():
            number = int(value)
            if number not in numbers:
                numbers.append(number)
    return numbers


def html_fragment_to_plain_text(html_fragment: str) -> str:
    """
    HTML断片をプレーンテキストへ変換する。
    """
    if not html_fragment:
        return ""
    fragment_soup = BeautifulSoup(html_fragment, "html.parser")
    text = normalize_inline_text(extract_text_with_subsup(fragment_soup))
    return text.strip()


def fetch_answer_result_data(
    http_session: requests.Session,
    html_soup: BeautifulSoup,
    question_url: str,
) -> AnswerResultData | None:
    """
    questions/answer の AJAX 応答から、回答後に表示される正答情報を取得する。
    """
    csrf_token = ""
    csrf_meta = html_soup.find("meta", attrs={"name": "csrf-token"})
    if csrf_meta is not None:
        csrf_token = (csrf_meta.get("content") or "").strip()
    if not csrf_token:
        csrf_token = extract_hidden_input_value(html_soup, input_name="_token")

    study_random_id = extract_hidden_input_value(html_soup, input_id="intStudyRandumId")
    if not study_random_id:
        study_random_id = extract_hidden_input_value(html_soup, input_name="StudyRandumId")
    if not study_random_id:
        # 一部ページでは hidden input が欠けている場合がある。
        # 通常は URL の末尾数値がそのまま StudyRandumId として使える。
        try:
            url_path = urlparse(question_url).path
            study_random_id = url_path.rstrip("/").split("/")[-1]
        except Exception:
            study_random_id = ""
    category_flag = extract_hidden_input_value(html_soup, input_id="intIdCategoryFlag")
    answer_numbers = extract_answer_input_numbers(html_soup)

    if not csrf_token or not study_random_id:
        return None

    if answer_numbers:
        selected_choice_numbers = [answer_numbers[0]]
    else:
        # ページ側の回答 UI が差分で変わっていても、回答結果を返す仕様は利用できるため
        # 代表として「1」を選択した体で POST して正答情報を取得する。
        selected_choice_numbers = [1]
    post_data = {
        "strAnswerData": "-".join(str(num) for num in selected_choice_numbers) + "-",
        "intStudyRandumId": study_random_id,
        "intIdCategoryFlag": category_flag,
    }
    answer_url = urljoin(question_url, "/questions/answer")
    headers = {
        "X-CSRF-TOKEN": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": question_url,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    for retry_index in range(2):
        try:
            slow_down(0.15, 0.15)
            response = http_session.post(
                answer_url,
                data=post_data,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()

            response_data03 = str(payload.get("response_data03") or "").strip()
            response_data_for_rdm = str(payload.get("response_data_for_rdm") or "").strip()
            answer_result_html = "\n".join(
                part for part in [response_data03, response_data_for_rdm] if part
            ).strip()
            answer_result_text = html_fragment_to_plain_text(answer_result_html)

            response_data02 = str(payload.get("response_data02") or "").strip()
            is_selected_choice_correct: bool | None = None
            if any(marker in response_data02 for marker in ("○", "〇", "◯")):
                is_selected_choice_correct = True
            elif any(marker in response_data02 for marker in ("×", "✕", "✖", "❌")):
                is_selected_choice_correct = False

            inferred_correct_choice_numbers = find_correct_choice_numbers_in_text(
                answer_result_text
            )

            return AnswerResultData(
                answer_result_text=answer_result_text,
                answer_result_html=answer_result_html,
                selected_choice_numbers=selected_choice_numbers,
                is_selected_choice_correct=is_selected_choice_correct,
                inferred_correct_choice_numbers=inferred_correct_choice_numbers,
            )
        except Exception as answer_error:
            print(f"[WARN] answer result fetch failed ({question_url}): {answer_error}")
            if retry_index == 1:
                return None
            slow_down(0.3, 0.3)

    return None


def update_correct_choices_from_text(
    explanation_text: str,
    correct_choice_number_set: Set[int],
) -> None:
    """
    解説本文の中から「選択肢3」「正解です」などを手掛かりに
    正解の選択肢番号を推論して追加する。
    """
    import re

    last_choice_number: int | None = None

    for text_line in explanation_text.splitlines():
        stripped_line = text_line.strip()

        match_choice = re.search(r"選択肢\s*(\d+)", stripped_line)
        if match_choice:
            last_choice_number = int(match_choice.group(1))
            continue

        if "正解" in stripped_line and "不正解" not in stripped_line:
            if last_choice_number is not None:
                correct_choice_number_set.add(last_choice_number)


def normalize_explanation_text_for_choices(explanation_text: str) -> str:
    """
    解説本文中の「１．」「1．」「１→」「1→」「1、」のような
    選択肢番号の見出しを「選択肢1.」形式に正規化する。
    これにより、後続の処理（選択肢ごとの切り出しなど）を
    既存の "選択肢n." ベースのロジックで共通化できる。
    """
    import re

    if not explanation_text:
        return explanation_text

    fullwidth_digits = "０１２３４５６７８９"
    halfwidth_digits = "0123456789"
    trans_table = str.maketrans(fullwidth_digits, halfwidth_digits)

    # 行頭（または改行直後）に現れる「数字 + 区切り記号（．/./:/:/→/、）」を検出
    # ユーザー指摘の「1、」に対応するため「、」を追加
    # 修正: 小数点（例: 2.5倍）を誤検知しないよう、区切り文字の直後に数字が来ないことを条件に追加 (?![0-9０-９])
    pattern = re.compile(
        r'(^|\n)([ \t　]*)([0-9０-９]+)[ \t　]*([\.．:：→、])(?![0-9０-９])([^\n]*)'
    )

    def repl(match):
        prefix = match.group(1) or ""
        spaces = match.group(2) or ""
        num_str = match.group(3)
        tail = match.group(5) or ""

        num_ascii = num_str.translate(trans_table)

        # tail は元の行の残り。先頭にスペースがなければ 1 つ追加してから付ける。
        tail_str = tail
        if tail_str and not tail_str.startswith((" ", "　")):
            tail_str = " " + tail_str

        # 「１→説明文」 -> 「選択肢1. 説明文」
        return f"{prefix}{spaces}選択肢{num_ascii}.{tail_str}"

    return pattern.sub(repl, explanation_text)


def extract_explanations_and_correct_choices(
    html_soup: BeautifulSoup,
    http_session: requests.Session,
    question_id: int,
    base_url: str,
) -> Tuple[List[ExplanationData], List[int]]:
    """
    解説部分（.sect_commentary .commentary_wrap .item もしくは
    「この過去問の解説（n件）」以降）から
    - 解説 01 / 02 / 03 ...
    - 正解の選択肢番号
    - 解説ごとの画像ファイル名
    を抽出する。
    """
    # question_id から公開用 ID を生成（画像ファイル名などに利用）
    public_qid = make_public_question_id(question_id)

    # まずは .sect_commentary .commentary_wrap .item を優先
    items = html_soup.select(".sect_commentary .commentary_wrap .item")
    if items:
        explanation_data_list: List[ExplanationData] = []
        correct_choice_number_set: Set[int] = set()

        for idx, item in enumerate(items, start=1):
            num_el = item.select_one(".num")
            text_el = item.select_one(".text") or item

            index_label = ""
            if num_el is not None:
                index_label = num_el.get_text(strip=True)
            if not index_label:
                # インデックスが取れない場合は 01, 02, ... を自動採番
                index_label = f"{idx:02d}"

            explanation_text = normalize_question_body_text(
                extract_text_with_subsup(text_el)
            )
            if not explanation_text:
                continue

            # 「１．」「１→」形式などを "選択肢1." に正規化
            explanation_text = normalize_explanation_text_for_choices(explanation_text)

            # 画像を取得 & 保存
            image_url_list = extract_image_urls_from_element(item, base_url)
            filename_prefix = f"q{public_qid}_exp{index_label}"
            image_filenames = download_and_save_images(
                http_session,
                image_url_list,
                filename_prefix,
            )

            explanation_data_list.append(
                ExplanationData(
                    explanation_index_label=index_label,
                    explanation_body_text=explanation_text,
                    image_filenames=image_filenames,
                )
            )
            update_correct_choices_from_text(
                explanation_text,
                correct_choice_number_set,
            )

        return explanation_data_list, sorted(correct_choice_number_set)

    # 上記で取得できなかった場合は、従来の見出しベースの処理をフォールバックとして使う
    explanation_heading = html_soup.find(
        lambda tag: tag.name in ("h2", "h3")
        and "この過去問の解説" in tag.get_text(strip=True)
    )
    if not explanation_heading:
        return [], []

    explanation_data_list = []
    correct_choice_number_set: Set[int] = set()

    current_index_label = ""
    current_lines: List[str] = []
    current_image_url_list: List[str] = []

    def flush_current_block() -> None:
        nonlocal current_index_label, current_lines, current_image_url_list
        if current_index_label and (current_lines or current_image_url_list):
            explanation_text = "\n".join(current_lines).strip()
            # 「１．」「１→」形式などを "選択肢1." に正規化
            explanation_text = normalize_explanation_text_for_choices(explanation_text)
            filename_prefix = f"q{public_qid}_exp{current_index_label}"
            image_filenames = download_and_save_images(
                http_session,
                current_image_url_list,
                filename_prefix,
            )
            explanation_data_list.append(
                ExplanationData(
                    explanation_index_label=current_index_label,
                    explanation_body_text=explanation_text,
                    image_filenames=image_filenames,
                )
            )
            update_correct_choices_from_text(
                explanation_text,
                correct_choice_number_set,
            )
        current_index_label = ""
        current_lines = []
        current_image_url_list = []

    for sibling_element in explanation_heading.next_siblings:
        sibling_name = getattr(sibling_element, "name", None)
        if sibling_name in ("h1", "h2", "h3"):
            break

        get_text_function = getattr(sibling_element, "get_text", None)
        if get_text_function is None:
            continue

        sibling_text = normalize_question_body_text(
            extract_text_with_subsup(sibling_element)
        ).strip()
        if not sibling_text:
            continue
        if "Advertisement" in sibling_text:
            continue

        # 「01」「02」などインデックスだけの行
        if sibling_text.isdigit() and len(sibling_text) == 2:
            flush_current_block()
            current_index_label = sibling_text
            current_lines = []
            current_image_url_list = []
            continue

        current_lines.append(sibling_text)
        current_image_url_list.extend(
            extract_image_urls_from_element(sibling_element, base_url)
        )

    flush_current_block()
    return explanation_data_list, sorted(correct_choice_number_set)


def parse_question_page(
    html_text: str,
    question_url: str,
    http_session: requests.Session,
) -> QuestionData:
    html_soup = BeautifulSoup(html_text, "html.parser")

    url_path = urlparse(question_url).path   # "/questions/75936"
    question_id = int(url_path.rstrip("/").split("/")[-1])

    exam_label, question_label = parse_exam_labels(html_soup)
    (
        question_body_text,
        raw_choice_text_list,
        question_image_filenames,
        choice_image_filenames_by_choice,
    ) = (
        extract_question_body_and_choices(
            html_soup,
            http_session,
            question_id,
            question_url,
        )
    )

    # 「選択肢{番号}.」を先頭に付けた形に整形していたが、
    # ユーザー要望によりプレフィックスを除外する（raw_choice_text_list をそのまま使う）
    # numbered_choice_text_list = [
    #     f"選択肢{idx}.{choice}"
    #     for idx, choice in enumerate(raw_choice_text_list, start=1)
    # ]

    explanation_data_list, correct_choice_numbers = extract_explanations_and_correct_choices(
        html_soup,
        http_session,
        question_id,
        question_url,
    )

    answer_result_data = fetch_answer_result_data(
        http_session,
        html_soup,
        question_url,
    )
    if (
        answer_result_data is not None
        and answer_result_data.inferred_correct_choice_numbers
    ):
        correct_choice_numbers = list(answer_result_data.inferred_correct_choice_numbers)

    return QuestionData(
        question_url=question_url,
        question_id=question_id,
        exam_label=exam_label,
        question_label=question_label,
        question_body_text=question_body_text,
        choice_text_list=raw_choice_text_list,
        correct_choice_numbers=correct_choice_numbers,
        answer_result_data=answer_result_data,
        explanations=explanation_data_list,
        question_image_filenames=question_image_filenames,
        choice_image_filenames_by_choice=choice_image_filenames_by_choice,
    )


# ==========
# 解説テキストから「選択肢n」部分だけを抜き出すヘルパ
# ==========

def extract_choice_explanation_snippets_for_index(
    explanations: List[ExplanationData],
    sub_question_index: int,
) -> List[str]:
    """
    explanations の各 explanation_body_text から、
    「選択肢{sub_question_index}.」に対応する部分だけを切り出して
    リストで返す。
    """
    import re

    target = str(sub_question_index)
    pattern_all = re.compile(r"選択肢(\d+)\.")  # 例: 選択肢1. / 選択肢2.

    snippets: List[str] = []

    for exp in explanations:
        text = exp.explanation_body_text
        matches = list(pattern_all.finditer(text))
        if not matches:
            continue

        start_idx = None
        end_idx = None

        for i, m in enumerate(matches):
            choice_num = m.group(1)
            if choice_num == target:
                start_idx = m.start()
                if i + 1 < len(matches):
                    end_idx = matches[i + 1].start()
                else:
                    end_idx = len(text)
                break

        if start_idx is not None:
            snippet = text[start_idx:end_idx].strip()
            if snippet:
                snippets.append(snippet)

    return snippets


def is_combination_choice_problem(choice_text_list: List[str]) -> bool:
    """
    「アとウ」「1、3」など、選択肢自体が組合せ命題になっている問題かを判定する。
    組合せ問題では、設問文に「誤っているもの」が含まれていても、正答の組合せ選択肢は
    correctChoiceText として「正しい」と扱う。
    """
    import re

    if len(choice_text_list) < 2:
        return False

    token = r"[ア-ンA-Za-zⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ一二三四五六七八九十0-9０-９]+"
    separator = r"(?:、|,|・|と|及び|および)"
    combination_pattern = re.compile(rf"^{token}(?:\s*{separator}\s*{token})+$")

    combination_count = 0
    for choice_text in choice_text_list:
        normalized = (choice_text or "").strip()
        if len(normalized) <= 24 and combination_pattern.fullmatch(normalized):
            combination_count += 1

    return combination_count >= max(2, len(choice_text_list) - 1)


def answer_marker_to_fact_correctness(
    is_answer_choice: bool,
    question_intent: str | None,
) -> str:
    """
    「正解です」「不正解です」のような Answer ラベルを、選択肢内容の Fact 正誤へ変換する。
    select_incorrect では Answer と Fact が反転する。
    """
    if is_answer_choice:
        if question_intent == "select_incorrect":
            return "間違い"
        return "正しい"

    if question_intent == "select_incorrect":
        return "正しい"
    return "間違い"


def infer_correct_choice_texts_from_answer_numbers(
    current_correct_choice_texts: List[str | None],
    answer_numbers: List[int],
    question_intent: str | None,
) -> List[str | None]:
    """
    解説で示された正答番号（Answer）と設問意図から correctChoiceText（Fact）を再構成する。
    None 混在、または全肢同一のように抽出結果が不安定な場合だけ呼び出す想定。
    """
    if question_intent not in {"select_correct", "select_incorrect"}:
        return current_correct_choice_texts

    answer_number_set = {
        n for n in answer_numbers if 1 <= n <= len(current_correct_choice_texts)
    }
    if not answer_number_set:
        return current_correct_choice_texts

    # ルール（絶対）:
    # - select_correct   → 正解番号の位置が「正しい」
    # - select_incorrect → 正解番号の位置が「間違い」
    if question_intent == "select_incorrect":
        return [
            "間違い" if (idx + 1) in answer_number_set else "正しい"
            for idx in range(len(current_correct_choice_texts))
        ]

    return [
        "正しい" if (idx + 1) in answer_number_set else "間違い"
        for idx in range(len(current_correct_choice_texts))
    ]


def determine_correctness_from_snippets(
    snippets: List[str],
    question_intent: str | None = None,
    is_combination_choice: bool = False,
) -> str | None:
    """
    解説スニペットから正誤（正しい/間違い）を判定する。
    """
    import re
    if not snippets:
        return None

    # 0. 先頭のマーカーによる即時判定（精度高）
    # 「選択肢1. ×」「選択肢1. 誤）」などで始まる場合は即決する
    for s in snippets:
        if not s:
            continue
        # "選択肢n." や空白を除去して先頭を確認
        # 行頭にある "選択\d+." を削除
        clean_s = re.sub(r"^選択肢\d+\.\s*", "", s.strip())
        # さらに選択肢テキスト（改行まで）を除去して次の行を確認
        lines = clean_s.split('\n')
        if len(lines) > 1:
            second_line = lines[1].strip() if len(lines) > 1 else ""
        else:
            second_line = ""
        
        # 否定マーカー（先頭チェック）
        # "×" (U+00D7), "✕" (U+2715), "✖", "❌", "❎", "✗", "✘"
        neg_markers = ("×", "✕", "✖", "❌", "❎", "✗", "✘", "誤）", "誤)", "誤\n", "誤。")
        if clean_s.startswith(neg_markers) or second_line.startswith(neg_markers):
            return "間違い"
        
        # 肯定マーカー（先頭チェック）
        pos_markers = ("〇", "○", "◎", "◯", "正）", "正)", "正\n", "正。")
        if clean_s.startswith(pos_markers) or second_line.startswith(pos_markers):
            return "正しい"

    # 判定精度向上のため、改行や空白を除去して連結
    normalized_text = "".join(snippets).replace("\n", "").replace(" ", "").replace("　", "")
    
    # 1. 最優先: 「正解です」「不正解です」の明確な Answer 判定
    # correctChoiceText は Fact 正誤なので、select_incorrect では Answer と Fact を反転する。
    non_answer_markers = [
        "不正解です",
        "不正答です",
        "誤答です",
        "正解ではありません",
        "正答ではありません",
    ]
    answer_markers = [
        "正解です",
        "正答です",
        "正解となります",
        "正答となります",
        "こちらが正解",
        "こちらが正答",
        "この選択肢が正解",
        "この選択肢が正答",
    ]
    if any(marker in normalized_text for marker in non_answer_markers):
        return answer_marker_to_fact_correctness(
            False,
            question_intent,
        )
    if any(marker in normalized_text for marker in answer_markers):
        return answer_marker_to_fact_correctness(
            True,
            question_intent,
        )
    
    # 2. 明確な誤りを示すフレーズ（「不適当」「不適切」を含む）
    # これらは「適当」「適切」より先にチェックする必要がある
    strong_negative_phrases = [
        "不適当な記述", "不適当です", "不適当である",
        "不適切な記述", "不適切です", "不適切である",
        "記述は誤り", "記述は誤っ", "誤った記述", "誤っている記述",
        "記述は間違い", "間違いです", "間違っています",
        "誤りです", "誤っています", "誤りである",
        "適切ではない", "適切でない", "適当ではない", "適当でない",
        "正しくない", "正しくありません",
    ]
    
    # 3. 明確な正しさを示すフレーズ
    strong_positive_phrases = [
        "記述は正しい", "正しい記述", "記述は適切", "適切な記述",
        "適切です", "正しいです", "合っています",
        "記述は適当", "適当な記述", "適当です",
        "正しい", "適切である", "適当である",
    ]

    # 誤りフレーズを先にチェック（「不適切」が「適切」にマッチしないように）
    positive_exception_phrases = [
        "不適切とは言えません",
        "不適切とはいえません",
        "不適切ではありません",
        "不適当とは言えません",
        "不適当とはいえません",
        "不適当ではありません",
        "誤りではありません",
        "間違いではありません",
    ]
    if any(p in normalized_text for p in positive_exception_phrases):
        return "正しい"

    has_strong_negative = any(p in normalized_text for p in strong_negative_phrases)
    has_strong_positive = any(p in normalized_text for p in strong_positive_phrases)

    # 「不適」「誤」などが含まれる場合は、strong_positive より優先して誤りとする
    negative_override_keywords = ["不適", "誤り", "誤っ", "間違", "正しくな"]
    has_negative_override = any(k in normalized_text for k in negative_override_keywords)
    
    if has_strong_negative:
        return "間違い"
    
    if has_negative_override and has_strong_positive:
        # 「正しい記述ではない」「適切とは言えない」などのパターン
        denial_patterns = [
            "正しいとは言えない", "適切とは言えない", "適当とは言えない",
            "正しいとはいえない", "適切とはいえない", "適当とはいえない",
            "正しくはない", "適切ではない", "適当ではない",
        ]
        if any(p in normalized_text for p in denial_patterns):
            return "間違い"
        return None
    
    if has_strong_positive and not has_negative_override:
        return "正しい"
    
    # 4. キーワードによる判定（フォールバック）
    # 順序重要: 否定キーワードを先にチェック
    negative_keywords = [
        "間違い", "間違", "誤り", "誤っ", "誤",
        "×", "✕", "✖", "❌", "❎", "✗", "✘",
        "不適", "満たしません", "満たさない", "成立しません", "成立しない", "矛盾",
        "できません", "できない", "ありません", "ない",
    ]
    
    positive_keywords = [
        "正しい", "正解", "正。", "〇", "○", "◎", "◯",
        "適切", "適当", "妥当",
    ]

    # 否定キーワードのチェック
    has_negative = any(k in normalized_text for k in negative_keywords)
    
    # 肯定キーワードのチェック
    # ただし「不適切」「不適当」が含まれる場合は「適切」「適当」を肯定とみなさない
    has_positive = False
    if not any(k in normalized_text for k in ["不適切", "不適当", "不適"]):
        has_positive = any(k in normalized_text for k in positive_keywords)
    else:
        # 「不適」が含まれる場合でも、他の肯定キーワードをチェック
        non_conflict_positive = ["正しい", "正解", "正。", "〇", "○", "◎", "◯", "妥当"]
        has_positive = any(k in normalized_text for k in non_conflict_positive)

    # 両方のキーワードがある場合はnullとする
    if has_negative and has_positive:
        return None

    if has_negative:
        return "間違い"

    if has_positive:
        return "正しい"
    
    return None


def find_correct_choice_numbers_in_text(text: str) -> List[int]:
    """
    テキスト内から「正解は3」「正答は1、3」のようなパターンを探して、
    正答番号のリストを返す。数字だけの正解表示は選択肢番号として扱う。
    """
    import re
    if not text:
        return []

    digit_map = {
        "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
        "６": "6", "７": "7", "８": "8", "９": "9",
        "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
        "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9",
    }
    digit_chars = "1-9１-９①②③④⑤⑥⑦⑧⑨"
    separator = r"(?:、|,|・|と|及び|および|/|／|\s+)"
    pattern = re.compile(
        rf"(?:正解|正答|答え|解答)\s*(?:は|:|：|、|。)?\s*(?:選択肢)?\s*"
        rf"([{digit_chars}](?:\s*{separator}\s*[{digit_chars}])*)"
    )

    numbers: List[int] = []
    for match in pattern.finditer(text):
        for digit in re.findall(rf"[{digit_chars}]", match.group(1)):
            normalized_digit = digit_map.get(digit, digit)
            number = int(normalized_digit)
            if number not in numbers:
                numbers.append(number)
    return numbers


def find_correct_choice_number_in_text(text: str) -> int | None:
    """
    互換用: 最初に見つかった正答番号を返す。複数正答の補完では
    find_correct_choice_numbers_in_text を使う。
    """
    numbers = find_correct_choice_numbers_in_text(text)
    return numbers[0] if numbers else None


def determine_question_intent(question_text: str) -> str | None:
    """
    問題文から「正しいものを選ぶ」のか「誤っているものを選ぶ」のかを判定する。
    select_incorrect に該当する表現を優先し、それ以外は select_correct とする。
    戻り値: "select_correct", "select_incorrect", or None
    """
    if not question_text:
        return None
    
    import re

    # 判定を容易にするため空白と改行を圧縮する
    text = re.sub(r"\s+", "", question_text)

    incorrect_patterns = [
        r"最も不適切(?:なもの|な記述|な説明|な組合せ|な選択肢)?",
        r"最も不適当(?:なもの|な記述|な説明|な組合せ|な選択肢)?",
        r"誤っている(?:もの|記述|説明|組合せ|選択肢)?",
        r"誤り(?:である)?(?:もの|記述|説明|組合せ|選択肢)?",
        r"間違っている(?:もの|記述|説明|組合せ|選択肢)?",
        r"正しくない(?:もの|記述|説明|組合せ|選択肢)?",
        r"不適切(?:な|である)?(?:もの|記述|説明|組合せ|選択肢|対応|方法|処置|行動|内容)?",
        r"不適当(?:な|である)?(?:もの|記述|説明|組合せ|選択肢|対応|方法|処置|行動|内容)?",
        r"適切でない(?:もの|記述|説明|組合せ|選択肢)?",
        r"適当でない(?:もの|記述|説明|組合せ|選択肢)?",
        r"含まれないもの",
        r"該当しないもの",
        r"規定されていないもの",
        r"定められていないもの",
        r"対象とならないもの",
    ]
    has_incorrect = any(re.search(pattern, text) for pattern in incorrect_patterns)
    if has_incorrect:
        return "select_incorrect"

    return "select_correct"


# ==========
# 解説テキストから「導入部分」と「まとめ部分」を抜き出すヘルパ
# ==========

def extract_common_explanation_parts(
    explanations: List[ExplanationData],
) -> Tuple[List[str], List[str]]:
    """
    各 explanation_body_text から
    - 最初の「選択肢n.」より前のテキスト（導入部分）
    - 最後の「選択肢n.」以降の「まとめ」以降のテキスト
    を抽出して、問題全体に紐づく情報として返す。
    戻り値: (prefix_list, summary_list)
    """
    import re

    pattern_choice = re.compile(r"選択肢(\d+)\.")
    pattern_summary = re.compile(r"(?:^|\n)\s*まとめ[^\n]*")

    prefix_list: List[str] = []
    summary_list: List[str] = []

    for exp in explanations:
        text = exp.explanation_body_text
        if not text:
            continue

        matches = list(pattern_choice.finditer(text))
        if matches:
            # 導入部分: 最初の「選択肢n.」より前
            prefix = text[:matches[0].start()].strip()
            if prefix:
                prefix_list.append(f"[{exp.explanation_index_label}] {prefix}")

            # まとめ部分: 最後の選択肢以降の「まとめ」から末尾まで
            tail = text[matches[-1].end():]
            m_sum = pattern_summary.search(tail)
            if m_sum:
                summary = tail[m_sum.start():].strip()
                if summary:
                    summary_list.append(f"[{exp.explanation_index_label}] {summary}")
        else:
            # 「選択肢n.」が一切ない解説は、全体を導入扱い
            stripped = text.strip()
            if stripped:
                prefix_list.append(f"[{exp.explanation_index_label}] {stripped}")

    return prefix_list, summary_list


# ==========
# 問題一覧ページ関連
# ==========

def get_total_pages_from_list_page(html_soup: BeautifulSoup) -> int:
    """
    一覧ページに表示されている
    「全2ページ中1ページ目です。」といったテキストから総ページ数を取得する。
    見つからない場合は 1 を返す。
    """
    import re

    text_node = html_soup.find(
        string=lambda s: s and "ページ中" in s and "ページ目です" in s
    )
    if not text_node:
        return 1

    m = re.search(r"全\s*(\d+)\s*ページ中\s*(\d+)\s*ページ目です", text_node)
    if not m:
        return 1

    total_pages = int(m.group(1))
    return max(total_pages, 1)


def build_list_page_url(first_page_url: str, page_number: int) -> str:
    """
    1ページ目のURLから、指定ページ番号のURLを組み立てる。
    例:
      https://1denkikoujishi.kakomonn.com/list1/71013?page=1
      -> page_number=2 のとき
         https://1denkikoujishi.kakomonn.com/list1/71013?page=2
    """
    parsed = urlparse(first_page_url)
    query_dict = parse_qs(parsed.query)
    query_dict["page"] = [str(page_number)]
    new_query = urlencode(query_dict, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def extract_question_urls_from_list_page(
    html_soup: BeautifulSoup,
    list_page_url: str,
) -> List[str]:
    """
    一覧ページ中の「問題文へのリンク」の部分から
    /questions/xxxx 形式のURLをすべて抜き出す。
    """
    parsed_list_url = urlparse(list_page_url)
    base_scheme = parsed_list_url.scheme
    base_netloc = parsed_list_url.netloc

    question_url_list: List[str] = []
    seen: Set[str] = set()

    for a in html_soup.find_all("a", href=True):
        href = a["href"]
        parsed_href = urlparse(href)

        # 相対パスの場合は一覧ページと同じドメインにする
        if not parsed_href.netloc:
            href_netloc = base_netloc
            href_path = parsed_href.path
        else:
            href_netloc = parsed_href.netloc
            href_path = parsed_href.path

        if href_netloc != base_netloc:
            continue
        if not href_path.startswith("/questions/"):
            continue

        full_url = f"{base_scheme}://{href_netloc}{href_path}"
        if parsed_href.query:
            full_url = f"{full_url}?{parsed_href.query}"

        if full_url not in seen:
            seen.add(full_url)
            question_url_list.append(full_url)

    return question_url_list


def collect_question_urls_from_all_list_pages(
    http_session: requests.Session,
    first_list_page_url: str,
) -> List[str]:
    """
    一覧1ページ目のURLから始めて、全ページ分の問題URLを取得する。
    """
    question_urls: List[str] = []

    # 1ページ目
    first_html = fetch_html_text(http_session, first_list_page_url)
    first_soup = BeautifulSoup(first_html, "html.parser")
    total_pages = get_total_pages_from_list_page(first_soup)

    print(f"[INFO] list total pages = {total_pages}")

    question_urls.extend(
        extract_question_urls_from_list_page(first_soup, first_list_page_url)
    )

    # 2ページ目以降
    for page_number in range(2, total_pages + 1):
        page_url = build_list_page_url(first_list_page_url, page_number)
        print(f"[INFO] fetching list page {page_number}: {page_url}")
        html_text = fetch_html_text(http_session, page_url)
        html_soup = BeautifulSoup(html_text, "html.parser")
        question_urls.extend(
            extract_question_urls_from_list_page(html_soup, page_url)
        )

    # 念のため重複排除（順序は保持）
    unique_question_urls: List[str] = []
    seen: Set[str] = set()
    for url in question_urls:
        if url not in seen:
            seen.add(url)
            unique_question_urls.append(url)

    print(f"[INFO] collected question urls = {len(unique_question_urls)}")
    return unique_question_urls


def update_existing_files(
    json_output_dir: str,
    new_questions_list: List[dict],
    new_bodies_dict: dict,
    new_bodies_empty_dict: dict,
) -> None:
    """
    指定されたディレクトリ内のJSONファイルを走査し、
    今回生成したデータに含まれるURLを持つ既存データを上書き保存する。
    """
    if not os.path.exists(json_output_dir):
        print(f"[WARN] Directory not found: {json_output_dir}")
        return

    # 更新対象のURLセットを作成
    target_urls = set()
    
    # URL -> Body のマッピングを作成（Normal / Empty 別）
    url_to_normal_body = {}
    for body in new_bodies_dict.values():
        u = body.get("question_url")
        if u:
            target_urls.add(u)
            url_to_normal_body[u] = body

    url_to_empty_body = {}
    for body in new_bodies_empty_dict.values():
        u = body.get("question_url")
        if u:
            target_urls.add(u)
            url_to_empty_body[u] = body

    # questions_summary 用のマッピング (url -> list of question_data)
    url_to_questions = {}
    for q in new_questions_list:
        u = q.get("question_url")
        if u:
            target_urls.add(u)
            if u not in url_to_questions:
                url_to_questions[u] = []
            url_to_questions[u].append(q)

    if not target_urls:
        print("[INFO] No data to update.")
        return

    print(f"[INFO] Start batch update for {len(target_urls)} URLs.")

    # 1. questions_summary_*.json の更新
    summary_files = glob.glob(os.path.join(json_output_dir, "questions_summary_*.json"))
    for fpath in summary_files:
        updated = False
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            questions = data.get("questions", [])
            if not questions:
                continue

            new_list = []
            processed_urls_in_this_file = set()
            
            for q in questions:
                u = q.get("question_url")
                if u in target_urls:
                    # 更新対象
                    if u not in processed_urls_in_this_file:
                        # 新しいデータを挿入 (設問数分)
                        if u in url_to_questions:
                            new_list.extend(url_to_questions[u])
                        processed_urls_in_this_file.add(u)
                        updated = True
                    # 既に挿入済みならスキップ (古いデータは削除されることになる)
                else:
                    # 更新対象でないならそのまま維持
                    new_list.append(q)
            
            if updated:
                data["questions"] = new_list
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[SUCCESS] Updated {os.path.basename(fpath)}")

        except Exception as e:
            print(f"[ERROR] Failed to update {fpath}: {e}")

    # 2. question_*_*.json / question_*_empty_*.json の更新
    # ファイル種別ごとに処理を分ける
    all_body_files = glob.glob(os.path.join(json_output_dir, "question_*_*.json"))
    empty_files = set(glob.glob(os.path.join(json_output_dir, "question_*_empty_*.json")))
    normal_files = set(all_body_files) - empty_files

    def process_body_files(files, source_map, remove_map):
        for fpath in files:
            updated = False
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                bodies = data.get("question_bodies", [])
                if not bodies:
                    continue

                new_bodies_list = []
                for b in bodies:
                    u = b.get("question_url")
                    
                    # 削除対象（もう一方のカテゴリに移動したなど）ならスキップ
                    if u in remove_map:
                        updated = True
                        continue
                    
                    # 更新対象なら新しいデータに置き換え
                    if u in source_map:
                        new_bodies_list.append(source_map[u])
                        updated = True
                    else:
                        new_bodies_list.append(b)
                
                if updated:
                    data["question_bodies"] = new_bodies_list
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"[SUCCESS] Updated {os.path.basename(fpath)}")

            except Exception as e:
                print(f"[ERROR] Failed to update {fpath}: {e}")

    # Normal files: update from url_to_normal_body, remove if in url_to_empty_body
    process_body_files(normal_files, url_to_normal_body, url_to_empty_body)

    # Empty files: update from url_to_empty_body, remove if in url_to_normal_body
    process_body_files(empty_files, url_to_empty_body, url_to_normal_body)


# ==========
# 実行部分
# ==========

def load_qualification_rules() -> dict:
    """
    資格ごとのバリデーションルール定義を読み込む。
    ファイルがない場合はデフォルト設定を返す。
    """
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to load config file: {e}")
    
    # デフォルトフォールバック
    return {
        "default": {
            "allow_all_same": False,
            "allow_multiple_correct": False
        }
    }


def is_valid_answer_distribution(
    qualification_code: str,
    correct_count: int,
    incorrect_count: int,
    total_count: int,
    rules_config: dict,
) -> bool:
    """
    正誤判定の分布（正しい数、間違い数）が妥当かどうかを判定する。

    設計変更（ユーザー要望）:
      - correctChoiceText は answer_result_inferred_correct_choice_numbers と questionIntent で決定する。
      - 正解番号が複数ある場合、その件数が「正しい」または「間違い」の件数になる（資格ごとの固定分布は持たない）。
    そのため、ここでは「件数が合っているか（正しい+間違い==総数）」のみを検査し、分布形は制限しない。
    """
    if total_count == 0:
        return False
    if correct_count + incorrect_count != total_count:
        return False
    return True


def main() -> None:
    # ローカルPC専用の秘密情報（~/.config/exam_scraper/secure.env）を先に読み込む
    load_local_secure_env()
    apply_runtime_overrides_from_env()

    http_session = create_http_session()

    # ルール設定の読み込み
    qualification_rules = load_qualification_rules()
    print(f"[INFO] Loaded qualification rules from {CONFIG_FILE_PATH}")

    # コマンドライン引数でグループIDを指定可能にする
    # 使い方: python3 code.py 850003
    global LIST_FIRST_PAGE_URL
    if len(sys.argv) > 1:
        arg_group_id = sys.argv[1]
        # LIST_FIRST_PAGE_URL からベースURLを推定してグループIDを差し替え
        parsed = urlparse(LIST_FIRST_PAGE_URL)
        path_parts = parsed.path.rstrip("/").rsplit("/", 1)
        new_path = f"{path_parts[0]}/{arg_group_id}"
        LIST_FIRST_PAGE_URL = urlunparse(
            (parsed.scheme, parsed.netloc, new_path, "", "page=1", "")
        )
        print(f"[INFO] Overriding LIST_FIRST_PAGE_URL = {LIST_FIRST_PAGE_URL}")

    # LIST_FIRST_PAGE_URL から「71013」のようなグループIDを抽出
    list_group_id = extract_list_group_id_from_url(LIST_FIRST_PAGE_URL)
    output_list_group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID") or list_group_id
    if list_group_id:
        print(f"[INFO] list_group_id = {output_list_group_id}")

    # 出力ディレクトリ（JSON と画像のベース）
    output_dir = OUTPUT_DIR

    # 資格コード単位のベースディレクトリ
    qualification_dir = os.path.join(output_dir, QUALIFICATION_CODE)
    json_root_dir = os.path.join(qualification_dir, "questions_json")
    images_root_dir = os.path.join(qualification_dir, "question_images")

    # ベースディレクトリを作成
    for d in [qualification_dir, json_root_dir, images_root_dir]:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            print(f"[WARN] failed to create dir ({d}): {e}")

    # グループID単位のディレクトリ設定
    global IMAGE_OUTPUT_DIR
    if output_list_group_id:
        json_output_dir = os.path.join(json_root_dir, output_list_group_id, JSON_SUBDIR_NAME)
        IMAGE_OUTPUT_DIR = os.path.join(images_root_dir, output_list_group_id)
    else:
        json_output_dir = json_root_dir
        IMAGE_OUTPUT_DIR = images_root_dir

    # グループID単位ディレクトリの作成（必要ならサブディレクトリもまとめて作成）
    try:
        os.makedirs(json_output_dir, exist_ok=True)
    except Exception as e:
        print(f"[WARN] failed to create JSON output dir ({json_output_dir}): {e}")

    try:
        os.makedirs(IMAGE_OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        print(f"[WARN] failed to create image output dir ({IMAGE_OUTPUT_DIR}): {e}")

    if TARGET_LIST_PAGE_NUMBER is not None:
        # 特定ページのみ取得
        print(f"[INFO] TARGET_LIST_PAGE_NUMBER = {TARGET_LIST_PAGE_NUMBER}")
        # ページURL構築
        target_list_url = build_list_page_url(LIST_FIRST_PAGE_URL, TARGET_LIST_PAGE_NUMBER)
        print(f"[INFO] fetching list page: {target_list_url}")
        
        # そのページのHTML取得
        html_text = fetch_html_text(http_session, target_list_url)
        html_soup = BeautifulSoup(html_text, "html.parser")
        
        # URL抽出
        target_question_urls = extract_question_urls_from_list_page(html_soup, target_list_url)
        
        # 更新モードを強制ON
        global UPDATE_JSON_MODE
        UPDATE_JSON_MODE = True

    elif UPDATE_JSON_MODE and TARGET_URL:
        print(f"[INFO] UPDATE_JSON_MODE is ON. Target: {TARGET_URL}")
        target_question_urls = [TARGET_URL]
    elif LIST_FIRST_PAGE_URL:
        # 問題一覧からすべての問題URLを取得して処理
        target_question_urls = collect_question_urls_from_all_list_pages(
            http_session,
            LIST_FIRST_PAGE_URL,
        )
    else:
        # 単一問題ページだけを処理
        target_question_urls = [TARGET_URL]

    # 取得する問題数の上限を適用（テスト用）
    if MAX_QUESTIONS is not None:
        target_question_urls = target_question_urls[:MAX_QUESTIONS]
        print(
            f"[INFO] limit questions to first {len(target_question_urls)} URLs "
            f"(MAX_QUESTIONS={MAX_QUESTIONS})"
        )

    all_question_data: List[QuestionData] = []
    questions_for_json: List[dict] = []       # 問題単位データ
    label_pair_set: Set[Tuple[str, str]] = set()
    duplicate_label_pairs: Set[Tuple[str, str]] = set()
    # 追加: 問題文のみを重複なく蓄積するための辞書
    # key: question_body_text, value: メタ情報を含む dict
    unique_question_bodies: dict[str, dict] = {}
    # 追加: 選択肢が空の問題を分けて蓄積するための辞書
    unique_question_bodies_empty: dict[str, dict] = {}

    for index, question_url in enumerate(target_question_urls, start=1):
        print(f"[INFO] ({index}/{len(target_question_urls)}) fetch question: {question_url}")

        html_text = fetch_html_text(http_session, question_url)
        question_data = parse_question_page(html_text, question_url, http_session)

        # 出典ページ自体の識別子。canonical identity とは分けて保持する。
        source_question_id = (
            question_data.source_question_id
            or common_make_url_source_question_id(QUALIFICATION_CODE, question_data.question_url)
        )
        source_public_qid = make_public_question_id(source_question_id)
        question_source_site = common_source_site_from_url(question_data.question_url)
        public_qid = source_public_qid

        # 問題全体に紐づく導入・まとめを取得
        common_prefix_list, common_summary_list = extract_common_explanation_parts(
            question_data.explanations,
        )

        # explanation_common_prefix から正解番号を抽出（保存用）
        prefix_full_text = "\n".join(common_prefix_list)
        prefix_inferred_correct_numbers = find_correct_choice_numbers_in_text(prefix_full_text)
        if not prefix_inferred_correct_numbers and question_data.correct_choice_numbers:
            prefix_inferred_correct_numbers = list(question_data.correct_choice_numbers)
        prefix_inferred_correct_num = (
            prefix_inferred_correct_numbers[0]
            if prefix_inferred_correct_numbers
            else None
        )
        answer_result_data = question_data.answer_result_data
        answer_result_text = (
            (answer_result_data.answer_result_text or "")
            if answer_result_data is not None
            else ""
        )
        answer_result_inferred_correct_numbers = (
            list(answer_result_data.inferred_correct_choice_numbers)
            if answer_result_data is not None
            else []
        )

        # 問題画像の HTTP Storage URL を生成
        question_image_storage_urls = [
            make_storage_url(fn) for fn in question_data.question_image_filenames
        ]
        choice_image_storage_urls_by_choice = [
            [make_storage_url(fn) for fn in filenames]
            for filenames in question_data.choice_image_filenames_by_choice
        ]

        # 解説画像の HTTP Storage URL を、インデックスラベルごとにまとめる
        explanation_image_storage_urls = {}
        for exp in question_data.explanations:
            if exp.image_filenames:
                explanation_image_storage_urls[exp.explanation_index_label] = [
                    make_storage_url(fn) for fn in exp.image_filenames
                ]

        # examOccurrenceId 用の共通試験回識別子と、西暦年を抽出
        exam_year = extract_exam_year_value(question_data.exam_label)
        exam_occurrence_id = build_exam_occurrence_id(question_data.exam_label)
        section_code = None
        if QUALIFICATION_CODE == "1st-class-kenchikushi":
            section_code = extract_1st_class_kenchikushi_section_code(question_data.exam_label)
        canonical_question_key = make_canonical_question_key(
            exam_occurrence_id=exam_occurrence_id,
            exam_year=exam_year,
            question_label=question_data.question_label,
            section_code=section_code,
        )
        if canonical_question_key:
            public_qid = make_public_question_id(canonical_question_key)

        # カテゴリ判定
        category_val = None
        # 判定対象の文字列（exam_label を見るのが確実）
        cat_check_str = question_data.exam_label or ""
        
        if QUALIFICATION_CODE == "1st-class-kenchikushi":
            if any(k in cat_check_str for k in ["計画", "学科Ⅰ", "学科1"]):
                category_val = "学科Ⅰ（計画）"
            elif any(k in cat_check_str for k in ["環境・設備", "学科Ⅱ", "学科2"]):
                category_val = "学科Ⅱ（環境・設備）"
            elif any(k in cat_check_str for k in ["法規", "学科Ⅲ", "学科3"]):
                category_val = "学科Ⅲ（法規）"
            elif any(k in cat_check_str for k in ["構造", "学科Ⅳ", "学科4"]):
                category_val = "学科Ⅳ（構造）"
            elif any(k in cat_check_str for k in ["施工", "学科Ⅴ", "学科5"]):
                category_val = "学科Ⅴ（施工）"
        else:
            if any(k in cat_check_str for k in ["建築計画", "学科Ⅰ", "学科1"]):
                category_val = "学科Ⅰ（建築計画）"
            elif any(k in cat_check_str for k in ["建築法規", "学科Ⅱ", "学科2"]):
                category_val = "学科Ⅱ（建築法規）"
            elif any(k in cat_check_str for k in ["建築構造", "学科Ⅲ", "学科3"]):
                category_val = "学科Ⅲ（建築構造）"
            elif any(k in cat_check_str for k in ["建築施工", "学科Ⅳ", "学科4"]):
                category_val = "学科Ⅳ（建築施工）"

        # === 選択肢リストと正誤判定の準備 ===
        # 選択肢リストを決定（画像選択肢などで空の場合の補完を含む）
        final_choice_text_list = list(question_data.choice_text_list)
        final_choice_image_storage_urls_by_choice = list(choice_image_storage_urls_by_choice)

        # 選択肢テキストと選択肢画像の件数を index 対応でそろえる
        if len(final_choice_image_storage_urls_by_choice) > len(final_choice_text_list):
            final_choice_text_list.extend(
                [""] * (len(final_choice_image_storage_urls_by_choice) - len(final_choice_text_list))
            )
        
        # 選択肢が空の場合、解説から正解番号を探して選択肢数を補完する
        if not final_choice_text_list:
            if prefix_inferred_correct_numbers:
                # 5択と仮定（または正解番号まで）
                count = max(5, max(prefix_inferred_correct_numbers))
                final_choice_text_list = [""] * count
        
        # それでも空なら、最低1つは作る（従来動作維持）
        if not final_choice_text_list:
            final_choice_text_list = [""]

        if len(final_choice_image_storage_urls_by_choice) < len(final_choice_text_list):
            final_choice_image_storage_urls_by_choice.extend(
                [[] for _ in range(len(final_choice_text_list) - len(final_choice_image_storage_urls_by_choice))]
            )
        source_unique_keys = make_canonical_statement_keys(
            canonical_question_key or source_question_id,
            len(final_choice_text_list),
        )

        # 正誤判定リストの作成
        # 設計変更（ユーザー要望）:
        # correctChoiceText は answer_result_inferred_correct_choice_numbers（=正解番号）と questionIntent で決定する。
        question_intent = determine_question_intent(question_data.question_body_text)

        # 解説スニペットは explanationText の構築に使う（正誤判定は answer_numbers を優先）
        all_choice_snippets = []
        for i in range(1, len(final_choice_text_list) + 1):
            snippets = extract_choice_explanation_snippets_for_index(
                question_data.explanations,
                i,
            )
            all_choice_snippets.append(snippets)

        # answer_result_data が取れていればそれを最優先し、無ければ prefix 推定を使う
        answer_numbers_for_correctness = (
            list(answer_result_inferred_correct_numbers)
            if answer_result_inferred_correct_numbers
            else list(prefix_inferred_correct_numbers)
        )
        all_correct_choice_texts = infer_correct_choice_texts_from_answer_numbers(
            [None for _ in range(len(final_choice_text_list))],
            answer_numbers_for_correctness,
            question_intent,
        )

        # 互換のため保持（旧: スニペットから直接判定していた）
        raw_correct_choice_texts = []

        # questionType の判定
        # choiceTextList が空白のみの場合は group_choice とする（ユーザー要望）
        choices_are_blank = all(not (t or "").strip() for t in final_choice_text_list)
        if choices_are_blank:
            question_type_val = "group_choice"
        else:
            # 複数選択肢の正誤が判定できる場合は true_false、
            # それ以外は flash_card とする。
            count_choices_with_snippets = sum(1 for s in all_choice_snippets if len(s) > 0)
            if count_choices_with_snippets > 1:
                question_type_val = "true_false"
            else:
                question_type_val = "flash_card"

        # 解説から正解番号を推測して補完（None 混在、または全肢同一の場合に再構成）
        needs_answer_based_rebuild = (
            None in all_correct_choice_texts
            or (
                all_correct_choice_texts
                and len(set(all_correct_choice_texts)) == 1
            )
        )
        if needs_answer_based_rebuild:
            inferred_correct_numbers = list(prefix_inferred_correct_numbers)
            
            # ユーザー要望: 正解番号がテキストから判別できず、
            # かつ解説スニペットが1つの選択肢にしか存在しない場合、その選択肢を正解の対象とみなす
            if not inferred_correct_numbers:
                indices_with_snippets = [
                    idx for idx, s in enumerate(all_choice_snippets) if s
                ]
                if len(indices_with_snippets) == 1:
                    inferred_correct_numbers = [indices_with_snippets[0] + 1]

            all_correct_choice_texts = infer_correct_choice_texts_from_answer_numbers(
                all_correct_choice_texts,
                inferred_correct_numbers,
                question_intent,
            )

        # ===== 問題文のみの重複なしリストに追加 =====
        body_key = (question_data.question_body_text or "").strip()
        if body_key:
            # テキストも画像も空の選択肢しかない場合のみ empty 扱い
            is_empty_choices = all(
                (not choice_text.strip()) and (not choice_images)
                for choice_text, choice_images in zip(
                    final_choice_text_list,
                    final_choice_image_storage_urls_by_choice,
                )
            )
            
            # 正誤判定が不完全（Noneが含まれる）かどうか判定
            has_null_correctness = any(c is None for c in all_correct_choice_texts)

            # 正誤の分布チェック (ユーザー要望: 正しいが4つ間違いが1つ、またはその逆でない場合はemptyへ)
            correct_count = all_correct_choice_texts.count("正しい")
            incorrect_count = all_correct_choice_texts.count("間違い")
            total_count = len(all_correct_choice_texts)
            
            is_valid_distribution = False
            if not has_null_correctness:
                is_valid_distribution = is_valid_answer_distribution(
                    QUALIFICATION_CODE,
                    correct_count,
                    incorrect_count,
                    total_count,
                    qualification_rules,
                )

            # ユーザー要望: "correctChoiceText"が５つなくて、nullとなっている場合は、個別対応できるように
            # ここでは「選択肢が空」または「正誤判定に失敗している(Noneがある)」または「正誤分布が不正」な場合に empty 扱いとする
            if is_empty_choices or has_null_correctness or not is_valid_distribution:
                target_dict = unique_question_bodies_empty
            else:
                target_dict = unique_question_bodies

            # 同じ本文でも別設問として出題されるケースがあるため、URL が取れる場合は URL 単位で保持する。
            dedupe_key = question_data.question_url or body_key

            # まだ登録されていなければ追加
            if dedupe_key not in target_dict:
                # 問題文の意図を判定（出力用）
                intent_val = question_intent

                target_dict[dedupe_key] = {
                    "questionBodyText": body_key,
                    "examLabel": question_data.exam_label,
                    "questionLabel": question_data.question_label,
                    "questionType": question_type_val,
                    "choiceTextList": final_choice_text_list,
                    "originalQuestionChoiceImageUrls": final_choice_image_storage_urls_by_choice,
                    "category": category_val,
                    "examYear": exam_year,
                    "examOccurrenceId": exam_occurrence_id,
                    "list_group_id": output_list_group_id,
                    "question_url": question_data.question_url,
                    "public_question_id": public_qid,
                    "original_question_id": public_qid,
                    "source_question_id": source_question_id,
                    "source_public_question_id": source_public_qid,
                    "questionSourceSite": question_source_site,
                    "canonical_question_key": canonical_question_key,
                    "question_id_policy_key": "canonical-question-key:hmac:v1",
                    "question_id_policy_version": 1,
                    "question_id_source_key_description": (
                        "{qualification_code}:{exam_occurrence_id_or_year}:q{question_number}"
                    ),
                    "sourceUniqueKeys": source_unique_keys,
                    "questionImageStorageUrls": question_image_storage_urls,
                    "questionIntent": intent_val,
                    "correctChoiceText": all_correct_choice_texts,
                    "explanation_common_prefix": common_prefix_list,
                    "explanation_common_prefix_inferred_correct_choice": prefix_inferred_correct_num,
                    "explanation_common_summary": common_summary_list,
                    "explanation_choice_snippets": all_choice_snippets,
                    "explanation_choice_correctness": raw_correct_choice_texts,
                    "answer_result_text": answer_result_text,
                    "answer_result_inferred_correct_choice_numbers": answer_result_inferred_correct_numbers,
                }

        # ===== Firestore データモデルに合わせた JSON 作成 (設問単位) =====
        # 選択肢があれば、それぞれの選択肢を1つの「問題(Question)」として扱う
        loop_targets = []
        for i, txt in enumerate(final_choice_text_list, start=1):
            loop_targets.append((i, txt))

        for sub_index, choice_text in loop_targets:
            # 解説テキスト (この選択肢に関するもの)
            # 事前に抽出したスニペットを使用
            snippets = all_choice_snippets[sub_index - 1]

            # correctChoiceText の自動判定（補完済みのリストを使用）
            correct_choice_text_val = all_correct_choice_texts[sub_index - 1]
            
            # 解説テキストの構築 (Snippets + Summary)
            full_explanation_parts = []
            if snippets:
                full_explanation_parts.extend(snippets)
            
            # まとめがあれば解説の末尾に追加
            if common_summary_list:
                full_explanation_parts.append("")
                full_explanation_parts.extend(common_summary_list)
            
            explanation_text = "\n".join(full_explanation_parts).strip()

            # knowledgeText の構築 (Prefix + Snippets + Summary)
            # ユーザー要望: explanationText, prefix, summary をすべて統合
            knowledge_parts = []
            if common_prefix_list:
                knowledge_parts.extend(common_prefix_list)
            
            # explanationText (Snippets + Summary) の内容を追加
            if explanation_text:
                if knowledge_parts:
                    knowledge_parts.append("")
                knowledge_parts.append(explanation_text)
            
            knowledge_text = "\n".join(knowledge_parts).strip()

            # 解説画像 (この選択肢に関するもの: index_label="01" etc.)
            exp_img_key = f"{sub_index:02d}"
            exp_img_urls = explanation_image_storage_urls.get(exp_img_key, [])
            choice_img_urls = final_choice_image_storage_urls_by_choice[sub_index - 1]

            # questionText (設問文章のみ)
            if choice_text:
                final_question_text = choice_text
            else:
                final_question_text = question_data.question_body_text

            # questionId (Firestore のドキュメントIDとして利用する想定)
            # 現在の importKey に記載しているもの（public_qid）と設問番号の組み合わせ
            source_unique_key = (
                source_unique_keys[sub_index - 1]
                if sub_index - 1 < len(source_unique_keys)
                else f"{public_qid}_{sub_index}"
            )
            question_id_for_doc = source_unique_key

            # examSource の生成
            # 例: 二級建築士,2025年10月,問1,設問2
            source_year_str = exam_year or ""
            exam_source_val = f"{QUALIFICATION_NAME},{source_year_str},{question_data.question_label},設問{sub_index}"

            question_json = {
                # --- Firestore Fields ---
                "questionSetId": "",  # ユーザー要望: "現在は空白で良い"
                "questionText": final_question_text,
                "questionType": question_type_val,
                "questionImageUrls": question_image_storage_urls,
                
                # Firestore ドキュメント ID 用（インポート時に使用）
                "questionId": question_id_for_doc,
                
                "correctChoiceText": correct_choice_text_val,
                "correctChoiceImageUrls": [],
                "originalQuestionChoiceImageUrls": choice_img_urls,
                
                "incorrectChoice1Text": None,
                "incorrectChoice2Text": None,
                "incorrectChoice3Text": None,
                "incorrectChoice4Text": None,
                
                "knowledgeText": knowledge_text,
                "explanationText": None,  # knowledgeText に統合したため null を出力
                "explanationImageUrls": exp_img_urls,
                
                "hintText": None,
                "hintImageUrls": [],
                
                "examYear": exam_year,
                "examOccurrenceId": exam_occurrence_id,
                "examSource": exam_source_val,
                "questionTags": [],
                
                "isOfficial": True,
                "isDeleted": False,
                "importKey": None,
                
                # --- Internal / Debug Info (Preserved) ---
                "original_question_id": public_qid,
                "question_url": question_data.question_url,
                "sub_question_index": sub_index,
                "source_question_id": source_question_id,
                "source_public_question_id": source_public_qid,
                "questionSourceSite": question_source_site,
                "canonical_question_key": canonical_question_key,
                "sourceUniqueKey": source_unique_key,
                "sourceUniqueKeys": source_unique_keys,
                "choice_text": choice_text,
                "is_correct_choice": (
                    sub_index in question_data.correct_choice_numbers
                    if question_data.correct_choice_numbers
                    else None
                ),
                "list_group_id": output_list_group_id,
                "exam_label": question_data.exam_label,
                "question_label": question_data.question_label,
                "correct_choice_numbers": question_data.correct_choice_numbers,
                "public_question_id": public_qid,
                "question_body_text": question_data.question_body_text,
                "question_image_filenames": question_data.question_image_filenames,
                "question_image_storage_urls": question_image_storage_urls,
                "choice_image_filenames_by_choice": question_data.choice_image_filenames_by_choice,
                "original_question_choice_image_urls": final_choice_image_storage_urls_by_choice,
                "explanation_common_prefix": common_prefix_list,
                "explanation_common_summary": common_summary_list,
                "explanation_choice_snippets": snippets,
                "explanation_image_storage_urls": explanation_image_storage_urls,
            }
            questions_for_json.append(question_json)

        all_question_data.append(question_data)

        # exam_label + question_label の組み合わせで重複チェック（問題単位）
        label_pair = (question_data.exam_label, question_data.question_label)
        if label_pair in label_pair_set:
            duplicate_label_pairs.add(label_pair)
        else:
            label_pair_set.add(label_pair)

        # 問題ごとに少し休む（短め）
        slow_down(0.1, 0.2)

    # UPDATE_JSON_MODE の場合はここで既存ファイルを更新して終了
    if UPDATE_JSON_MODE:
        update_existing_files(
            json_output_dir,
            questions_for_json,
            unique_question_bodies,
            unique_question_bodies_empty,
        )
        print("\n[INFO] Update completed.")
        return

    # JSON として分割して保存（1ファイル = 25問）
    CHUNK_SIZE = 25
    
    # ユーザー要望により questions_summary は出力しない（ロジックは保持）
    if False and questions_for_json:
        total_questions = len(questions_for_json)
        for i, start_idx in enumerate(range(0, total_questions, CHUNK_SIZE), start=1):
            chunk = questions_for_json[start_idx : start_idx + CHUNK_SIZE]

            json_chunk_data = {
                "list_group_id": list_group_id,
                "questions": chunk,
            }

            # グループID単位ディレクトリ内に連番付きファイル名で保存
            base_filename = "questions_summary"
            json_chunk_filename = os.path.join(json_output_dir, f"{base_filename}_{i}.json")

            try:
                with open(json_chunk_filename, "w", encoding="utf-8") as fout:
                    json.dump(json_chunk_data, fout, ensure_ascii=False, indent=2)
                print(f"\n[INFO] JSON ({len(chunk)} questions) を {json_chunk_filename} に保存しました。")
            except Exception as save_error:  # noqa: PERF203
                print(f"[WARN] failed to save JSON ({json_chunk_filename}): {save_error}")

    save_targets = [
        (unique_question_bodies, "question"),
        (unique_question_bodies_empty, "question_empty"),
    ]

    for bodies_dict, base_filename_suffix in save_targets:
        try:
            saved_paths = save_question_body_chunks(
                json_output_dir,
                list_group_id,
                bodies_dict,
                base_filename_suffix=base_filename_suffix,
                chunk_size=CHUNK_SIZE,
            )
            for saved_path in saved_paths:
                print(f"\n[INFO] 問題文のみを {saved_path} に保存しました。")
        except Exception as save_error:  # noqa: PERF203
            print(f"[WARN] failed to save {base_filename_suffix} JSON: {save_error}")

    # ====== 最後に取得結果をコメントとして表示 ======
    print("\n==============================")
    print("取得結果まとめ（問題単位）")
    print("==============================")
    print(f"取得した問題数: {len(all_question_data)}")
    print("\nexam_label と question_label の組み合わせ一覧:")
    for exam_label, question_label in sorted(label_pair_set):
        print(f"- {exam_label} / {question_label}")

    if duplicate_label_pairs:
        print("\n[WARN] exam_label と question_label の組み合わせが重複しているもの:")
        for exam_label, question_label in sorted(duplicate_label_pairs):
            print(f"- {exam_label} / {question_label}")
    else:
        print("\nexam_label と question_label の組み合わせに重複はありません。")


if __name__ == "__main__":
    main()
