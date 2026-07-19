from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from scripts.scrape.common import (
    create_http_session,
    download_image_with_retry,
    guess_image_extension,
    load_local_secure_env,
    make_public_question_id,
    make_storage_url,
    normalize_inline_text,
    normalize_question_body_text,
    prepare_output_dirs,
)


UDEMY_BROWSER_EXPORT_PATH_ENV = "UDEMY_BROWSER_EXPORT_PATH"
SOURCE_SITE = "tokyo-gas-dx-udemy-com"
INCORRECT_PATTERNS = (
    r"誤っている",
    r"誤り",
    r"正しくない",
    r"不適切",
    r"不適当",
    r"適切でない",
    r"適当でない",
    r"含まれない",
    r"該当しない",
    r"対象とならない",
)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def load_browser_export(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Udemy browser exportはobjectである必要があります")
    if int(data.get("schema_version") or 0) != 1:
        raise ValueError("Udemy browser exportのschema_versionが不正です")
    quizzes = data.get("quizzes")
    if not isinstance(quizzes, list) or not quizzes:
        raise ValueError("Udemy browser exportにquizzesがありません")
    return data


def validate_browser_export(data: dict[str, Any]) -> list[dict[str, Any]]:
    expected_count = int(data.get("expected_count") or 0)
    course_slug = str(data.get("course_slug") or "").strip()
    course_url = str(data.get("course_url") or "").strip()
    if not course_slug or not course_url:
        raise ValueError("Udemy course情報が不足しています")
    records: list[dict[str, Any]] = []
    quiz_ids: set[str] = set()
    identity_keys: set[str] = set()
    for quiz in data["quizzes"]:
        if not isinstance(quiz, dict):
            raise ValueError("Udemy quizがobjectではありません")
        quiz_id = str(quiz.get("quiz_id") or "").strip()
        if not quiz_id.isdigit() or quiz_id in quiz_ids:
            raise ValueError(f"Udemy quiz IDが不正又は重複しています: {quiz_id}")
        quiz_ids.add(quiz_id)
        quiz_records = quiz.get("records")
        quiz_expected_count = int(quiz.get("expected_count") or 0)
        if not isinstance(quiz_records, list) or len(quiz_records) != quiz_expected_count:
            raise ValueError(
                f"Udemy quiz問題数が一致しません: quiz={quiz_id} "
                f"actual={len(quiz_records) if isinstance(quiz_records, list) else 0} "
                f"expected={quiz_expected_count}"
            )
        numbers: set[int] = set()
        for record in quiz_records:
            if not isinstance(record, dict):
                raise ValueError(f"Udemy問題がobjectではありません: quiz={quiz_id}")
            number = int(record.get("question_number") or 0)
            if number <= 0 or number in numbers:
                raise ValueError(
                    f"Udemy問題番号が不正又は重複しています: quiz={quiz_id} number={number}"
                )
            numbers.add(number)
            if str(record.get("quiz_id") or "") != quiz_id:
                raise ValueError(f"Udemy問題とquiz IDが一致しません: quiz={quiz_id} number={number}")
            identity = f"{quiz_id}:{number:03d}"
            if identity in identity_keys:
                raise ValueError(f"Udemy問題identityが重複しています: {identity}")
            identity_keys.add(identity)
            records.append(record)
    if len(records) != expected_count:
        raise ValueError(
            f"Udemy全問題数が一致しません: actual={len(records)} expected={expected_count}"
        )
    return records


def determine_question_intent(question_text: str) -> str:
    return (
        "select_incorrect"
        if any(re.search(pattern, question_text or "") for pattern in INCORRECT_PATTERNS)
        else "select_correct"
    )


def choice_truth_labels(
    *, choice_count: int, correct_choice_numbers: Iterable[int], question_intent: str
) -> list[str]:
    correct = {int(value) for value in correct_choice_numbers}
    if question_intent == "select_incorrect":
        return ["間違い" if index in correct else "正しい" for index in range(1, choice_count + 1)]
    return ["正しい" if index in correct else "間違い" for index in range(1, choice_count + 1)]


def build_answer_result_text(correct_choice_numbers: Iterable[int]) -> str:
    numbers = sorted({int(value) for value in correct_choice_numbers})
    return "正解は " + "、".join(str(value) for value in numbers) + " です。"


def source_identity(
    *, qualification_code: str, course_slug: str, quiz_id: str, question_number: int
) -> str:
    return (
        f"{qualification_code}:{SOURCE_SITE}:course:{course_slug}:"
        f"quiz:{quiz_id}:question:{question_number:03d}"
    )


def stable_question_url(*, course_slug: str, quiz_id: str, question_number: int) -> str:
    return (
        f"https://tokyo-gas-dx.udemy.com/course/{course_slug}/learn/quiz/{quiz_id}/test"
        f"#question-{question_number:03d}"
    )


def _image_filename(*, quiz_id: str, question_number: int, kind: str, image_url: str) -> str:
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:12]
    return (
        f"udemy_{quiz_id}_{question_number:03d}_{kind}_{digest}"
        f"{guess_image_extension(image_url)}"
    )


