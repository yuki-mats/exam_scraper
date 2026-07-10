from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4.element import NavigableString, Tag


FIREBASE_STORAGE_BASE_URL = "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o"
QUESTION_ID_SECRET_KEY_ENV = "QUESTION_ID_SECRET_KEY"
LOCAL_SECURE_ENV_PATH = os.path.expanduser("~/.config/exam_scraper/secure.env")
PLACEHOLDER_IMAGE_PREFIXES = ("data:image/gif;base64,",)
SUBSCRIPT_DIGITS = {
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
}
SUPERSCRIPT_DIGITS = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
}


@dataclass
class ExplanationData:
    explanation_index_label: str
    explanation_body_text: str
    image_filenames: list[str]


@dataclass
class AnswerResultData:
    answer_result_text: str
    answer_result_html: str
    selected_choice_numbers: list[int]
    is_selected_choice_correct: bool | None
    inferred_correct_choice_numbers: list[int]


@dataclass
class QuestionData:
    question_url: str
    question_id: int | str
    exam_label: str
    question_label: str
    question_body_text: str
    choice_text_list: list[str]
    correct_choice_numbers: list[int]
    answer_result_data: AnswerResultData | None
    explanations: list[ExplanationData]
    question_image_filenames: list[str]
    choice_image_filenames_by_choice: list[list[str]]
    source_question_id: str | None = None


def load_local_secure_env(env_file_path: str = LOCAL_SECURE_ENV_PATH) -> None:
    if not os.path.exists(env_file_path):
        return

    try:
        with open(env_file_path, "r", encoding="utf-8") as fin:
            for raw_line in fin:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] failed to load local secure env ({env_file_path}): {exc}")


def slow_down(base_sec: float = 2.0, jitter_sec: float = 1.0) -> None:
    delay = base_sec + random.random() * jitter_sec
    time.sleep(delay)


def create_http_session() -> requests.Session:
    http_session = requests.Session()
    http_session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome Safari"
            ),
            "Accept-Language": "ja,en;q=0.8",
        }
    )
    return http_session


def fetch_html_text(http_session: requests.Session, target_url: str) -> str:
    for retry_index in range(3):
        try:
            slow_down(0.5, 0.5)
            response = http_session.get(target_url, timeout=20)
            response.raise_for_status()
            return response.text
        except Exception as fetch_error:  # noqa: PERF203
            print(f"[WARN] fetch failed ({target_url}): {fetch_error}")
            if retry_index == 2:
                raise
            slow_down(1.5, 1.5)
    raise RuntimeError("Unexpected error in fetch_html_text")


def make_public_question_id(question_id: int | str) -> str:
    secret_key = os.environ.get(QUESTION_ID_SECRET_KEY_ENV)
    if not secret_key:
        raise RuntimeError(f"{QUESTION_ID_SECRET_KEY_ENV} を環境変数に設定してください。")

    msg = str(question_id).encode("utf-8")
    digest = hmac.new(secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest[:16]


def normalize_identity_token(value: int | str | None) -> str:
    """Return a deterministic ASCII token for identity keys."""
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    normalized = re.sub(r"\s+", "-", normalized)
    ascii_token = re.sub(r"[^a-z0-9_-]+", "-", normalized).strip("-")
    if ascii_token:
        return ascii_token
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"h{digest}"


def source_site_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".kakomonn.com"):
        return "kakomonn"
    if host == "yaku-tik.com" or host.endswith(".yaku-tik.com"):
        return "yaku-tik"
    return normalize_identity_token(host.replace(".", "-"))


def make_url_source_question_id(qualification_code: str, question_url: str) -> str:
    parsed = urlparse(question_url)
    path_key = parsed.path.strip("/").replace("/", ":")
    return ":".join(
        part
        for part in [
            normalize_identity_token(qualification_code),
            source_site_from_url(question_url),
            path_key,
        ]
        if part
    )


def extract_primary_question_number(question_label: str | None) -> int | None:
    if not question_label:
        return None
    normalized = unicodedata.normalize("NFKC", str(question_label))
    match = re.search(r"(?:問|問題)\s*([0-9]{1,4})", normalized)
    if not match:
        return None
    return int(match.group(1))


