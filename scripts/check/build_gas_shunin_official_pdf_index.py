#!/usr/bin/env python3
"""Build a reproducible official-PDF index for gas-shunin questions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


OCR_SCHEMA = "gas-shunin-official-question-pdf-ocr/v1"
INDEX_SCHEMA = "gas-shunin-official-question-identity-index/v1"
ANSWER_INDEX_SCHEMA = "gas-shunin-official-answer-index/v1"
DOCUMENT_INDEX_SCHEMA = "gas-shunin-official-document-index/v1"
EXPECTED_BY_SECTION = {"law": 16, "basic": 15, "gas": 27}
SECTION_NAMES = {"法": "law", "基": "basic", "ガ": "gas"}
ANSWER_ROW_LENGTHS = (16, 15, 9, 9, 9)
DISPLAY_QUOTE_RE = re.compile(r"\[quote\](.*?)\[/quote\]\s*$", re.DOTALL)

# The 2020 and 2022 official answer PDFs are image-only. These rows were
# transcribed from the rendered first page and independently checked against
# that page at 200 dpi. The five rows are law, basic, manufacture, supply, and
# consumption, in that order.
SCANNED_ANSWER_ROWS: dict[tuple[int, str], list[list[int]]] = {
    (2020, "kou"): [
        [1, 4, 5, 3, 5, 1, 3, 2, 2, 2, 4, 1, 4, 3, 2, 5],
        [3, 5, 2, 3, 2, 3, 5, 1, 5, 4, 4, 4, 2, 1, 1],
        [3, 3, 5, 4, 3, 2, 4, 1, 2],
        [4, 2, 3, 5, 1, 2, 4, 3, 2],
        [2, 5, 5, 1, 4, 3, 1, 3, 1],
    ],
    (2020, "otsu"): [
        [1, 1, 3, 1, 2, 4, 3, 5, 2, 3, 4, 5, 2, 5, 2, 4],
        [3, 3, 4, 1, 3, 2, 1, 4, 2, 5, 5, 4, 4, 5, 2],
        [5, 2, 4, 3, 5, 4, 3, 1, 4],
        [3, 4, 5, 3, 2, 1, 4, 1, 5],
        [5, 4, 2, 2, 1, 4, 3, 5, 3],
    ],
    (2022, "kou"): [
        [2, 5, 1, 5, 4, 2, 2, 4, 3, 3, 2, 1, 3, 4, 2, 3],
        [1, 3, 4, 3, 2, 5, 3, 4, 2, 5, 1, 2, 2, 4, 5],
        [5, 1, 3, 4, 5, 2, 4, 3, 2],
        [1, 3, 2, 3, 2, 4, 4, 5, 3],
        [5, 3, 4, 3, 4, 4, 2, 1, 5],
    ],
    (2022, "otsu"): [
        [2, 5, 2, 5, 4, 3, 1, 4, 5, 4, 1, 3, 4, 2, 2, 3],
        [2, 3, 1, 3, 2, 4, 3, 5, 1, 2, 5, 5, 4, 4, 1],
        [1, 4, 1, 5, 4, 4, 4, 3, 2],
        [5, 5, 3, 3, 1, 4, 4, 5, 2],
        [4, 5, 3, 2, 2, 4, 1, 3, 1],
    ],
}

# These documents are the only cases left below the automatic text-support
# thresholds. Each override was resolved by viewing the listed official PDF
# page and comparing the complete body and choices. Identity is kept unless the
# body actually belongs to another official question.
DOCUMENT_IDENTITY_OVERRIDES: dict[str, dict[str, Any]] = {
    **{
        f"gas-shunin-kou-2018-shohi-q20-s{number:02d}": {
            "identity": ("kou", 2018, "gas", 20),
            "pdfPage": 29,
            "reason": "official question PDF page 29 visually matches gas question 20",
        }
        for number in range(1, 6)
    },
    **{
        f"gas-shunin-otsu-2025-seizo-q02-s{number:02d}": {
            "identity": ("otsu", 2025, "gas", 2),
            "pdfPage": 26,
            "reason": "official question PDF page 26 visually matches gas question 2",
        }
        for number in range(1, 6)
    },
    "gasushunin-otsushu-hourei-2021-5-2": {
        "identity": ("otsu", 2021, "law", 6),
        "pdfPage": 7,
        "reason": "body and choice visually match official law question 6, not question 5",
    },
    "gasushunin-otsushu-hourei-2023-1-1": {
        "identity": ("otsu", 2023, "law", 1),
        "pdfPage": 3,
        "reason": "official question PDF page 3 visually matches law question 1",
    },
    "gasushunin-otsushu-hourei-2023-1-4": {
        "identity": ("otsu", 2023, "law", 1),
        "pdfPage": 3,
        "reason": "official question PDF page 3 visually matches law question 1",
    },
    **{
        f"chiefgasengineerlicense-A-10-{number:04d}": {
            "identity": ("kou", 2022, "law", 1),
            "pdfPage": 3,
            "reason": "contiguous 2022 law question 1 import; display body and choice match the official PDF",
        }
        for number in range(241, 246)
    },
    "chiefgasengineerlicense-A-10-0246": {
        "identity": ("kou", 2022, "law", 2),
        "pdfPage": 4,
        "reason": "contiguous 2022 law question 2 import; display body and choice match the official PDF",
    },
    **{
        f"chiefgasengineerlicense-A-10-{number:04d}": {
            "identity": ("kou", 2023, "law", 1),
            "pdfPage": 3,
            "reason": "contiguous 2023 law question 1 import; display body and choice match the official PDF",
        }
        for number in range(321, 326)
    },
    "chiefgasengineerlicense-A-10-0326": {
        "identity": ("kou", 2023, "law", 2),
        "pdfPage": 4,
        "reason": "contiguous 2023 law question 2 import; display body and choice match the official PDF",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]+", "", text)


def ngrams(value: str, size: int = 3) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def coverage_score(needle: str, haystack: str) -> float:
    needle_grams = ngrams(needle)
    if not needle_grams:
        return 0.0
    return len(needle_grams & ngrams(haystack)) / len(needle_grams)


def parse_pdf_identity(path: Path) -> tuple[int, str]:
    match = re.match(r"q_(kou|otsu)_", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"unsupported question PDF: {path}")
    return int(path.parent.name), match.group(1).lower()


def parse_answer_pdf_identity(path: Path) -> tuple[int, str]:
    match = re.match(r"a_(kou|otsu)_", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"unsupported answer PDF: {path}")
    return int(path.parent.name), match.group(1).lower()


def ocr_image(path: Path) -> str:
    from Foundation import NSURL
    from Vision import (
        VNImageRequestHandler,
        VNRecognizeTextRequest,
        VNRequestTextRecognitionLevelAccurate,
    )

    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["ja-JP"])
    request.setUsesLanguageCorrection_(True)
    handler = VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(str(path)), {}
    )
    ok, error = handler.performRequests_error_([request], None)
    if not ok or error:
        raise RuntimeError(f"Vision OCR failed for {path}: {error}")
    results = request.results()
    lines: list[tuple[float, str]] = []
    for index in range(results.count() if results is not None else 0):
        observation = results.objectAtIndex_(index)
        candidates = observation.topCandidates_(1)
        if candidates.count() == 0:
            continue
        candidate = candidates.objectAtIndex_(0)
        lines.append((observation.boundingBox().origin.y, candidate.string()))
    return "\n".join(text for _, text in sorted(lines, reverse=True))


def render_and_ocr_pdf(
    path: Path, *, pdftoppm: str, dpi: int
) -> dict[str, Any]:
    year, grade = parse_pdf_identity(path)
    with tempfile.TemporaryDirectory(prefix=f"gas-shunin-{year}-{grade}-") as temp:
        prefix = Path(temp) / "page"
        subprocess.run(
            [
                pdftoppm,
                "-f",
                "3",
                "-jpeg",
                "-r",
                str(dpi),
                str(path),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        images = sorted(
            Path(temp).glob("page-*.jpg"),
            key=lambda item: int(item.stem.rsplit("-", 1)[1]),
        )
        pages: list[dict[str, Any]] = []
        for image in images:
            pdf_page = int(image.stem.rsplit("-", 1)[1])
            print(f"OCR {path} page {pdf_page}", flush=True)
            pages.append({"pdfPage": pdf_page, "text": ocr_image(image)})
    return {
        "year": year,
        "grade": grade,
        "path": str(path),
        "sha256": sha256_file(path),
        "pages": pages,
    }


def ocr_questions(args: argparse.Namespace) -> None:
    pdfs = sorted(
        path
        for path in args.pdf_root.glob("20*/q_*.pdf")
        if re.match(r"q_(kou|otsu)_", path.name, re.IGNORECASE)
    )
    if len(pdfs) != 18:
        raise ValueError(f"expected 18 official question PDFs, found {len(pdfs)}")
    existing = read_json(args.output) if args.output.exists() else None
    cached = {
        (item["year"], item["grade"]): item
        for item in (existing or {}).get("pdfs", [])
        if isinstance(item, dict)
    }
    records: list[dict[str, Any]] = []
    for path in pdfs:
        year, grade = parse_pdf_identity(path)
        current_hash = sha256_file(path)
        cached_record = cached.get((year, grade))
        if cached_record and cached_record.get("sha256") == current_hash:
            records.append(cached_record)
        else:
            records.append(
                render_and_ocr_pdf(path, pdftoppm=args.pdftoppm, dpi=args.dpi)
            )
        payload = {
            "schemaVersion": OCR_SCHEMA,
            "generatedAt": utc_now(),
            "method": {
                "renderer": "pdftoppm",
                "ocr": "Apple Vision accurate Japanese recognition",
                "dpi": args.dpi,
            },
            "pdfs": sorted(records, key=lambda item: (item["year"], item["grade"])),
        }
        write_json_atomic(args.output, payload)
    print(json.dumps({"pdfCount": len(records)}, ensure_ascii=False))


def section_from_identifier(value: Any) -> str | None:
    text = str(value or "").lower()
    if "hourei" in text or "-law-" in text:
        return "law"
    if "kiso" in text or "-basic-" in text:
        return "basic"
    if any(token in text for token in ("gizyutsu", "seizo", "shohi", "kyokyu", "-gas-")):
        return "gas"
    return None


def number_from_identifier(value: Any) -> int | None:
    text = str(value or "").lower()
    for pattern in (
        r"-q0*(\d+)(?:-|$)",
        r"-(?:19|20)\d{2}-0*(\d+)(?:-\d+)?$",
        r"問(?:番号[：:]?)?\s*0*(\d+)",
    ):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def canonical_identity(
    audit: dict[str, Any], decoded: dict[str, Any]
) -> tuple[str, int, str, int]:
    grade = "kou" if audit.get("grade") == "甲種" else "otsu"
    source_match = audit.get("sourceMatch") or {}
    values = [
        source_match.get("sourceKey"),
        decoded.get("originalQuestionId"),
        audit.get("questionId"),
    ]
    section = next(
        (section_from_identifier(value) for value in values if section_from_identifier(value)),
        None,
    )
    question_number = next(
        (number_from_identifier(value) for value in values if number_from_identifier(value)),
        None,
    ) or number_from_identifier(decoded.get("examSource"))
    if not section or not question_number:
        raise ValueError(f"cannot resolve identity: {audit.get('questionId')}")
    return grade, int(audit["examYear"]), section, question_number


def load_active_raw(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        for document in read_json(path)["documents"]:
            decoded = document["decoded"]
            if decoded.get("isDeleted") is True or decoded.get("isChoiceOnly") is True:
                continue
            question_id = document["_id"]
            if question_id in result:
                raise ValueError(f"duplicate raw question: {question_id}")
            result[question_id] = decoded
    return result


def parse_header_candidates(text: str) -> list[tuple[str | None, int]]:
    candidates: list[tuple[str | None, int]] = []
    for raw_line in text.splitlines():
        line = unicodedata.normalize("NFKC", raw_line).strip()
        explicit = re.match(r"^[（(]?([法基ガ])[）)]?\s*問\s*(\d{1,2})(?:\D|$)", line)
        if explicit:
            candidates.append((SECTION_NAMES[explicit.group(1)], int(explicit.group(2))))
            continue
        generic = re.match(r"^問\s*(\d{1,2})(?:\D|$)", line)
        if generic and not line.startswith("問番号"):
            candidates.append((None, int(generic.group(1))))
    return candidates


def build_header_index(pdf: dict[str, Any]) -> dict[tuple[str, int], int]:
    result: dict[tuple[str, int], int] = {}
    current_section = "law"
    seen_by_section: dict[str, set[int]] = defaultdict(set)
    section_order = ["law", "basic", "gas"]
    for page in pdf["pages"]:
        for explicit_section, number in parse_header_candidates(page["text"]):
            if explicit_section:
                current_section = explicit_section
            elif number == 1:
                current_index = section_order.index(current_section)
                expected = EXPECTED_BY_SECTION[current_section]
                if len(seen_by_section[current_section]) >= expected and current_index < 2:
                    current_section = section_order[current_index + 1]
            if not (1 <= number <= EXPECTED_BY_SECTION[current_section]):
                continue
            key = (current_section, number)
            if key not in result:
                result[key] = page["pdfPage"]
            seen_by_section[current_section].add(number)
    return result


def group_evidence_text(records: list[dict[str, Any]]) -> str:
    bodies: list[str] = []
    choices: list[str] = []
    for decoded in records:
        body = decoded.get("originalQuestionBodyText") or decoded.get("questionBodyText")
        if isinstance(body, str) and body not in bodies:
            bodies.append(body)
        choice = decoded.get("originalQuestionChoiceText")
        if isinstance(choice, str) and choice not in choices:
            choices.append(choice)
    return normalize_text("\n".join(bodies + choices))


def build_question_index(args: argparse.Namespace) -> None:
    ocr = read_json(args.ocr)
    if ocr.get("schemaVersion") != OCR_SCHEMA or len(ocr.get("pdfs", [])) != 18:
        raise ValueError("complete 18-PDF OCR cache required")
    audit_records = read_jsonl(args.live_audit)
    raw_by_id = load_active_raw([args.kou_raw, args.otsu_raw])
    if {item["questionId"] for item in audit_records} != set(raw_by_id):
        raise ValueError("live audit and raw question IDs differ")

    grouped: dict[tuple[str, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    question_ids: dict[tuple[str, int, str, int], list[str]] = defaultdict(list)
    for audit in audit_records:
        question_id = audit["questionId"]
        identity = canonical_identity(audit, raw_by_id[question_id])
        grouped[identity].append(raw_by_id[question_id])
        question_ids[identity].append(question_id)
    if len(grouped) != 1044:
        raise ValueError(f"expected 1044 original question identities, found {len(grouped)}")

    pdf_by_grade_year = {
        (item["grade"], item["year"]): item for item in ocr["pdfs"]
    }
    output_records: list[dict[str, Any]] = []
    status_counts: defaultdict[str, int] = defaultdict(int)
    for identity in sorted(grouped):
        grade, year, section, question_number = identity
        pdf = pdf_by_grade_year[(grade, year)]
        header_index = build_header_index(pdf)
        header_page = header_index.get((section, question_number))
        evidence = group_evidence_text(grouped[identity])
        scored = sorted(
            (
                coverage_score(evidence, normalize_text(page["text"])),
                page["pdfPage"],
            )
            for page in pdf["pages"]
        )
        best_score, best_page = scored[-1]
        second_score = scored[-2][0] if len(scored) > 1 else 0.0
        margin = best_score - second_score
        if header_page == best_page and best_score >= 0.35:
            status = "verified_header_and_text"
            pdf_page = best_page
        elif best_score >= 0.62 and margin >= 0.05:
            status = "verified_text_unique"
            pdf_page = best_page
        elif header_page is not None and best_score >= 0.25 and header_page in {
            best_page,
            scored[-2][1],
        }:
            status = "verified_header_with_text_support"
            pdf_page = header_page
        else:
            status = "hold"
            pdf_page = None
        status_counts[status] += 1
        output_records.append(
            {
                "grade": grade,
                "examYear": year,
                "section": section,
                "questionNumber": question_number,
                "questionIds": sorted(question_ids[identity]),
                "officialQuestionPdf": {
                    "path": pdf["path"],
                    "sha256": pdf["sha256"],
                    "pdfPage": pdf_page,
                },
                "evidence": {
                    "headerPage": header_page,
                    "bestTextPage": best_page,
                    "bestTextCoverage": round(best_score, 6),
                    "secondTextCoverage": round(second_score, 6),
                    "margin": round(margin, 6),
                },
                "status": status,
            }
        )
    result = {
        "schemaVersion": INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "sourcePolicy": "official question PDFs only; listing site not used",
        "summary": {
            "identityCount": len(output_records),
            "questionDocumentCount": sum(len(item["questionIds"]) for item in output_records),
            "statusCounts": dict(sorted(status_counts.items())),
        },
        "records": output_records,
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


def extract_answer_rows(text: str) -> list[list[int]]:
    normalized = unicodedata.normalize("NFKC", text)
    rows: list[list[int]] = []
    for line in normalized.splitlines():
        match = re.search(r"正\s*解(.*)$", line)
        if not match:
            continue
        rows.append([int(value) for value in re.findall(r"[1-5]", match.group(1))])
    if tuple(len(row) for row in rows) != ANSWER_ROW_LENGTHS:
        raise ValueError(
            "answer row lengths differ: "
            f"expected={ANSWER_ROW_LENGTHS}, actual={tuple(len(row) for row in rows)}"
        )
    return rows


def answer_records_from_rows(
    *, year: int, grade: str, path: Path, rows: list[list[int]], method: str
) -> list[dict[str, Any]]:
    if tuple(len(row) for row in rows) != ANSWER_ROW_LENGTHS:
        raise ValueError(f"invalid answer rows for {year} {grade}")
    answers = {
        "law": rows[0],
        "basic": rows[1],
        "gas": rows[2] + rows[3] + rows[4],
    }
    pdf_hash = sha256_file(path)
    return [
        {
            "grade": grade,
            "examYear": year,
            "section": section,
            "questionNumber": question_number,
            "correctChoiceNumber": answer,
            "officialAnswerPdf": {
                "path": str(path),
                "sha256": pdf_hash,
                "pdfPage": 1,
                "extractionMethod": method,
            },
            "status": "verified",
        }
        for section in ("law", "basic", "gas")
        for question_number, answer in enumerate(answers[section], start=1)
    ]


def build_answer_index(args: argparse.Namespace) -> None:
    from pypdf import PdfReader

    pdfs = sorted(
        path
        for path in args.pdf_root.glob("20*/a_*.pdf")
        if re.match(r"a_(kou|otsu)_", path.name, re.IGNORECASE)
    )
    if len(pdfs) != 18:
        raise ValueError(f"expected 18 official answer PDFs, found {len(pdfs)}")
    output_records: list[dict[str, Any]] = []
    pdf_records: list[dict[str, Any]] = []
    for path in pdfs:
        year, grade = parse_answer_pdf_identity(path)
        embedded_text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if embedded_text.strip():
            rows = extract_answer_rows(embedded_text)
            method = "embedded PDF text parsed and row lengths verified"
        else:
            rows = SCANNED_ANSWER_ROWS.get((year, grade))
            if rows is None:
                raise ValueError(f"image-only answer PDF lacks verified rows: {path}")
            method = "200-dpi rendered page visually transcribed and checked"
        records = answer_records_from_rows(
            year=year, grade=grade, path=path, rows=rows, method=method
        )
        output_records.extend(records)
        pdf_records.append(
            {
                "year": year,
                "grade": grade,
                "path": str(path),
                "sha256": sha256_file(path),
                "answerCount": len(records),
                "extractionMethod": method,
            }
        )
    identities = {
        (item["grade"], item["examYear"], item["section"], item["questionNumber"])
        for item in output_records
    }
    if len(output_records) != 1044 or len(identities) != 1044:
        raise ValueError(
            f"expected 1044 unique answer identities, got records={len(output_records)} "
            f"identities={len(identities)}"
        )
    result = {
        "schemaVersion": ANSWER_INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "sourcePolicy": "official answer PDFs only; listing site not used",
        "summary": {
            "pdfCount": len(pdf_records),
            "identityCount": len(identities),
            "statusCounts": {"verified": len(output_records)},
            "imageOnlyPdfCount": sum(
                1
                for item in pdf_records
                if item["extractionMethod"].startswith("200-dpi")
            ),
        },
        "pdfs": pdf_records,
        "records": sorted(
            output_records,
            key=lambda item: (
                item["examYear"],
                item["grade"],
                item["section"],
                item["questionNumber"],
            ),
        ),
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


def parse_official_question_header(
    line: str, current_section: str, seen: set[tuple[str, int]]
) -> tuple[str, int] | None:
    normalized = unicodedata.normalize("NFKC", line).strip().replace("間", "問")
    # The introductory range lines such as （ガ）問1～（ガ）問9 are not
    # question starts.
    if "～" in normalized or "~" in normalized:
        return None
    match = re.match(
        r"^[^一-龠ぁ-んァ-ヶ0-9]{0,4}[（(]?\s*([法基ガが分力])?"
        r"[）)]?\s*問\s*(\d{1,2})(?:\D|$)",
        normalized,
    )
    if not match:
        return None
    section = {
        "法": "law",
        "基": "basic",
        "ガ": "gas",
        "が": "gas",
        "分": "gas",
        "力": "gas",
    }.get(match.group(1), current_section)
    number = int(match.group(2))
    if not match.group(1) and number == 1:
        seen_numbers = {item[1] for item in seen if item[0] == current_section}
        if len(seen_numbers) >= EXPECTED_BY_SECTION[current_section]:
            section = {"law": "basic", "basic": "gas", "gas": "gas"}[
                current_section
            ]
    if not 1 <= number <= EXPECTED_BY_SECTION[section]:
        return None
    return section, number


def build_official_question_blocks(
    ocr: dict[str, Any],
) -> tuple[
    dict[tuple[str, int, str, int], str],
    dict[tuple[str, int, str, int], int],
]:
    blocks: dict[tuple[str, int, str, int], list[str]] = {}
    start_pages: dict[tuple[str, int, str, int], int] = {}
    for pdf in ocr["pdfs"]:
        current_section = "law"
        seen: set[tuple[str, int]] = set()
        current_key: tuple[str, int, str, int] | None = None
        for page in pdf["pages"]:
            for line in page["text"].splitlines():
                if "3.ガス技術" in unicodedata.normalize("NFKC", line):
                    current_section = "gas"
                    current_key = None
                header = parse_official_question_header(line, current_section, seen)
                if header:
                    current_section = header[0]
                    seen.add(header)
                    current_key = (pdf["grade"], pdf["year"], *header)
                    blocks.setdefault(current_key, [])
                    start_pages.setdefault(current_key, page["pdfPage"])
                if current_key:
                    blocks[current_key].append(line)
    return (
        {key: normalize_text("\n".join(lines)) for key, lines in blocks.items()},
        start_pages,
    )


def score_coverage_from_grams(needle_grams: set[str], haystack: str) -> float:
    if not needle_grams:
        return 0.0
    return len(needle_grams & ngrams(haystack)) / len(needle_grams)


def display_question_evidence(live: dict[str, Any]) -> tuple[str, str]:
    """Return the body and choice that users actually see.

    The historical original fields are not authoritative here: some imported
    documents contain a display question from another year while retaining an
    older original body.  Official-PDF identity therefore starts from the
    rendered questionText and its quote, and only falls back to the original
    choice when no quote is present.
    """

    question_text = str(live.get("questionText") or "")
    match = DISPLAY_QUOTE_RE.search(question_text)
    choice = match.group(1) if match else str(live.get("originalQuestionChoiceText") or "")
    body = DISPLAY_QUOTE_RE.sub("", question_text)
    if not body.strip():
        body = str(live.get("originalQuestionBodyText") or "")
    return normalize_text(body), normalize_text(choice)


def ranked_block_scores(
    evidence_grams: set[str],
    candidates: Iterable[tuple[tuple[str, int, str, int], str]],
) -> list[tuple[float, tuple[str, int, str, int]]]:
    return sorted(
        (score_coverage_from_grams(evidence_grams, block), identity)
        for identity, block in candidates
    )


def build_document_index(args: argparse.Namespace) -> None:
    ocr = read_json(args.ocr)
    question_index = read_json(args.question_index)
    answer_index = read_json(args.answer_index)
    live_records = read_jsonl(args.live_audit)
    if len(live_records) != 4326:
        raise ValueError(f"expected 4326 live records, got {len(live_records)}")

    current_identity_by_id = {
        question_id: (
            record["grade"],
            record["examYear"],
            record["section"],
            record["questionNumber"],
        )
        for record in question_index["records"]
        for question_id in record["questionIds"]
    }
    question_by_identity = {
        (
            record["grade"],
            record["examYear"],
            record["section"],
            record["questionNumber"],
        ): record
        for record in question_index["records"]
    }
    answer_by_identity = {
        (
            record["grade"],
            record["examYear"],
            record["section"],
            record["questionNumber"],
        ): record
        for record in answer_index["records"]
    }
    if set(question_by_identity) != set(answer_by_identity):
        raise ValueError("question and answer identity sets differ")

    blocks, block_pages = build_official_question_blocks(ocr)
    blocks_by_grade_year: dict[
        tuple[str, int], list[tuple[tuple[str, int, str, int], str]]
    ] = defaultdict(list)
    for identity, block in blocks.items():
        blocks_by_grade_year[identity[:2]].append((identity, block))
    page_texts = {
        (pdf["grade"], pdf["year"], page["pdfPage"]): normalize_text(page["text"])
        for pdf in ocr["pdfs"]
        for page in pdf["pages"]
    }
    pdf_by_grade_year = {
        (pdf["grade"], pdf["year"]): pdf for pdf in ocr["pdfs"]
    }

    output_records: list[dict[str, Any]] = []
    status_counts: defaultdict[str, int] = defaultdict(int)
    remapped_count = 0
    for live in live_records:
        question_id = live["questionId"]
        current_identity = current_identity_by_id[question_id]
        display_body, display_choice = display_question_evidence(live["live"])
        original_body = normalize_text(live["live"].get("originalQuestionBodyText"))
        original_choice = normalize_text(live["live"].get("originalQuestionChoiceText"))
        evidence_grams = ngrams(display_body + display_choice)
        body_grams = ngrams(display_body)
        candidate_scores = ranked_block_scores(
            evidence_grams,
            blocks_by_grade_year[current_identity[:2]],
        )
        best_score, best_identity = candidate_scores[-1]
        current_block_score = next(
            (score for score, identity in candidate_scores if identity == current_identity),
            None,
        )
        override = DOCUMENT_IDENTITY_OVERRIDES.get(question_id)
        if override:
            final_identity = tuple(override["identity"])
            pdf_page = int(override["pdfPage"])
            match_status = (
                "needs_content_repair"
                if override.get("requiresContentRepair")
                else "verified_manual_pdf"
            )
            match_reason = override["reason"]
        elif (
            current_block_score is not None
            and best_identity != current_identity
            and best_score >= 0.60
            and best_score - current_block_score >= 0.25
        ):
            final_identity = best_identity
            pdf_page = block_pages[best_identity]
            match_status = "verified_text_remap"
            match_reason = "official question block has decisive text advantage"
        elif current_block_score is not None and current_block_score >= 0.35:
            final_identity = current_identity
            pdf_page = block_pages[current_identity]
            match_status = "verified_question_block"
            match_reason = "display question is supported by its official question block"
        else:
            original_score = score_coverage_from_grams(
                ngrams(original_body + original_choice),
                blocks.get(current_identity, ""),
            )
            current_record = question_by_identity[current_identity]
            pdf_page = current_record["officialQuestionPdf"]["pdfPage"]
            page_score = score_coverage_from_grams(
                body_grams,
                page_texts[(current_identity[0], current_identity[1], pdf_page)],
            )
            if max(original_score, page_score) < 0.35:
                match_status = "hold"
                match_reason = "official PDF text support below threshold"
            elif original_score >= page_score:
                match_status = "verified_original_fields_fallback"
                match_reason = "display text is transformed; original fields support the current official question"
            else:
                match_status = "verified_question_page"
                match_reason = "display body is supported by official question page"
            final_identity = current_identity
        if final_identity != current_identity:
            remapped_count += 1
        status_counts[match_status] += 1
        answer = answer_by_identity[final_identity]
        pdf = pdf_by_grade_year[final_identity[:2]]
        output_records.append(
            {
                "questionId": question_id,
                "grade": final_identity[0],
                "examYear": final_identity[1],
                "section": final_identity[2],
                "questionNumber": final_identity[3],
                "previousIdentity": {
                    "grade": current_identity[0],
                    "examYear": current_identity[1],
                    "section": current_identity[2],
                    "questionNumber": current_identity[3],
                },
                "identityChanged": final_identity != current_identity,
                "requiresContentRepair": bool(
                    override and override.get("requiresContentRepair")
                ),
                "officialQuestionPdf": {
                    "path": pdf["path"],
                    "sha256": pdf["sha256"],
                    "pdfPage": pdf_page,
                },
                "officialAnswerPdf": answer["officialAnswerPdf"],
                "officialCorrectChoiceNumber": answer["correctChoiceNumber"],
                "match": {
                    "status": match_status,
                    "reason": match_reason,
                    "bestBlockScore": round(best_score, 6),
                    "currentBlockScore": (
                        round(current_block_score, 6)
                        if current_block_score is not None
                        else None
                    ),
                    "bestBlockIdentity": {
                        "grade": best_identity[0],
                        "examYear": best_identity[1],
                        "section": best_identity[2],
                        "questionNumber": best_identity[3],
                    },
                },
                "liveAudit": {
                    "questionType": live["questionType"],
                    "answerCheck": live["answerCheck"],
                    "schemaIssues": live["schemaIssues"],
                    "contentIssues": live["contentIssues"],
                    "reviewIssues": live["reviewIssues"],
                    "overallStatus": live["overallStatus"],
                },
            }
        )
    question_ids = [record["questionId"] for record in output_records]
    final_identities = {
        (
            record["grade"],
            record["examYear"],
            record["section"],
            record["questionNumber"],
        )
        for record in output_records
    }
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("document index contains duplicate question IDs")
    if len(final_identities) != 1044:
        raise ValueError(f"expected 1044 represented identities, got {len(final_identities)}")
    result = {
        "schemaVersion": DOCUMENT_INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "sourcePolicy": "official question and answer PDFs only; listing site not used",
        "summary": {
            "questionDocumentCount": len(output_records),
            "uniqueQuestionDocumentCount": len(set(question_ids)),
            "representedOfficialIdentityCount": len(final_identities),
            "parsedOfficialQuestionBlockCount": len(blocks),
            "identityChangedDocumentCount": remapped_count,
            "manualPdfVerificationCount": sum(
                count
                for status, count in status_counts.items()
                if status == "verified_manual_pdf"
            ),
            "contentRepairRequiredCount": status_counts.get(
                "needs_content_repair", 0
            ),
            "holdCount": status_counts.get("hold", 0),
            "statusCounts": dict(sorted(status_counts.items())),
        },
        "records": sorted(output_records, key=lambda item: item["questionId"]),
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)
    ocr = subparsers.add_parser("ocr-questions")
    ocr.add_argument("--pdf-root", type=Path, required=True)
    ocr.add_argument("--output", type=Path, required=True)
    ocr.add_argument("--pdftoppm", default=shutil.which("pdftoppm"))
    ocr.add_argument("--dpi", type=int, default=100)
    ocr.set_defaults(handler=ocr_questions)

    index = subparsers.add_parser("build-question-index")
    index.add_argument("--ocr", type=Path, required=True)
    index.add_argument("--live-audit", type=Path, required=True)
    index.add_argument("--kou-raw", type=Path, required=True)
    index.add_argument("--otsu-raw", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    index.set_defaults(handler=build_question_index)

    answers = subparsers.add_parser("build-answer-index")
    answers.add_argument("--pdf-root", type=Path, required=True)
    answers.add_argument("--output", type=Path, required=True)
    answers.set_defaults(handler=build_answer_index)

    documents = subparsers.add_parser("build-document-index")
    documents.add_argument("--ocr", type=Path, required=True)
    documents.add_argument("--question-index", type=Path, required=True)
    documents.add_argument("--answer-index", type=Path, required=True)
    documents.add_argument("--live-audit", type=Path, required=True)
    documents.add_argument("--output", type=Path, required=True)
    documents.set_defaults(handler=build_document_index)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "ocr-questions" and not args.pdftoppm:
        raise SystemExit("pdftoppm not found; pass --pdftoppm")
    args.handler(args)


if __name__ == "__main__":
    main()
