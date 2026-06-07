#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / "document" / "sources" / "mhlw_kokushi_blueprint"
DEFAULT_BLUEPRINT_TEXT = DEFAULT_SOURCE_DIR / "text" / "blueprint.txt"
DEFAULT_OUTLINE_JSON = DEFAULT_SOURCE_DIR / "blueprint_outline.json"
DEFAULT_CATEGORY_JSON = REPO_ROOT / "output" / "mecnet-kokushi" / "category" / "category.json"

MHLW_PAGE_URL = "https://www.mhlw.go.jp/stf/shingi2/0000128981_00001.html"
BLUEPRINT_PDF_URL = "https://www.mhlw.go.jp/content/10803000/001079480.pdf"
REQUIRED_PDF_URL = "https://www.mhlw.go.jp/content/10803000/001079482.pdf"
GENERAL_PDF_URL = "https://www.mhlw.go.jp/content/10803000/001079483.pdf"
SPECIFIC_PDF_URL = "https://www.mhlw.go.jp/content/10803000/001079484.pdf"

EXPECTED_COUNTS = {
    "required_items": 18,
    "general_chapters": 9,
    "general_items": 82,
    "specific_chapters": 13,
    "specific_items": 100,
}

ROMAN_NUMBER_BY_SYMBOL = {
    "Ⅰ": 1,
    "Ⅱ": 2,
    "Ⅲ": 3,
    "Ⅳ": 4,
    "Ⅴ": 5,
    "Ⅵ": 6,
    "Ⅶ": 7,
    "Ⅷ": 8,
    "Ⅸ": 9,
    "Ⅹ": 10,
    "Ⅺ": 11,
    "Ⅻ": 12,
    "XIII": 13,
}

SECTION_KIND_LABELS = {
    "required": "必修の基本的事項",
    "general": "医学総論",
    "specific": "医学各論",
}

ROMAN_RE = re.compile(r"^(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ|Ⅸ|Ⅹ|Ⅺ|Ⅻ|XIII|)\s+(.*)$")
ITEM_RE = re.compile(r"^(\d+)\s+(.*)$")


@dataclass
class BlueprintItem:
    number: int
    name: str
    approx: str = ""
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "approx": self.approx,
            "page": self.page,
        }


@dataclass
class BlueprintChapter:
    roman: str
    name: str
    approx: str
    items: list[BlueprintItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roman": self.roman,
            "name": self.name,
            "approx": self.approx,
            "items": [item.to_dict() for item in self.items],
        }