def make_canonical_question_key(
    *,
    qualification_code: str,
    exam_occurrence_id: str | None = None,
    exam_year: int | str | None = None,
    question_number: int | str | None = None,
    question_label: str | None = None,
    section_code: str | None = None,
) -> str | None:
    """Build a site-independent exam question key.

    The key intentionally excludes the scrape site. It should identify the same
    exam question when kakomonn, yaku-tik, PDFs, or another source describe it.
    """
    occurrence = str(exam_occurrence_id or exam_year or "").strip()
    if not occurrence:
        return None

    number_value = question_number
    if number_value is None:
        number_value = extract_primary_question_number(question_label)
    if number_value is None or str(number_value).strip() == "":
        return None

    number_text = str(number_value).strip()
    if number_text.isdigit():
        question_token = f"q{int(number_text):03d}"
    else:
        question_token = f"q-{normalize_identity_token(number_text)}"

    parts = [
        normalize_identity_token(qualification_code),
        normalize_identity_token(occurrence),
    ]
    if section_code:
        parts.append(normalize_identity_token(section_code))
    parts.append(question_token)
    return ":".join(part for part in parts if part)


def make_canonical_statement_keys(
    canonical_question_key: str | None,
    statement_count: int,
) -> list[str]:
    if not canonical_question_key or statement_count <= 0:
        return []
    return [
        f"{canonical_question_key}:s{index:02d}"
        for index in range(1, statement_count + 1)
    ]


def firebase_storage_path_prefix(qualification_code: str) -> str:
    return f"question_images/official/{qualification_code}/"


def make_storage_url(filename: str, qualification_code: str) -> str:
    path = firebase_storage_path_prefix(qualification_code) + filename
    encoded_path = quote(path, safe="")
    return f"{FIREBASE_STORAGE_BASE_URL}/{encoded_path}?alt=media"


