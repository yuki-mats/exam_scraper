import json
import os
import sys
import glob  # Added
import random
import time
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

import hmac
import hashlib

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag


# 1問だけ試す場合はこちらを使う（LIST_FIRST_PAGE_URL が空のときに利用）
TARGET_URL = None
# 特定のページ番号（1, 2, ...）を指定して、そのページの25問を取得・更新する場合に設定
# ※ サイトの仕様で1ページ25問の場合、2を指定すると26〜50問目を取得して更新します。
TARGET_LIST_PAGE_NUMBER = None

# 既存のJSONファイルを更新するモード（TARGET_URL指定必須）
UPDATE_JSON_MODE = False

# ユーザーが設定する資格コード（UI側で管理）
# 公認心理士：kounin-shinrishi、二級建築士：2nd-class-kenchikushi、
QUALIFICATION_CODE = "kounin-shinrishi"
# 資格名（examSource等に使用）
QUALIFICATION_NAME = "公認心理師"

# 問題一覧の1ページ目のURLを指定する
LIST_FIRST_PAGE_URL = "https://shinrishi.kakomonn.com/list1/97009?page=1"

# JSON出力を配置するサブディレクトリ名（list_group_id 配下）
JSON_SUBDIR_NAME = "00_source"

# 取得する問題数の上限（None の場合はすべて取得）
# テスト実行時は例: MAX_QUESTIONS = 5 などに変更
MAX_QUESTIONS = None

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


def make_public_question_id(question_id: int) -> str:
    """
    元の question_id から公開用 ID（public_question_id）を生成する。
    QUESTION_ID_SECRET_KEY と組み合わせて HMAC-SHA256 を計算し、
    先頭16文字分の16進数文字列を public_question_id として用いる。
    """
    secret_key = os.environ.get(QUESTION_ID_SECRET_KEY_ENV)
    if not secret_key:
        raise RuntimeError(f"{QUESTION_ID_SECRET_KEY_ENV} を環境変数に設定してください。")
    msg = str(question_id).encode("utf-8")
    digest = hmac.new(secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest[:16]


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
    """
    リクエスト前後に少し待つためのヘルパ。
    base_sec + [0, jitter_sec) 秒だけ sleep する。
    """
    delay = base_sec + random.random() * jitter_sec
    time.sleep(delay)


@dataclass
class ExplanationData:
    explanation_index_label: str      # "01", "02", "03" など
    explanation_body_text: str        # 解説本文
    image_filenames: List[str]        # この解説に紐づく画像ファイル名一覧（ローカルのファイル名）


@dataclass
class QuestionData:
    question_url: str
    question_id: int

    exam_label: str                   # 例: "令和5年度（2023年） 午後"
    question_label: str               # 例: "問1 (一般問題 問1)"

    question_body_text: str           # 問題文
    choice_text_list: List[str]       # 設問文（選択肢） "選択肢1.～～" 形式
    correct_choice_numbers: List[int] # 正解の選択肢番号（1始まり）

    explanations: List[ExplanationData]

    question_image_filenames: List[str]  # 問題文の図などの画像ファイル名一覧（ローカルのファイル名）
    choice_image_filenames_by_choice: List[List[str]]  # 選択肢ごとの画像ファイル名一覧（choice index対応）


def create_http_session() -> requests.Session:
    http_session = requests.Session()
    http_session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome Safari"
        ),
        "Accept-Language": "ja,en;q=0.8",
    })
    return http_session


