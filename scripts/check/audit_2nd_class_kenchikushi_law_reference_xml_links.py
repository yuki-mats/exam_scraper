#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


QUALIFICATION = "2nd-class-kenchikushi"
PATCH_SUBDIR = "21_explanationText_added"
PATCH_GLOB = "question_*_law_merged_explanationText_added_*.json"
DEFAULT_XML_ROOT = Path("/Users/yuki/Downloads/all_xml")
SCOPE_PATH = Path("prompt/qualification_docs/2nd-class-kenchikushi/02_law_reference_scope.md")


def latest_patch_files(repo_root: Path) -> list[Path]:
    root = repo_root / "output" / QUALIFICATION / "questions_json"
    paths: list[Path] = []
    for list_group_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        candidates = sorted((list_group_dir / PATCH_SUBDIR).glob(PATCH_GLOB))
        if candidates:
            paths.append(candidates[-1])
    return paths


def normalize_article(article: str) -> str:
    value = str(article or "").strip()
    value = value.removeprefix("第")
    value = value.replace("条の", "_")
    value = value.removesuffix("条")
    return value.replace("の", "_")


def normalize_paragraph(paragraph: str | None) -> str | None:
    if not paragraph:
        return None
    value = str(paragraph).strip()
    value = value.removeprefix("第").removesuffix("項")
    return value or None


def read_scope_law_ids(repo_root: Path) -> set[str]:
    scope_text = (repo_root / SCOPE_PATH).read_text(encoding="utf-8")
    return set(re.findall(r"`([0-9A-Z]{6,})`", scope_text))


def text_content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


class LawXmlIndex:
    def __init__(self, xml_root: Path) -> None:
        self.xml_root = xml_root
        self._cache: dict[str, dict[str, Any] | None] = {}

    def find_xml_path(self, law_id: str) -> Path | None:
        candidates = sorted(self.xml_root.glob(f"{law_id}_*/*.xml"))
        return candidates[-1] if candidates else None

    def load_law(self, law_id: str) -> dict[str, Any] | None:
        if law_id in self._cache:
            return self._cache[law_id]
        path = self.find_xml_path(law_id)
        if not path:
            self._cache[law_id] = None
            return None
        root = ET.parse(path).getroot()
        law_title = text_content(root.find(".//LawTitle"))
        articles: dict[str, ET.Element] = {}
        # e-Gov XML also contains Articles in supplementary provisions. Index
        # only the current main provisions so duplicate Article Num values do
        # not overwrite the actual current-law article body.
        for article in root.findall("./LawBody/MainProvision//Article"):
            num = article.get("Num")
            if num:
                articles[num] = article
        payload = {"path": str(path), "lawTitle": law_title, "articles": articles}
        self._cache[law_id] = payload
        return payload


def ref_context(entry: dict[str, Any], choice_index: int) -> dict[str, str]:
    choices = entry.get("choiceTextList") or []
    explanations = entry.get("explanationText") or []
    return {
        "choiceText": choices[choice_index] if choice_index < len(choices) else "",
        "explanationText": explanations[choice_index] if choice_index < len(explanations) else "",
    }


def audit(repo_root: Path, xml_root: Path) -> dict[str, Any]:
    scope_law_ids = read_scope_law_ids(repo_root)
    xml_index = LawXmlIndex(xml_root)
    issues: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()

    for patch_path in latest_patch_files(repo_root):
        entries = json.loads(patch_path.read_text(encoding="utf-8"))
        for entry in entries:
            refs_by_choice = entry.get("lawReferences") or []
            for choice_index, refs in enumerate(refs_by_choice):
                if not isinstance(refs, list):
                    continue
                for ref_index, ref in enumerate(refs):
                    counters["referenceCount"] += 1
                    law_id = str(ref.get("lawId") or "")
                    article = str(ref.get("article") or "")
                    issue_base = {
                        "patchFile": str(patch_path),
                        "originalQuestionId": entry.get("original_question_id"),
                        "questionUrl": entry.get("question_url"),
                        "choiceIndex": choice_index,
                        "refIndex": ref_index,
                        "lawTitle": ref.get("lawTitle"),
                        "lawId": law_id,
                        "article": article,
                        "paragraph": ref.get("paragraph"),
                        "item": ref.get("item"),
                        "reason": ref.get("reason"),
                    }
                    if law_id not in scope_law_ids:
                        counters["outOfScopeLawId"] += 1
                        issues.append({**issue_base, "type": "out_of_scope_law_id"})
                    law = xml_index.load_law(law_id)
                    if law is None:
                        counters["missingLawXml"] += 1
                        issues.append({**issue_base, "type": "missing_law_xml"})
                        continue
                    if ref.get("lawTitle") != law["lawTitle"]:
                        counters["lawTitleMismatch"] += 1
                        issues.append(
                            {
                                **issue_base,
                                "type": "law_title_mismatch",
                                "xmlLawTitle": law["lawTitle"],
                                "xmlPath": law["path"],
                            }
                        )
                    article_num = normalize_article(article)
                    article_node = law["articles"].get(article_num)
                    if article_node is None:
                        counters["missingArticle"] += 1
                        issues.append(
                            {
                                **issue_base,
                                "type": "missing_article",
                                "normalizedArticle": article_num,
                                "xmlPath": law["path"],
                            }
                        )
                        continue
                    paragraph_num = normalize_paragraph(ref.get("paragraph"))
                    if paragraph_num is not None and article_node.find(f"./Paragraph[@Num='{paragraph_num}']") is None:
                        counters["missingParagraph"] += 1
                        issues.append(
                            {
                                **issue_base,
                                "type": "missing_paragraph",
                                "normalizedArticle": article_num,
                                "normalizedParagraph": paragraph_num,
                                "xmlPath": law["path"],
                            }
                        )
    return {
        "summary": {
            "referenceCount": counters["referenceCount"],
            "outOfScopeLawId": counters["outOfScopeLawId"],
            "missingLawXml": counters["missingLawXml"],
            "lawTitleMismatch": counters["lawTitleMismatch"],
            "missingArticle": counters["missingArticle"],
            "missingParagraph": counters["missingParagraph"],
            "issueCount": len(issues),
        },
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--xml-root", type=Path, default=DEFAULT_XML_ROOT)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit(args.repo_root, args.xml_root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return 1 if args.strict and result["summary"]["issueCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