def _download_images(
    *,
    image_urls: Iterable[str],
    http_session: Any,
    image_output_dir: Path,
    qualification_code: str,
    quiz_id: str,
    question_number: int,
    kind: str,
) -> list[str]:
    storage_urls: list[str] = []
    for image_url in _dedupe(image_urls):
        filename = _image_filename(
            quiz_id=quiz_id,
            question_number=question_number,
            kind=kind,
            image_url=image_url,
        )
        target = image_output_dir / filename
        if not target.is_file():
            image_bytes = download_image_with_retry(http_session, image_url)
            if not image_bytes:
                raise ValueError(f"Udemy画像を取得できません: {image_url}")
            target.write_bytes(image_bytes)
        if target.stat().st_size <= 0:
            raise ValueError(f"Udemy画像が空です: {target}")
        storage_urls.append(make_storage_url(filename, qualification_code))
    return storage_urls


def build_source_record(
    raw: dict[str, Any],
    *,
    qualification_code: str,
    qualification_name: str,
    output_list_group_id: str,
    source_list_group_id: str,
    http_session: Any,
    image_output_dir: Path,
) -> dict[str, Any]:
    quiz_id = str(raw.get("quiz_id") or "").strip()
    question_number = int(raw.get("question_number") or 0)
    question_text = normalize_question_body_text(str(raw.get("question_text") or ""))
    explanation_text = normalize_question_body_text(str(raw.get("explanation_text") or ""))
    choices_raw = raw.get("choices")
    if not quiz_id.isdigit() or question_number <= 0:
        raise ValueError("Udemy quiz ID又は問題番号が不正です")
    if not question_text or not explanation_text:
        raise ValueError("Udemy問題文又は解説が空です")
    if not isinstance(choices_raw, list) or len(choices_raw) < 2:
        raise ValueError("Udemy選択肢が不足しています")
    choices = [normalize_question_body_text(str(choice.get("text") or "")) for choice in choices_raw]
    correct_numbers = sorted({int(value) for value in raw.get("correct_choice_numbers") or []})
    if not correct_numbers or any(value < 1 or value > len(choices) for value in correct_numbers):
        raise ValueError("Udemy正答番号が不正です")
    question_intent = determine_question_intent(question_text)
    source_question_id = source_identity(
        qualification_code=qualification_code,
        course_slug=source_list_group_id,
        quiz_id=quiz_id,
        question_number=question_number,
    )
    public_question_id = make_public_question_id(source_question_id)

    question_source_images = _dedupe(raw.get("question_image_urls") or [])
    explanation_source_images = _dedupe(raw.get("explanation_image_urls") or [])
    choice_source_images = [
        _dedupe(choice.get("image_urls") or []) for choice in choices_raw
    ]
    question_storage_images = _download_images(
        image_urls=question_source_images,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        quiz_id=quiz_id,
        question_number=question_number,
        kind="question",
    )
    choice_storage_images = [
        _download_images(
            image_urls=urls,
            http_session=http_session,
            image_output_dir=image_output_dir,
            qualification_code=qualification_code,
            quiz_id=quiz_id,
            question_number=question_number,
            kind=f"choice{index:02d}",
        )
        for index, urls in enumerate(choice_source_images, start=1)
    ]
    explanation_storage_images = _download_images(
        image_urls=explanation_source_images,
        http_session=http_session,
        image_output_dir=image_output_dir,
        qualification_code=qualification_code,
        quiz_id=quiz_id,
        question_number=question_number,
        kind="explanation",
    )
    reference_urls = [
        {
            "title": normalize_inline_text(str(item.get("title") or "")),
            "url": str(item.get("url") or "").strip(),
        }
        for item in raw.get("reference_urls") or []
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]

    record: dict[str, Any] = {
        "questionBodyText": question_text,
        "examLabel": f"{qualification_name} / Udemy Business",
        "questionLabel": f"{raw.get('quiz_title') or quiz_id} 問題{question_number}",
        "questionType": "true_false",
        "choiceTextList": choices,
        "originalQuestionChoiceImageUrls": choice_storage_images,
        "choiceImageSourceUrlsByChoice": choice_source_images,
        "category": str(raw.get("domain") or "").strip(),
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_url": stable_question_url(
            course_slug=source_list_group_id,
            quiz_id=quiz_id,
            question_number=question_number,
        ),
        "public_question_id": public_question_id,
        "original_question_id": public_question_id,
        "source_question_id": source_question_id,
        "source_public_question_id": public_question_id,
        "questionSourceSite": SOURCE_SITE,
        "question_id_policy_key": "source-question-id:hmac:v1",
        "question_id_policy_version": 1,
        "question_id_source_key_description": (
            "{qualification_code}:tokyo-gas-dx-udemy-com:course:{course_slug}:"
            "quiz:{quiz_id}:question:{question_number}"
        ),
        "sourceUniqueKeys": [
            f"{source_question_id}:s{index:02d}" for index in range(1, len(choices) + 1)
        ],
        "sourceQuestionInputType": str(raw.get("selection_type") or "").strip(),
        "questionImageSourceUrls": question_source_images,
        "questionImageStorageUrls": question_storage_images,
        "questionIntent": question_intent,
        "correctChoiceText": choice_truth_labels(
            choice_count=len(choices),
            correct_choice_numbers=correct_numbers,
            question_intent=question_intent,
        ),
        "explanation_common_prefix": [explanation_text],
        "explanation_common_summary": [],
        "explanation_choice_snippets": [[] for _ in choices],
        "answer_result_text": build_answer_result_text(correct_numbers),
        "answer_result_inferred_correct_choice_numbers": correct_numbers,
        "explanationImageSourceUrls": explanation_source_images,
        "explanationImageStorageUrls": explanation_storage_images,
        "referenceUrls": reference_urls,
    }
    if len(correct_numbers) == 1:
        record["explanation_common_prefix_inferred_correct_choice"] = correct_numbers[0]
    return record