def fetch_html_text(http_session: requests.Session, target_url: str) -> str:
    for retry_index in range(3):
        try:
            # のんびりアクセス（少し短く）
            slow_down(0.5, 0.5)
            response = http_session.get(target_url, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as fetch_error:  # noqa: PERF203
            print(f"[WARN] fetch failed ({target_url}): {fetch_error}")
            if retry_index == 2:
                raise
            # リトライ前も少し待つ
            slow_down(1.5, 1.5)
    raise RuntimeError("Unexpected error in fetch_html_text")


# ==========
# 画像関連
# ==========

def guess_image_extension(image_url: str) -> str:
    """
    画像URLの拡張子を推測する。
    不明な場合は .bin にする。
    """
    path = urlparse(image_url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"}:
            return f".{ext}"
    return ".bin"


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
    """
    ローカルで保存した画像ファイル名から、Firebase Storage の HTTP URL を生成する。
    例:
      https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/
        question_images%2Fofficial%2Ffirst-class-electrician%2FqXXXXXXXXXXXXXXX_q_img01.jpg?alt=media
    """
    # question_images/official/first-class-electrician/qXXXXXXXXXXXXXXX_q_img01.jpg
    path = FIREBASE_STORAGE_PATH_PREFIX + filename

    # パス全体を URL エンコード（/ も %2F にする）
    encoded_path = quote(path, safe="")

    # token はアップロード時に付与されるのでここでは付けない
    return f"{FIREBASE_STORAGE_BASE_URL}/{encoded_path}?alt=media"


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
    """
    画像URLリストをダウンロードして保存し、保存した「ファイル名」のリストを返す。
    filename_prefix には "q{public_question_id}_q" や "q{public_question_id}_exp01" などを渡す。
    画像は output/<QUALIFICATION_CODE>/question_images/<list_group_id>/ 配下に保存する。
    """
    global IMAGE_OUTPUT_DIR

    # 保存先ディレクトリ（main で設定されていない場合はカレントディレクトリ）
    base_dir = IMAGE_OUTPUT_DIR or "."

    # 念のためここでもディレクトリ作成を試みる
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as e:
        print(f"[WARN] failed to create image output dir ({base_dir}): {e}")

    saved_files_map = {}

    # 並列ダウンロード（サーバー負荷を考慮して max_workers は控えめに）
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for index, image_url in enumerate(image_url_list, start=1):
            futures.append(
                executor.submit(
                    _download_and_save_single_image,
                    http_session,
                    image_url,
                    base_dir,
                    filename_prefix,
                    index
                )
            )
        
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, fname = future.result()
                if fname:
                    saved_files_map[idx] = fname
            except Exception as e:
                print(f"[WARN] Image download task failed: {e}")

    # インデックス順にファイル名をリスト化
    sorted_filenames = [saved_files_map[i] for i in sorted(saved_files_map.keys())]
    return sorted_filenames


def extract_image_urls_from_element(element, base_url: str) -> List[str]:
    """
    指定した要素以下の <img> から src を列挙し、絶対URLにして返す。
    """
    image_url_list: List[str] = []
    if element is None:
        return image_url_list

    for img in element.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        abs_url = urljoin(base_url, src)
        if abs_url not in image_url_list:
            image_url_list.append(abs_url)

    return image_url_list


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
    """
    Remove unintended line breaks inserted by HTML inline elements (e.g. <sub>).
    """
    import re

    if not text:
        return text

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # Join broken inline tokens such as "R\nB" or "10\nm".
    normalized = re.sub(r"([A-Za-z0-9])\n([A-Za-z0-9])", r"\1\2", normalized)
    # Join ASCII tokens split before Japanese characters or punctuation.
    normalized = re.sub(r"([A-Za-z0-9])\n([ぁ-んァ-ン一-龥々〆〤])", r"\1\2", normalized)
    normalized = re.sub(r"([A-Za-z0-9])\n([、。,:;])", r"\1\2", normalized)
    normalized = re.sub(r"([、。])\n([A-Za-z0-9])", r"\1\2", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized.strip()


def normalize_inline_text(text: str) -> str:
    """
    Normalize inline text by removing unintended line breaks and collapsing whitespace.
    """
    import re

    if not text:
        return text
    normalized = normalize_question_body_text(text)
    return re.sub(r"\s+", " ", normalized).strip()


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

    return QuestionData(
        question_url=question_url,
        question_id=question_id,
        exam_label=exam_label,
        question_label=question_label,
        question_body_text=question_body_text,
        choice_text_list=raw_choice_text_list,
        correct_choice_numbers=correct_choice_numbers,
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


def determine_correctness_from_snippets(snippets: List[str]) -> str | None:
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
    
    # 1. 最優先: 「正解です」「不正解です」の明確な判定
    # これらは試験解説で最も明確な正誤表現
    if "不正解です" in normalized_text:
        return "間違い"
    if "正解です" in normalized_text:
        return "正しい"
    
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


def find_correct_choice_number_in_text(text: str) -> int | None:
    """
    テキスト内から「正解は3」のようなパターンを探して、その番号(int)を返す。
    見つからなければ None。
    """
    import re
    if not text:
        return None
    
    # "正解は" または "正解　" の後に続く 1桁の数字(1-5)
    # 全角数字も含む
    # 強化: 「正解は、選択肢1です」や「正解：1」なども拾えるようにする
    pattern = re.compile(r"正解\s*(?:は|:|：|、|。)?\s*(?:選択肢)?\s*([1-5１-５])")
    match = pattern.search(text)
    if match:
        num_str = match.group(1)
        trans_table = str.maketrans("１２３４５", "12345")
        return int(num_str.translate(trans_table))
    return None


def determine_question_intent(question_text: str) -> str | None:
    """
    問題文から「正しいものを選ぶ」のか「誤っているものを選ぶ」のかを判定する。
    戻り値: "select_correct", "select_incorrect", or None
    """
    if not question_text:
        return None
    
    # 判定を容易にするため改行を除く
    text = question_text.replace("\n", "")
    
    # 「誤っているもの」「不適当なもの」系 (Falseを探す)
    # ユーザー要望により「不適」などのキーワードで判定（優先）
    incorrect_keywords = [
        "不適",      # 不適当、不適切
        "適当でない",
        "誤って",    # 誤っている
        "誤り",      # 誤りはどれか
        "間違い",    # 間違いはどれか
        "正しくない",
    ]
    if any(k in text for k in incorrect_keywords):
        return "select_incorrect"

    # 「正しいもの」「適当なもの」系 (Trueを探す)
    correct_keywords = [
        "適当",      # 最も適当なもの
        "正しい",
        "適切",
    ]
    if any(k in text for k in correct_keywords):
        return "select_correct"
        
    return None


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
    正誤判定の分布（正しい数、間違い数）が、その資格試験の形式として妥当かどうかを判定する。
    妥当でない場合（全て正しい、全て間違い、あるいは択一式なのに複数正解など）は False を返し、
    手動確認用（empty）に振り分ける。
    """
    if total_count == 0:
        return False

    # 設定からルールを取得（なければデフォルト）
    rule = rules_config.get(qualification_code, rules_config.get("default", {}))
    
    # ルール値の取得（デフォルトは厳しめに設定）
    allow_all_same = rule.get("allow_all_same", False)
    allow_multiple_correct = rule.get("allow_multiple_correct", False)

    # 1. 全て「正しい」または全て「間違い」のチェック
    # 公認心理師でもここを False に設定することで empty 扱いにできる
    if not allow_all_same:
        if correct_count == total_count or incorrect_count == total_count:
            return False

    # 2. 複数正解の許容チェック
    if allow_multiple_correct:
        # ここまで来れば（全一致でなければ）OK
        return True

    # 3. デフォルト（択一）: (正1, 誤N-1) または (正N-1, 誤1) のみ許可
    is_one_vs_rest = (correct_count == 1 and incorrect_count == total_count - 1) or \
                     (correct_count == total_count - 1 and incorrect_count == 1)
    return is_one_vs_rest


def main() -> None:
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
    if list_group_id:
        print(f"[INFO] list_group_id = {list_group_id}")

    # 出力ディレクトリ（JSON と画像のベース）
    output_dir = "/Users/yuki/development/exam_scraper/output"

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
    if list_group_id:
        json_output_dir = os.path.join(json_root_dir, list_group_id, JSON_SUBDIR_NAME)
        IMAGE_OUTPUT_DIR = os.path.join(images_root_dir, list_group_id)
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

        # 公開用 question ID（ハッシュ済み）を生成
        public_qid = make_public_question_id(question_data.question_id)

        # 問題全体に紐づく導入・まとめを取得
        common_prefix_list, common_summary_list = extract_common_explanation_parts(
            question_data.explanations,
        )

        # explanation_common_prefix から正解番号を抽出（保存用）
        prefix_full_text = "\n".join(common_prefix_list)
        prefix_inferred_correct_num = find_correct_choice_number_in_text(prefix_full_text)

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

        # examYear の抽出（簡易的）
        import re
        exam_year = None
        m_year = re.search(r"(\d{4})年", question_data.exam_label)
        if m_year:
            exam_year = int(m_year.group(1))

        # カテゴリ判定
        category_val = None
        # 判定対象の文字列（exam_label を見るのが確実）
        cat_check_str = question_data.exam_label or ""
        
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
            inferred_num = prefix_inferred_correct_num
            if inferred_num is not None:
                # 5択と仮定（または正解番号まで）
                count = max(5, inferred_num)
                final_choice_text_list = [""] * count
        
        # それでも空なら、最低1つは作る（従来動作維持）
        if not final_choice_text_list:
            final_choice_text_list = [""]

        if len(final_choice_image_storage_urls_by_choice) < len(final_choice_text_list):
            final_choice_image_storage_urls_by_choice.extend(
                [[] for _ in range(len(final_choice_text_list) - len(final_choice_image_storage_urls_by_choice))]
            )

        # 正誤判定リストの作成
        all_choice_snippets = []
        all_correct_choice_texts = []
        
        for i in range(1, len(final_choice_text_list) + 1):
            snippets = extract_choice_explanation_snippets_for_index(
                question_data.explanations,
                i,
            )
            all_choice_snippets.append(snippets)
            all_correct_choice_texts.append(determine_correctness_from_snippets(snippets))

        # 解説スニペットから直接得られた判定結果を保持（推論による補完前）
        raw_correct_choice_texts = list(all_correct_choice_texts)

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

        # 解説から正解番号を推測して補完（None の箇所を埋める）
        if None in all_correct_choice_texts:
            inferred_correct_num = prefix_inferred_correct_num
            
            # ユーザー要望: 正解番号がテキストから判別できず、
            # かつ解説スニペットが1つの選択肢にしか存在しない場合、その選択肢を正解の対象とみなす
            if inferred_correct_num is None:
                indices_with_snippets = [
                    idx for idx, s in enumerate(all_choice_snippets) if s
                ]
                if len(indices_with_snippets) == 1:
                    inferred_correct_num = indices_with_snippets[0] + 1
            
            # 問題文から「正しいものを選ぶ」のか「誤りを選ぶ」のか判定
            question_intent = determine_question_intent(question_data.question_body_text)

            if inferred_correct_num is not None and question_intent is not None:
                if 1 <= inferred_correct_num <= len(all_correct_choice_texts):
                    correct_idx = inferred_correct_num - 1
                    
                    if question_intent == "select_correct":
                        # 正解の選択肢 = 正しい内容 (他は間違い)
                        if all_correct_choice_texts[correct_idx] is None:
                            all_correct_choice_texts[correct_idx] = "正しい"
                        
                        for idx in range(len(all_correct_choice_texts)):
                            if idx != correct_idx and all_correct_choice_texts[idx] is None:
                                all_correct_choice_texts[idx] = "間違い"

                    elif question_intent == "select_incorrect":
                        # 正解の選択肢 = 間違いの内容 (他は正しい)
                        if all_correct_choice_texts[correct_idx] is None:
                            all_correct_choice_texts[correct_idx] = "間違い"
                        
                        for idx in range(len(all_correct_choice_texts)):
                            if idx != correct_idx and all_correct_choice_texts[idx] is None:
                                all_correct_choice_texts[idx] = "正しい"

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

            # まだ登録されていなければ追加
            if body_key not in target_dict:
                # 問題文の意図を判定（出力用）
                intent_val = determine_question_intent(question_data.question_body_text)

                target_dict[body_key] = {
                    "questionBodyText": body_key,
                    "examLabel": question_data.exam_label,
                    "questionLabel": question_data.question_label,
                    "questionType": question_type_val,
                    "choiceTextList": final_choice_text_list,
                    "originalQuestionChoiceImageUrls": final_choice_image_storage_urls_by_choice,
                    "category": category_val,
                    "examYear": exam_year,
                    "list_group_id": list_group_id,
                    "question_url": question_data.question_url,
                    "public_question_id": public_qid,
                    "questionImageStorageUrls": question_image_storage_urls,
                    "questionIntent": intent_val,
                    "correctChoiceText": all_correct_choice_texts,
                    "explanation_common_prefix": common_prefix_list,
                    "explanation_common_prefix_inferred_correct_choice": prefix_inferred_correct_num,
                    "explanation_common_summary": common_summary_list,
                    "explanation_choice_snippets": all_choice_snippets,
                    "explanation_choice_correctness": raw_correct_choice_texts,
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
            question_id_for_doc = f"{public_qid}_{sub_index}"

            # examSource の生成
            # 例: 二級建築士,2024年,問1,設問2
            source_year_str = f"{exam_year}年" if exam_year else ""
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
                "examSource": exam_source_val,
                "questionTags": [],
                
                "isOfficial": True,
                "isDeleted": False,
                "importKey": None,
                
                # --- Internal / Debug Info (Preserved) ---
                "original_question_id": question_data.question_id,
                "question_url": question_data.question_url,
                "sub_question_index": sub_index,
                "choice_text": choice_text,
                "is_correct_choice": (
                    sub_index in question_data.correct_choice_numbers
                    if question_data.correct_choice_numbers
                    else None
                ),
                "list_group_id": list_group_id,
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

    # 追加: 問題文のみの JSON を別ファイルとして保存（25問ずつ分割）
    # 通常の問題と、選択肢が空の問題を分けて保存
    save_targets = [
        (unique_question_bodies, "question"),
        (unique_question_bodies_empty, "question_empty"),
    ]

    for bodies_dict, base_filename_suffix in save_targets:
        if not bodies_dict:
            continue

        question_bodies_list = list(bodies_dict.values())
        total_bodies = len(question_bodies_list)
        
        for i, start_idx in enumerate(range(0, total_bodies, CHUNK_SIZE), start=1):
            chunk = question_bodies_list[start_idx : start_idx + CHUNK_SIZE]
            
            # list_group_id をファイル名の先頭に付与
            if list_group_id:
                if base_filename_suffix == "question":
                    file_path = os.path.join(json_output_dir, f"question_{list_group_id}_{i}.json")
                else:
                    file_path = os.path.join(json_output_dir, f"question_{list_group_id}_empty_{i}.json")

            else:
                if base_filename_suffix == "question":
                    file_path = os.path.join(json_output_dir, f"question_{i}.json")
                else:
                    file_path = os.path.join(json_output_dir, f"question_empty_{i}.json")
            
            try:
                # JSON形式で保存
                with open(file_path, "w", encoding="utf-8") as fout:
                    json.dump(
                        {
                            "list_group_id": list_group_id,
                            "question_bodies": chunk,
                        },
                        fout,
                        ensure_ascii=False,
                        indent=2,
                    )
                print(
                    f"\n[INFO] 問題文のみ（{len(chunk)} 件）を "
                    f"{file_path} に保存しました。"
                )

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