def normalize_text(value: str) -> str:
    text = value.replace("", "XIII")
    text = text.replace("⽣", "生").replace("⻑", "長")
    text = re.sub(r"\s+", " ", text).strip()
    replacements = {
        "社会 環境": "社会環境",
        "人工臓 器": "人工臓器",
        "カルシウ ム": "カルシウム",
        "成 長": "成長",
        "異 常": "異常",
        "精神作用 物質": "精神作用物質",
        "身体的苦痛症 または": "身体的苦痛症または",
        "小児・青年期の精神・心身医学的疾患、 成人": "小児・青年期の精神・心身医学的疾患、成人",
        "胸膜・縦隔・横隔膜・胸郭の形態・機能 異常": "胸膜・縦隔・横隔膜・胸郭の形態・機能異常",
        "上肢・下肢の運動器疾患、非感染性骨・ 関節": "上肢・下肢の運動器疾患、非感染性骨・関節",
        "神経・運動器の外傷、脳・脊髄の形成異 常": "神経・運動器の外傷、脳・脊髄の形成異常",
        "臓 器": "臓器",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def load_blueprint_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("=====") or line in {"5", "6", "7", "8"}:
            continue
        lines.append(line)
    return lines


def split_item_text(text: str, needs_approx: bool) -> tuple[str, str, int | None]:
    normalized = normalize_text(text)
    if needs_approx:
        match = re.search(r"約\s*([0-9０-９]+)％\s*([0-9]+)?\s*$", normalized)
        if not match:
            return normalized, "", None
        name = normalized[: match.start()].strip()
        return name, f"約{match.group(1)}％", int(match.group(2)) if match.group(2) else None

    match = re.search(r"\s([0-9]+)\s*$", normalized)
    if match:
        return normalized[: match.start()].strip(), "", int(match.group(1))
    return normalized, "", None


def split_heading_text(rest: str, index: int, end: int, lines: list[str]) -> tuple[str, str, int]:
    buffer = normalize_text(rest)
    match = re.search(r"約\s*([0-9０-９]+)％\s*$", buffer)
    if match:
        return buffer[: match.start()].strip(), f"約{match.group(1)}％", index

    cursor = index + 1
    while cursor < end:
        candidate = lines[cursor]
        if ITEM_RE.match(candidate) or ROMAN_RE.match(candidate) or candidate.startswith("【"):
            break
        buffer = normalize_text(f"{buffer} {candidate}")
        match = re.search(r"約\s*([0-9０-９]+)％\s*$", buffer)
        if match:
            return buffer[: match.start()].strip(), f"約{match.group(1)}％", cursor
        cursor += 1
    return buffer, "", index


def parse_item_at(index: int, end: int, lines: list[str], needs_approx: bool) -> tuple[int, BlueprintItem]:
    match = ITEM_RE.match(lines[index])
    if not match:
        raise ValueError(f"item line expected: {lines[index]}")

    number = int(match.group(1))
    buffer = match.group(2)
    cursor = index + 1
    while cursor < end:
        candidate = lines[cursor]
        if ROMAN_RE.match(candidate) or candidate.startswith("【"):
            break

        if ITEM_RE.match(candidate):
            _, approx, page = split_item_text(buffer, needs_approx)
            if (needs_approx and approx and page is not None) or (not needs_approx and page is not None):
                break

        buffer = f"{buffer} {candidate}"
        cursor += 1
        _, approx, page = split_item_text(buffer, needs_approx)
        if (needs_approx and approx and page is not None) or (not needs_approx and page is not None):
            break

    name, approx, page = split_item_text(buffer, needs_approx)
    return cursor, BlueprintItem(number=number, name=name, approx=approx, page=page)


def parse_required(lines: list[str], start: int, end: int) -> list[BlueprintItem]:
    items: list[BlueprintItem] = []
    index = start
    while index < end:
        if ITEM_RE.match(lines[index]):
            index, item = parse_item_at(index, end, lines, needs_approx=True)
            items.append(item)
            continue
        index += 1
    return items


def parse_chapters(lines: list[str], start: int, end: int, needs_item_approx: bool) -> list[BlueprintChapter]:
    chapters: list[BlueprintChapter] = []
    current: BlueprintChapter | None = None
    index = start
    while index < end:
        roman_match = ROMAN_RE.match(lines[index])
        if roman_match:
            roman = roman_match.group(1).replace("", "XIII")
            name, approx, new_index = split_heading_text(roman_match.group(2), index, end, lines)
            current = BlueprintChapter(roman=roman, name=name, approx=approx)
            chapters.append(current)
            index = new_index + 1
            continue

        if ITEM_RE.match(lines[index]) and current is not None:
            index, item = parse_item_at(index, end, lines, needs_approx=needs_item_approx)
            current.items.append(item)
            continue

        index += 1
    return chapters


def parse_blueprint(path: Path) -> dict[str, Any]:
    lines = load_blueprint_lines(path)
    required_start = lines.index("【必修の基本的事項】")
    general_start = lines.index("【医学総論】")
    specific_start = lines.index("【医学各論】")

    required = parse_required(lines, required_start + 1, general_start)
    general = parse_chapters(lines, general_start + 1, specific_start, needs_item_approx=True)
    specific = parse_chapters(lines, specific_start + 1, len(lines), needs_item_approx=False)

    outline = {
        "metadata": {
            "title": "令和6年版医師国家試験ブループリント（医師国家試験設計表）",
            "sourcePageUrl": MHLW_PAGE_URL,
            "sourcePdfUrls": {
                "blueprint": BLUEPRINT_PDF_URL,
                "required": REQUIRED_PDF_URL,
                "general": GENERAL_PDF_URL,
                "specific": SPECIFIC_PDF_URL,
            },
            "screenshotEvidence": [
                "document/sources/mhlw_kokushi_blueprint/screenshots/blueprint_page_01.png",
                "document/sources/mhlw_kokushi_blueprint/screenshots/blueprint_page_02.png",
                "document/sources/mhlw_kokushi_blueprint/screenshots/blueprint_page_03.png",
                "document/sources/mhlw_kokushi_blueprint/screenshots/blueprint_page_04.png",
            ],
            "note": "PDF text extraction was cross-checked with screenshots; XIII was normalized from the extracted private glyph on page 4.",
        },
        "required": [item.to_dict() for item in required],
        "general": [chapter.to_dict() for chapter in general],
        "specific": [chapter.to_dict() for chapter in specific],
    }
    validate_outline(outline)
    return outline


def validate_outline(outline: dict[str, Any]) -> None:
    required_items = len(outline["required"])
    general_chapters = len(outline["general"])
    general_items = sum(len(chapter["items"]) for chapter in outline["general"])
    specific_chapters = len(outline["specific"])
    specific_items = sum(len(chapter["items"]) for chapter in outline["specific"])
    actual = {
        "required_items": required_items,
        "general_chapters": general_chapters,
        "general_items": general_items,
        "specific_chapters": specific_chapters,
        "specific_items": specific_items,
    }
    if actual != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected blueprint counts: {actual} != {EXPECTED_COUNTS}")

    names = json.dumps(outline, ensure_ascii=False)
    if "" in names or "  " in names:
        raise ValueError("outline contains an unnormalized glyph or double space")


def folder_id(kind: str, chapter_index: int | None = None) -> str:
    if kind == "required":
        return "mk_bp_required"
    if chapter_index is None:
        raise ValueError("chapter_index is required")
    return f"mk_bp_{kind}_{chapter_index:02d}"


def question_set_id(kind: str, item_number: int, chapter_index: int | None = None) -> str:
    if kind == "required":
        return f"mk_bp_required_{item_number:02d}"
    if chapter_index is None:
        raise ValueError("chapter_index is required")
    return f"mk_bp_{kind}_{chapter_index:02d}_{item_number:02d}"


def build_item_description(kind_label: str, parent_name: str, item: dict[str, Any]) -> str:
    parts = [f"令和6年版医師国家試験ブループリントの「{kind_label} > {parent_name}」にある項目。"]
    if item.get("approx"):
        parts.append(f"出題割合: {item['approx']}。")
    if item.get("page") is not None:
        parts.append(f"該当頁: {item['page']}。")
    parts.append("問題単位の questionSetId 付与は別途 semantic mapping で行う。")
    return "".join(parts)


def build_category(outline: dict[str, Any]) -> dict[str, Any]:
    folders: list[dict[str, Any]] = []
    question_sets: list[dict[str, Any]] = []

    required_folder_id = folder_id("required")
    folders.append(
        {
            "folderId": required_folder_id,
            "name": "必修の基本的事項",
            "description": "令和6年版医師国家試験ブループリントの「必修の基本的事項」。",
            "questionCount": 0,
            "isDeleted": False,
            "source": "blueprint",
        }
    )
    for item in outline["required"]:
        question_sets.append(
            {
                "questionSetId": question_set_id("required", item["number"]),
                "folderId": required_folder_id,
                "name": item["name"],
                "description": build_item_description("必修の基本的事項", "必修の基本的事項", item),
                "questionCount": 0,
                "isDeleted": False,
                "blueprintNumber": item["number"],
                "blueprintApproxPercentage": item["approx"],
                "blueprintPage": item["page"],
                "source": "blueprint",
            }
        )

    for kind, chapters in (("general", outline["general"]), ("specific", outline["specific"])):
        kind_label = SECTION_KIND_LABELS[kind]
        for chapter_index, chapter in enumerate(chapters, start=1):
            fid = folder_id(kind, chapter_index)
            folder_name = f"{kind_label}：{chapter['roman']} {chapter['name']}"
            folders.append(
                {
                    "folderId": fid,
                    "name": folder_name,
                    "description": (
                        f"令和6年版医師国家試験ブループリントの「{kind_label} > "
                        f"{chapter['roman']} {chapter['name']}」。出題割合: {chapter['approx']}。"
                    ),
                    "questionCount": 0,
                    "isDeleted": False,
                    "blueprintSection": kind_label,
                    "blueprintRoman": chapter["roman"],
                    "blueprintApproxPercentage": chapter["approx"],
                    "source": "blueprint",
                }
            )
            for item in chapter["items"]:
                question_sets.append(
                    {
                        "questionSetId": question_set_id(kind, item["number"], chapter_index),
                        "folderId": fid,
                        "name": item["name"],
                        "description": build_item_description(kind_label, f"{chapter['roman']} {chapter['name']}", item),
                        "questionCount": 0,
                        "isDeleted": False,
                        "blueprintSection": kind_label,
                        "blueprintRoman": chapter["roman"],
                        "blueprintNumber": item["number"],
                        "blueprintApproxPercentage": item["approx"],
                        "blueprintPage": item["page"],
                        "source": "blueprint",
                    }
                )

    return {
        "metadata": {
            "qualificationId": "mecnet-kokushi",
            "licenseName": "医師国家試験",
            "sourcePageUrl": MHLW_PAGE_URL,
            "sourcePdfUrl": BLUEPRINT_PDF_URL,
            "namingPolicy": "ブループリント（医師国家試験設計表）の表記を原則そのまま利用する。",
            "mappingStatus": "category/questionSet skeleton only; per-question questionSetId mapping is not inferred in this file.",
            "folderCount": len(folders),
            "questionSetCount": len(question_sets),
        },
        "folders": folders,
        "questionSets": question_sets,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build mecnet-kokushi category.json from the MHLW blueprint text extraction.")
    parser.add_argument("--blueprint-text", type=Path, default=DEFAULT_BLUEPRINT_TEXT)
    parser.add_argument("--outline-json", type=Path, default=DEFAULT_OUTLINE_JSON)
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    args = parser.parse_args()

    outline = parse_blueprint(args.blueprint_text)
    category = build_category(outline)
    write_json(args.outline_json, outline)
    write_json(args.category_json, category)
    print(f"wrote outline: {args.outline_json}")
    print(f"wrote category: {args.category_json}")
    print(f"folders={len(category['folders'])} questionSets={len(category['questionSets'])}")


if __name__ == "__main__":
    main()