def source_filename(quiz_id: str, question_number: int) -> str:
    return f"question_udemy-{quiz_id}-{question_number:03d}.json"


def _identity_from_record(record: dict[str, Any]) -> tuple[str, int] | None:
    match = re.search(
        r":quiz:(?P<quiz_id>[0-9]+):question:(?P<number>[0-9]{3})$",
        str(record.get("source_question_id") or ""),
    )
    if not match:
        return None
    return match.group("quiz_id"), int(match.group("number"))


def load_existing_records(source_dir: Path) -> dict[tuple[str, int], tuple[Path, dict[str, Any]]]:
    records: dict[tuple[str, int], tuple[Path, dict[str, Any]]] = {}
    for path in sorted(source_dir.glob("question_udemy-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        bodies = data.get("question_bodies") if isinstance(data, dict) else None
        if not isinstance(bodies, list) or len(bodies) != 1 or not isinstance(bodies[0], dict):
            raise ValueError(f"既存Udemy 00_sourceの構造が不正です: {path}")
        identity = _identity_from_record(bodies[0])
        if identity is None or identity in records:
            raise ValueError(f"既存Udemy 00_sourceのIDが不正又は重複しています: {path}")
        records[identity] = (path, bodies[0])
    return records


def save_source_record(
    *,
    source_dir: Path,
    output_list_group_id: str,
    source_list_group_id: str,
    record: dict[str, Any],
    replace_existing: bool,
) -> Path:
    identity = _identity_from_record(record)
    if identity is None:
        raise ValueError("Udemy recordからidentityを取得できません")
    quiz_id, question_number = identity
    path = source_dir / source_filename(quiz_id, question_number)
    payload = {
        "list_group_id": output_list_group_id,
        "source_list_group_id": source_list_group_id,
        "question_bodies": [record],
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not replace_existing:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
        return path
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
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
            temporary_path = Path(output.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "questionBodyText",
        "choiceTextList",
        "answer_result_text",
        "answer_result_inferred_correct_choice_numbers",
        "explanation_common_prefix",
        "source_question_id",
        "question_url",
    ):
        if record.get(key) in (None, "", []):
            errors.append(f"{key} is empty")
    choices = record.get("choiceTextList")
    correct_numbers = record.get("answer_result_inferred_correct_choice_numbers")
    if not isinstance(choices, list) or len(choices) < 2:
        errors.append("choiceTextList must contain at least 2 choices")
    if isinstance(choices, list) and isinstance(correct_numbers, list):
        for number in correct_numbers:
            if not isinstance(number, int) or not 1 <= number <= len(choices):
                errors.append(f"correct choice number out of range: {number}")
    source_choice_images = record.get("choiceImageSourceUrlsByChoice")
    storage_choice_images = record.get("originalQuestionChoiceImageUrls")
    if isinstance(source_choice_images, list) and isinstance(storage_choice_images, list):
        if [len(value) for value in source_choice_images] != [
            len(value) for value in storage_choice_images
        ]:
            errors.append("choice image source/storage counts differ")
    if len(record.get("questionImageSourceUrls") or []) != len(
        record.get("questionImageStorageUrls") or []
    ):
        errors.append("question image source/storage counts differ")
    if len(record.get("explanationImageSourceUrls") or []) != len(
        record.get("explanationImageStorageUrls") or []
    ):
        errors.append("explanation image source/storage counts differ")
    if "examYear" in record:
        errors.append("independent source must not contain examYear")
    return errors


def _ids_digest(values: Iterable[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(values)) + "\n").encode("utf-8")).hexdigest()


def write_report(
    *,
    report_path: Path,
    browser_export: dict[str, Any],
    expected_keys: set[str],
    persisted_records: dict[tuple[str, int], tuple[Path, dict[str, Any]]],
    new_count: int,
    updated_count: int,
    verified_count: int,
    errors: list[dict[str, str]],
    image_output_dir: Path,
) -> dict[str, Any]:
    persisted_keys = {
        f"{quiz_id}:{question_number:03d}" for quiz_id, question_number in persisted_records
    }
    records = [record for _, record in persisted_records.values()]
    image_paths = sorted(path for path in image_output_dir.glob("*") if path.is_file())
    quiz_counts = Counter(key.split(":", 1)[0] for key in persisted_keys)
    category_counts = Counter(str(record.get("category") or "") for record in records)
    report = {
        "status": "complete"
        if not errors and persisted_keys == expected_keys
        else "incomplete",
        "completedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceSite": SOURCE_SITE,
        "sourceListGroupId": str(browser_export.get("course_slug") or ""),
        "courseTitle": str(browser_export.get("course_title") or ""),
        "courseUrl": str(browser_export.get("course_url") or ""),
        "expectedCount": len(expected_keys),
        "persistedCount": len(persisted_keys),
        "newlySavedCount": new_count,
        "updatedExistingCount": updated_count,
        "verifiedExistingCount": verified_count,
        "missingIds": sorted(expected_keys - persisted_keys),
        "unexpectedIds": sorted(persisted_keys - expected_keys),
        "duplicateSourceQuestionIdCount": len(records)
        - len({str(record.get("source_question_id") or "") for record in records}),
        "duplicateQuestionUrlCount": len(records)
        - len({str(record.get("question_url") or "") for record in records}),
        "expectedIdsSha256": _ids_digest(expected_keys),
        "persistedIdsSha256": _ids_digest(persisted_keys),
        "quizCounts": dict(sorted(quiz_counts.items())),
        "categoryCounts": dict(sorted(category_counts.items())),
        "domainMissingCount": sum(1 for record in records if not record.get("category")),
        "questionImageReferenceCount": sum(
            len(record.get("questionImageSourceUrls") or []) for record in records
        ),
        "choiceImageReferenceCount": sum(
            sum(len(value) for value in record.get("choiceImageSourceUrlsByChoice") or [])
            for record in records
        ),
        "explanationImageReferenceCount": sum(
            len(record.get("explanationImageSourceUrls") or []) for record in records
        ),
        "imageFileCount": len(image_paths),
        "sourceFileSha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path, _ in sorted(persisted_records.values(), key=lambda value: str(value[0]))
        },
        "imageFileSha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in image_paths
        },
        "errors": errors,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Udemy Businessのbrowser exportを1問1ファイルの00_sourceへ保存する。"
    )
    parser.add_argument("--qualification-code", default=os.environ.get("SCRAPER_QUALIFICATION_CODE", ""))
    parser.add_argument("--qualification-name", default=os.environ.get("SCRAPER_QUALIFICATION_NAME", ""))
    parser.add_argument("--list-url", default=os.environ.get("SCRAPER_LIST_FIRST_PAGE_URL", ""))
    parser.add_argument("--output-list-group-id", default=os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID", ""))
    parser.add_argument("--output-dir", default=os.environ.get("SCRAPER_OUTPUT_DIR", str(Path.cwd() / "output")))
    parser.add_argument("--browser-export", default=os.environ.get(UDEMY_BROWSER_EXPORT_PATH_ENV, ""))
    parser.add_argument(
        "--max-questions",
        type=int,
        default=int(os.environ["SCRAPER_MAX_QUESTIONS"])
        if os.environ.get("SCRAPER_MAX_QUESTIONS")
        else None,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_local_secure_env()
    args = parse_args(argv)
    for name, value in (
        ("qualification-code", args.qualification_code),
        ("qualification-name", args.qualification_name),
        ("list-url", args.list_url),
        ("output-list-group-id", args.output_list_group_id),
        ("browser-export", args.browser_export),
    ):
        if not str(value or "").strip():
            raise ValueError(f"--{name}は必須です")

    browser_export = load_browser_export(Path(args.browser_export).expanduser())
    source_list_group_id = str(browser_export.get("course_slug") or "").strip()
    list_course_slug = Path(urlparse(args.list_url).path).name
    if source_list_group_id != list_course_slug:
        raise ValueError(
            f"presetとbrowser exportのcourse slugが一致しません: "
            f"preset={list_course_slug} export={source_list_group_id}"
        )
    raw_records = validate_browser_export(browser_export)
    if args.max_questions is not None:
        raw_records = raw_records[: args.max_questions]
    expected_identities = {
        (str(record["quiz_id"]), int(record["question_number"])) for record in raw_records
    }
    expected_keys = {
        f"{quiz_id}:{number:03d}" for quiz_id, number in expected_identities
    }

    json_output_dir, image_output_dir_raw = prepare_output_dirs(
        args.output_dir,
        args.qualification_code,
        args.output_list_group_id,
        "00_source",
    )
    source_dir = Path(json_output_dir)
    image_output_dir = Path(image_output_dir_raw)
    existing_records = load_existing_records(source_dir)
    http_session = create_http_session()
    errors: list[dict[str, str]] = []
    candidates: dict[tuple[str, int], tuple[Path, dict[str, Any], str]] = {}

    with tempfile.TemporaryDirectory(
        prefix=".udemy-refresh-", dir=image_output_dir.parent
    ) as temporary_image_dir:
        staged_image_dir = Path(temporary_image_dir)
        for position, raw in enumerate(raw_records, start=1):
            quiz_id = str(raw.get("quiz_id") or "")
            number = int(raw.get("question_number") or 0)
            identity = (quiz_id, number)
            try:
                record = build_source_record(
                    raw,
                    qualification_code=args.qualification_code,
                    qualification_name=args.qualification_name,
                    output_list_group_id=args.output_list_group_id,
                    source_list_group_id=source_list_group_id,
                    http_session=http_session,
                    image_output_dir=staged_image_dir,
                )
                validation_errors = validate_record(record)
                if validation_errors:
                    raise ValueError(" / ".join(validation_errors))
                expected_path = source_dir / source_filename(quiz_id, number)
                existing = existing_records.get(identity)
                if existing is None:
                    action = "new"
                else:
                    if existing[0] != expected_path:
                        raise ValueError("安定問題IDに対応する00_sourceファイル名が一致しません")
                    action = "unchanged" if existing[1] == record else "updated"
                candidates[identity] = (expected_path, record, action)
                print(
                    f"[STAGE-{action.upper()}] ({position}/{len(raw_records)}) "
                    f"quiz={quiz_id} question={number}"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {"questionId": f"{quiz_id}:{number:03d}", "error": str(exc)}
                )
                print(f"[ERROR] quiz={quiz_id} question={number}: {exc}")

        if set(candidates) != expected_identities:
            for quiz_id, number in sorted(expected_identities - set(candidates)):
                if not any(error["questionId"] == f"{quiz_id}:{number:03d}" for error in errors):
                    errors.append(
                        {
                            "questionId": f"{quiz_id}:{number:03d}",
                            "error": "browser exportからsource recordを生成できません",
                        }
                    )
        if args.max_questions is None:
            unexpected = set(existing_records) - expected_identities
            for quiz_id, number in sorted(unexpected):
                errors.append(
                    {
                        "questionId": f"{quiz_id}:{number:03d}",
                        "error": "browser exportに存在しない既存00_sourceです",
                    }
                )

        new_count = updated_count = verified_count = 0
        if not errors:
            for identity in sorted(candidates):
                _path, record, action = candidates[identity]
                if action == "unchanged":
                    verified_count += 1
                    continue
                save_source_record(
                    source_dir=source_dir,
                    output_list_group_id=args.output_list_group_id,
                    source_list_group_id=source_list_group_id,
                    record=record,
                    replace_existing=action == "updated",
                )
                if action == "new":
                    new_count += 1
                else:
                    updated_count += 1
            for staged_path in sorted(staged_image_dir.glob("*")):
                target = image_output_dir / staged_path.name
                if target.is_file() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(
                    staged_path.read_bytes()
                ).digest():
                    continue
                os.replace(staged_path, target)

    after_records = load_existing_records(source_dir)
    relevant_records = {
        identity: value for identity, value in after_records.items() if identity in expected_identities
    }
    for identity, (_, record) in relevant_records.items():
        for message in validate_record(record):
            errors.append(
                {"questionId": f"{identity[0]}:{identity[1]:03d}", "error": message}
            )
    if len({record[1].get("source_question_id") for record in relevant_records.values()}) != len(
        relevant_records
    ):
        errors.append({"questionId": "", "error": "source_question_idが重複しています"})
    if len({record[1].get("question_url") for record in relevant_records.values()}) != len(
        relevant_records
    ):
        errors.append({"questionId": "", "error": "question_urlが重複しています"})

    report_path = (
        Path(args.output_dir)
        / args.qualification_code
        / "reports"
        / f"udemy_{source_list_group_id}_scrape_result.json"
    )
    report = write_report(
        report_path=report_path,
        browser_export=browser_export,
        expected_keys=expected_keys,
        persisted_records=relevant_records,
        new_count=new_count,
        updated_count=updated_count,
        verified_count=verified_count,
        errors=errors,
        image_output_dir=image_output_dir,
    )
    if report["status"] != "complete":
        print(
            f"[INCOMPLETE] persisted={report['persistedCount']} "
            f"expected={report['expectedCount']} errors={len(errors)} report={report_path}"
        )
        return 1
    print(
        f"[DONE] persisted={report['persistedCount']} new={new_count} "
        f"updated={updated_count} verified={verified_count} "
        f"images={report['imageFileCount']} report={report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