def guess_image_extension(image_url: str) -> str:
    path = urlparse(image_url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"}:
            return f".{ext}"
    return ".bin"


def is_placeholder_image_url(image_url: str) -> bool:
    return any(image_url.startswith(prefix) for prefix in PLACEHOLDER_IMAGE_PREFIXES)


def normalize_gassyunin_image_url(image_url: str) -> str:
    normalized_chars: list[str] = []
    for char in image_url:
        if char in SUBSCRIPT_DIGITS:
            normalized_chars.append("_")
            normalized_chars.append(SUBSCRIPT_DIGITS[char])
        elif char in SUPERSCRIPT_DIGITS:
            normalized_chars.append("_")
            normalized_chars.append(SUPERSCRIPT_DIGITS[char])
        else:
            normalized_chars.append(char)
    return "".join(normalized_chars)


def download_image_with_retry(
    http_session: requests.Session,
    image_url: str,
    max_retry: int = 3,
) -> bytes | None:
    for retry_index in range(max_retry):
        try:
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


def _download_and_save_single_image(
    http_session: requests.Session,
    image_url: str,
    base_dir: str,
    filename_prefix: str,
    index: int,
) -> tuple[int, str | None]:
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
    except Exception as save_error:  # noqa: BLE001
        print(f"[WARN] failed to save image ({file_path}): {save_error}")
        return index, None


def download_and_save_images(
    http_session: requests.Session,
    image_url_list: list[str],
    filename_prefix: str,
    *,
    base_dir: str,
) -> list[str]:
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] failed to create image output dir ({base_dir}): {exc}")

    saved_files_map: dict[int, str] = {}
    filtered_urls = [
        image_url
        for image_url in image_url_list
        if image_url and not is_placeholder_image_url(image_url)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(
                _download_and_save_single_image,
                http_session,
                image_url,
                base_dir,
                filename_prefix,
                index,
            )
            for index, image_url in enumerate(filtered_urls, start=1)
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, fname = future.result()
                if fname:
                    saved_files_map[idx] = fname
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Image download task failed: {exc}")

    return [saved_files_map[i] for i in sorted(saved_files_map.keys())]


def extract_image_urls_from_element(
    element: Tag | NavigableString | None,
    base_url: str,
) -> list[str]:
    image_url_list: list[str] = []
    if element is None or not hasattr(element, "find_all"):
        return image_url_list

    for img in element.find_all("img"):
        candidate_urls = [
            (img.get("data-src") or "").strip(),
            (img.get("data-lazy-src") or "").strip(),
            (img.get("src") or "").strip(),
        ]
        for candidate_url in candidate_urls:
            if not candidate_url or is_placeholder_image_url(candidate_url):
                continue
            abs_url = urljoin(base_url, normalize_gassyunin_image_url(candidate_url))
            if abs_url not in image_url_list:
                image_url_list.append(abs_url)
    return image_url_list


def normalize_question_body_text(text: str) -> str:
    if not text:
        return text

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"([A-Za-z0-9])\n([A-Za-z0-9])", r"\1\2", normalized)
    normalized = re.sub(r"([A-Za-z0-9])\n([ぁ-んァ-ン一-龥々〆〤])", r"\1\2", normalized)
    normalized = re.sub(r"([A-Za-z0-9])\n([、。,:;])", r"\1\2", normalized)
    normalized = re.sub(r"([、。])\n([A-Za-z0-9])", r"\1\2", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized.strip()


def normalize_inline_text(text: str) -> str:
    if not text:
        return text
    normalized = normalize_question_body_text(text)
    return re.sub(r"\s+", " ", normalized).strip()


def prepare_output_dirs(
    output_dir: str,
    qualification_code: str,
    output_list_group_id: str | None,
    json_subdir_name: str,
) -> tuple[str, str]:
    qualification_dir = os.path.join(output_dir, qualification_code)
    json_root_dir = os.path.join(qualification_dir, "questions_json")
    images_root_dir = os.path.join(qualification_dir, "question_images")

    for directory in [qualification_dir, json_root_dir, images_root_dir]:
        os.makedirs(directory, exist_ok=True)

    if output_list_group_id:
        json_output_dir = os.path.join(json_root_dir, output_list_group_id, json_subdir_name)
        image_output_dir = os.path.join(images_root_dir, output_list_group_id)
    else:
        json_output_dir = json_root_dir
        image_output_dir = images_root_dir

    os.makedirs(json_output_dir, exist_ok=True)
    os.makedirs(image_output_dir, exist_ok=True)
    return json_output_dir, image_output_dir


def _coerce_question_bodies(question_bodies: Iterable[dict] | dict[str, dict]) -> list[dict]:
    if isinstance(question_bodies, dict):
        return list(question_bodies.values())
    return list(question_bodies)


def save_question_body_chunks(
    json_output_dir: str,
    list_group_id: str | None,
    question_bodies: Iterable[dict] | dict[str, dict],
    *,
    base_filename_suffix: str = "question",
    chunk_size: int = 25,
) -> list[Path]:
    saved_paths: list[Path] = []
    question_bodies_list = _coerce_question_bodies(question_bodies)
    if not question_bodies_list:
        return saved_paths

    for chunk_index, start_idx in enumerate(range(0, len(question_bodies_list), chunk_size), start=1):
        chunk = question_bodies_list[start_idx : start_idx + chunk_size]
        if list_group_id:
            if base_filename_suffix == "question":
                filename = f"question_{list_group_id}_{chunk_index}.json"
            elif base_filename_suffix == "question_empty":
                filename = f"question_{list_group_id}_empty_{chunk_index}.json"
            else:
                filename = f"question_{list_group_id}_{base_filename_suffix}_{chunk_index}.json"
        else:
            if base_filename_suffix == "question":
                filename = f"question_{chunk_index}.json"
            elif base_filename_suffix == "question_empty":
                filename = f"question_empty_{chunk_index}.json"
            else:
                filename = f"{base_filename_suffix}_{chunk_index}.json"

        file_path = Path(json_output_dir) / filename
        with file_path.open("w", encoding="utf-8") as fout:
            json.dump(
                {
                    "list_group_id": list_group_id,
                    "question_bodies": chunk,
                },
                fout,
                ensure_ascii=False,
                indent=2,
            )
        saved_paths.append(file_path)

    return saved_paths
