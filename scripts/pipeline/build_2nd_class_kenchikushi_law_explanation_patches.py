#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


QUALIFICATION = "2nd-class-kenchikushi"
ROOT_DIR = Path("output") / QUALIFICATION / "questions_json"
PATCH_SUBDIR = "21_explanationText_added"
SOURCE_SUBDIR = "20_merged_1"
TARGET_CATEGORY_KEYWORD = "建築法規"

TODAY = date(2026, 6, 1).isoformat()
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M")

FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")

CHOICE_PREFIX_RE = re.compile(r"^選択肢\s*[0-9０-９]+\.\s*")
ARTICLE_RE = re.compile(r"第?\s*([0-9]+条(?:の[0-9]+)?)")
PARAGRAPH_RE = re.compile(r"第?\s*([0-9]+項)")
ITEM_RE = re.compile(r"第?\s*([一二三四五六七八九十百]+号|[0-9]+号)")
SEPARATOR_RE = re.compile(r"[、,，]|及び|および|又は|または|並びに|・")
REFERENCE_TRIGGER_RE = re.compile(
    r"(法|令|施行令|規則|施行規則|士法|建築士法|建築基準法|都市計画法|消防法|下水道法|民法|土地区画整理法|告示|第?\s*[0-9０-９]+条)"
)
PAREN_REFERENCE_RE = re.compile(r"[（(]([^()（）]{1,120})[)）]")


LAW_METADATA: dict[str, dict[str, str]] = {
    "建築基準法施行規則": {"lawTitle": "建築基準法施行規則", "lawId": "325M50004000040", "verificationStatus": "verified"},
    "建築基準法施行令": {"lawTitle": "建築基準法施行令", "lawId": "325CO0000000338", "verificationStatus": "verified"},
    "建築基準法": {"lawTitle": "建築基準法", "lawId": "325AC0000000201", "verificationStatus": "verified"},
    "基準法施行規則": {"lawTitle": "建築基準法施行規則", "lawId": "325M50004000040", "verificationStatus": "verified"},
    "基準法施行令": {"lawTitle": "建築基準法施行令", "lawId": "325CO0000000338", "verificationStatus": "verified"},
    "基準法": {"lawTitle": "建築基準法", "lawId": "325AC0000000201", "verificationStatus": "verified"},
    "建築士法施行規則": {"lawTitle": "建築士法施行規則", "lawId": "325M50004000038", "verificationStatus": "verified"},
    "建築士法施行令": {"lawTitle": "建築士法施行令", "lawId": "325CO0000000201", "verificationStatus": "verified"},
    "士法施行規則": {"lawTitle": "建築士法施行規則", "lawId": "325M50004000038", "verificationStatus": "verified"},
    "士法施行令": {"lawTitle": "建築士法施行令", "lawId": "325CO0000000201", "verificationStatus": "verified"},
    "士法施工規則": {"lawTitle": "建築士法施行規則", "lawId": "325M50004000038", "verificationStatus": "verified"},
    "建築士法": {"lawTitle": "建築士法", "lawId": "325AC1000000202", "verificationStatus": "verified"},
    "士法": {"lawTitle": "建築士法", "lawId": "325AC1000000202", "verificationStatus": "verified"},
    "長期優良住宅の普及の促進に関する法律": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律",
        "lawId": "420AC0000000087",
        "verificationStatus": "verified",
    },
    "長期優良住宅促進法": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律",
        "lawId": "420AC0000000087",
        "verificationStatus": "verified",
    },
    "長期優良住宅法": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律",
        "lawId": "420AC0000000087",
        "verificationStatus": "verified",
    },
    "長期優良住宅の普及の促進に関する法律施行令": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律施行令",
        "lawId": "421CO0000000024",
        "verificationStatus": "verified",
    },
    "長期優良住宅の普及の促進に関する法律施行規則": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律施行規則",
        "lawId": "421M60000800003",
        "verificationStatus": "verified",
    },
    "長期優良住宅促進法施行規則": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律施行規則",
        "lawId": "421M60000800003",
        "verificationStatus": "verified",
    },
    "長期優良住宅法施行規則": {
        "lawTitle": "長期優良住宅の普及の促進に関する法律施行規則",
        "lawId": "421M60000800003",
        "verificationStatus": "verified",
    },
    "都市計画法": {"lawTitle": "都市計画法", "lawId": "343AC0000000100", "verificationStatus": "verified"},
    "都市法": {"lawTitle": "都市計画法", "lawId": "343AC0000000100", "verificationStatus": "verified"},
    "都市計画法施行令": {"lawTitle": "都市計画法施行令", "lawId": "344CO0000000158", "verificationStatus": "verified"},
    "都市法令": {"lawTitle": "都市計画法施行令", "lawId": "344CO0000000158", "verificationStatus": "verified"},
    "建設業法": {"lawTitle": "建設業法", "lawId": "324AC0000000100", "verificationStatus": "verified"},
    "建設業法施行令": {"lawTitle": "建設業法施行令", "lawId": "331CO0000000273", "verificationStatus": "verified"},
    "下水道法": {"lawTitle": "下水道法", "lawId": "333AC0000000079", "verificationStatus": "verified"},
    "消防法": {"lawTitle": "消防法", "lawId": "323AC1000000186", "verificationStatus": "verified"},
    "民法": {"lawTitle": "民法", "lawId": "129AC0000000089", "verificationStatus": "verified"},
    "土地区画整理法": {"lawTitle": "土地区画整理法", "lawId": "329AC0000000119", "verificationStatus": "verified"},
    "建設工事に係る資材の再資源化等に関する法律": {
        "lawTitle": "建設工事に係る資材の再資源化等に関する法律",
        "lawId": "412AC0000000104",
        "verificationStatus": "verified",
    },
    "建設リサイクル法": {
        "lawTitle": "建設工事に係る資材の再資源化等に関する法律",
        "lawId": "412AC0000000104",
        "verificationStatus": "verified",
    },
    "建設工事に係る資材の再資源化等に関する法律施行令": {
        "lawTitle": "建設工事に係る資材の再資源化等に関する法律施行令",
        "lawId": "412CO0000000495",
        "verificationStatus": "verified",
    },
    "住宅の品質確保の促進等に関する法律": {
        "lawTitle": "住宅の品質確保の促進等に関する法律",
        "lawId": "411AC0000000081",
        "verificationStatus": "verified",
    },
    "品確法": {
        "lawTitle": "住宅の品質確保の促進等に関する法律",
        "lawId": "411AC0000000081",
        "verificationStatus": "verified",
    },
    "住宅品質確保法": {
        "lawTitle": "住宅の品質確保の促進等に関する法律",
        "lawId": "411AC0000000081",
        "verificationStatus": "verified",
    },
    "特定住宅瑕疵担保責任の履行の確保等に関する法律": {
        "lawTitle": "特定住宅瑕疵担保責任の履行の確保等に関する法律",
        "lawId": "419AC0000000066",
        "verificationStatus": "verified",
    },
    "瑕疵担保法": {
        "lawTitle": "特定住宅瑕疵担保責任の履行の確保等に関する法律",
        "lawId": "419AC0000000066",
        "verificationStatus": "verified",
    },
    "瑕疵担履行法": {
        "lawTitle": "特定住宅瑕疵担保責任の履行の確保等に関する法律",
        "lawId": "419AC0000000066",
        "verificationStatus": "verified",
    },
    "建築物の耐震改修の促進に関する法律": {
        "lawTitle": "建築物の耐震改修の促進に関する法律",
        "lawId": "407AC0000000123",
        "verificationStatus": "verified",
    },
    "耐震改修促進法": {
        "lawTitle": "建築物の耐震改修の促進に関する法律",
        "lawId": "407AC0000000123",
        "verificationStatus": "verified",
    },
    "都市の低炭素化の促進に関する法律": {
        "lawTitle": "都市の低炭素化の促進に関する法律",
        "lawId": "424AC0000000084",
        "verificationStatus": "verified",
    },
    "高齢者、障害者等の移動等の円滑化の促進に関する法律": {
        "lawTitle": "高齢者、障害者等の移動等の円滑化の促進に関する法律",
        "lawId": "418AC0000000091",
        "verificationStatus": "verified",
    },
    "高齢者、障害者等の移動等の円滑化の促進に関する法律施行令": {
        "lawTitle": "高齢者、障害者等の移動等の円滑化の促進に関する法律施行令",
        "lawId": "418CO0000000379",
        "verificationStatus": "verified",
    },
    "バリアフリー法": {
        "lawTitle": "高齢者、障害者等の移動等の円滑化の促進に関する法律",
        "lawId": "418AC0000000091",
        "verificationStatus": "verified",
    },
    "宅地造成及び特定盛土等規制法": {
        "lawTitle": "宅地造成及び特定盛土等規制法",
        "lawId": "336AC0000000191",
        "verificationStatus": "verified",
    },
    "宅地造成等規制法": {
        "lawTitle": "宅地造成及び特定盛土等規制法",
        "lawId": "336AC0000000191",
        "verificationStatus": "verified",
    },
    "宅地造成及び特定盛土等規制法施行令": {
        "lawTitle": "宅地造成及び特定盛土等規制法施行令",
        "lawId": "337CO0000000016",
        "verificationStatus": "verified",
    },
    "宅地造成等規制法施行令": {
        "lawTitle": "宅地造成及び特定盛土等規制法施行令",
        "lawId": "337CO0000000016",
        "verificationStatus": "verified",
    },
    "宅造法令": {
        "lawTitle": "宅地造成及び特定盛土等規制法施行令",
        "lawId": "337CO0000000016",
        "verificationStatus": "verified",
    },
    "宅地造成及び特定盛土等規制法施行規則": {
        "lawTitle": "宅地造成及び特定盛土等規制法施行規則",
        "lawId": "337M50004000003",
        "verificationStatus": "verified",
    },
    "告示": {"lawTitle": "国土交通大臣告示", "verificationStatus": "candidate"},
    "規": {"lawTitle": "建築基準法施行規則", "lawId": "325M50004000040", "verificationStatus": "candidate"},
    "規則": {"lawTitle": "建築基準法施行規則", "lawId": "325M50004000040", "verificationStatus": "candidate"},
    "施行規則": {"lawTitle": "建築基準法施行規則", "lawId": "325M50004000040", "verificationStatus": "candidate"},
    "施行令": {"lawTitle": "建築基準法施行令", "lawId": "325CO0000000338", "verificationStatus": "verified"},
    "令": {"lawTitle": "建築基準法施行令", "lawId": "325CO0000000338", "verificationStatus": "verified"},
    "法": {"lawTitle": "建築基準法", "lawId": "325AC0000000201", "verificationStatus": "verified"},
}

ALIASES = sorted(LAW_METADATA.keys(), key=len, reverse=True)


@dataclass
class ReferenceContext:
    law_alias: str | None = None
    article: str | None = None
    default_law_alias: str = "建築基準法"
    default_order_alias: str = "建築基準法施行令"
    default_rule_alias: str = "建築基準法施行規則"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def source_files(root: Path) -> list[Path]:
    return sorted(root.glob(f"*/{SOURCE_SUBDIR}/question_*.json"))


def is_target_question(question: dict[str, Any]) -> bool:
    return TARGET_CATEGORY_KEYWORD in str(question.get("category") or "")


def normalize_text(value: Any) -> str:
    text = str(value or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    text = text.replace("\u3000", " ")
    return text.strip()


def iter_snippet_variants(snippet_entry: Any) -> list[str]:
    if isinstance(snippet_entry, list):
        return [normalize_text(item) for item in snippet_entry if normalize_text(item)]
    value = normalize_text(snippet_entry)
    return [value] if value else []


def score_snippet(text: str) -> int:
    score = 0
    if "該当条文" in text:
        score += 5
    if REFERENCE_TRIGGER_RE.search(text):
        score += 4
    if "よって" in text:
        score += 2
    if "誤り" in text or "正しい" in text or "不適合" in text:
        score += 2
    score += min(len(text), 320) // 80
    return score


def choose_best_snippet(snippet_entry: Any) -> str:
    variants = iter_snippet_variants(snippet_entry)
    if not variants:
        return ""
    return max(variants, key=score_snippet)


def clean_explanation_text(text: str) -> str:
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        line = CHOICE_PREFIX_RE.sub("", line)
        line = line.lstrip("〇○× ")
        lines.append(line)
    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_wrong_phrase(choice_text: str, explanation_text: str) -> str | None:
    quoted = re.findall(r"「([^」]+)」", explanation_text)
    if quoted:
        return quoted[0]
    if "誤り" in explanation_text or "不適合" in explanation_text:
        return choice_text[:40] if choice_text else None
    return None


def split_reference_segments(text: str) -> list[str]:
    segments: list[str] = []
    normalized = normalize_text(text)
    if not normalized:
        return segments
    for line in normalized.split("。"):
        line = line.strip()
        if not line:
            continue
        if REFERENCE_TRIGGER_RE.search(line):
            segments.append(line)
        for paren in PAREN_REFERENCE_RE.findall(line):
            candidate = normalize_text(paren)
            if REFERENCE_TRIGGER_RE.search(candidate):
                segments.append(candidate)
    return segments


def normalize_reference_token(token: str) -> str:
    token = normalize_text(token)
    token = re.sub(r"([0-9]+)の([0-9]+)条", r"\1条の\2", token)
    token = re.sub(r"([0-9]+)条([0-9]+)第([0-9]+項)", r"\1条の\2第\3", token)
    token = token.replace("該当条文は", "")
    token = token.replace("により", "")
    token = token.replace("になります", "")
    token = token.replace("に記載されています", "")
    token = token.replace("と記載されています", "")
    token = token.replace("に関連します", "")
    token = token.replace("を確認します", "")
    token = token.replace("に該当します", "")
    token = token.replace("上、", "")
    token = token.replace("上", "")
    token = token.strip("()（） ")
    return token


def repair_compact_article_suffix(
    article: str,
    paragraph: str | None,
    item: str | None,
) -> tuple[str, str | None, str | None]:
    suffix_match = re.search(r"条の([0-9]{2,})$", article)
    if suffix_match and paragraph:
        suffix = suffix_match.group(1)
        compact_paragraph = paragraph.removesuffix("項")
        if compact_paragraph == suffix:
            article = article[: -len(suffix)] + suffix[:-1]
            paragraph = f"{suffix[-1]}項"
    if suffix_match and item:
        suffix = suffix_match.group(1)
        compact_item = item.removesuffix("号")
        if compact_item == suffix:
            article = article[: -len(suffix)] + suffix[:-1]
            item = f"{suffix[-1]}号"
    return article, paragraph, item


def find_alias(token: str, inherited_alias: str | None) -> str | None:
    for alias in ALIASES:
        if alias == "令" and not re.search(r"(?<!政)令", token):
            continue
        if alias in {"法", "規"} and not re.search(rf"(^|[「（(\s]){alias}(?=第?[0-9])", token):
            continue
        if alias in token:
            return alias
    return inherited_alias


def build_reference_context(question: dict[str, Any], choice_index: int, snippet_entry: Any) -> ReferenceContext:
    choices = question.get("choiceTextList") or []
    text_parts = [normalize_text(question.get("questionBodyText"))]
    if choice_index < len(choices):
        text_parts.append(normalize_text(choices[choice_index]))
    text_parts.extend(iter_snippet_variants(snippet_entry))
    merged_text = " ".join(part for part in text_parts if part)

    context = ReferenceContext()
    if any(keyword in merged_text for keyword in ("建築士法", "士法", "建築士事務所", "建築士定期講習")):
        context.default_law_alias = "士法"
        context.default_rule_alias = "建築士法施行規則"
    if any(keyword in merged_text for keyword in ("長期優良住宅", "長期優良住宅促進法", "長期優良住宅法")):
        context.default_law_alias = "長期優良住宅の普及の促進に関する法律"
        context.default_order_alias = "長期優良住宅の普及の促進に関する法律施行令"
        context.default_rule_alias = "長期優良住宅の普及の促進に関する法律施行規則"
    if any(keyword in merged_text for keyword in ("宅地造成等規制法", "宅地造成及び特定盛土等規制法", "宅造法")):
        context.default_law_alias = "宅地造成等規制法"
        context.default_order_alias = "宅地造成等規制法施行令"
        context.default_rule_alias = "宅地造成及び特定盛土等規制法施行規則"
    if any(keyword in merged_text for keyword in ("都市計画法", "都市法")):
        context.default_law_alias = "都市計画法"
        context.default_order_alias = "都市計画法施行令"
    if any(keyword in merged_text for keyword in ("バリアフリー法", "高齢者、障害者等の移動等の円滑化の促進に関する法律")):
        context.default_law_alias = "高齢者、障害者等の移動等の円滑化の促進に関する法律"
        context.default_order_alias = "高齢者、障害者等の移動等の円滑化の促進に関する法律施行令"
    if any(keyword in merged_text for keyword in ("建築物の耐震改修の促進に関する法律", "耐震改修促進法")):
        context.default_law_alias = "建築物の耐震改修の促進に関する法律"
    if any(keyword in merged_text for keyword in ("住宅の品質確保の促進等に関する法律", "品確法", "住宅品質確保法")):
        context.default_law_alias = "住宅の品質確保の促進等に関する法律"
    if any(keyword in merged_text for keyword in ("特定住宅瑕疵担保責任", "瑕疵担保法", "瑕疵担履行法")):
        context.default_law_alias = "特定住宅瑕疵担保責任の履行の確保等に関する法律"
    if any(keyword in merged_text for keyword in ("建設工事に係る資材の再資源化等", "建設リサイクル法")):
        context.default_law_alias = "建設工事に係る資材の再資源化等に関する法律"
    if any(keyword in merged_text for keyword in ("都市の低炭素化の促進に関する法律",)):
        context.default_law_alias = "都市の低炭素化の促進に関する法律"
    return context


def resolve_alias(alias: str | None, token: str, context: ReferenceContext) -> str | None:
    if alias is None:
        return None
    if alias == "法":
        return context.default_law_alias
    if alias in {"令", "施行令"}:
        return context.default_order_alias
    if alias in {"規", "規則", "施行規則"}:
        return context.default_rule_alias
    if alias == "宅地造成等規制法" and "施行令" in token:
        return "宅地造成等規制法施行令"
    return alias


def parse_reference_token(
    token: str,
    *,
    choice_index: int,
    context: ReferenceContext,
) -> dict[str, str | int] | None:
    token = normalize_reference_token(token)
    if not token:
        return None

    explicit_alias = find_alias(token, None)
    alias = resolve_alias(explicit_alias or context.law_alias, token, context)
    article_match = ARTICLE_RE.search(token)
    paragraph_match = PARAGRAPH_RE.search(token)
    item_match = ITEM_RE.search(token)

    if article_match:
        article = article_match.group(1)
    elif explicit_alias is None and (paragraph_match or item_match):
        article = context.article
    elif explicit_alias is not None and token.startswith(("同法", "同令", "同規則", "同施行規則", "同施行令")):
        article = context.article
    else:
        article = None
    paragraph = paragraph_match.group(1) if paragraph_match else None
    item = item_match.group(1) if item_match else None
    if article:
        article, paragraph, item = repair_compact_article_suffix(article, paragraph, item)

    if alias is None or article is None:
        return None
    if article_match is None and paragraph is None and item is None:
        return None

    metadata = LAW_METADATA.get(alias, {"lawTitle": alias, "verificationStatus": "candidate"})
    context.law_alias = alias
    context.article = article

    reference = {
        "role": "current_basis",
        "scope": "choice",
        "choiceIndex": choice_index,
        "lawTitle": metadata["lawTitle"],
        "lawAlias": alias,
        "referenceDate": TODAY,
        "verificationStatus": metadata.get("verificationStatus", "candidate"),
        "reason": token,
        "article": article,
    }
    if metadata.get("lawId"):
        reference["lawId"] = metadata["lawId"]
    if paragraph:
        reference["paragraph"] = paragraph
    if item:
        reference["item"] = item
    return reference


def parse_law_references(
    choice_index: int,
    question: dict[str, Any],
    snippet_entry: Any,
) -> list[dict[str, str | int]]:
    variants = iter_snippet_variants(snippet_entry)
    context = build_reference_context(question, choice_index, snippet_entry)
    refs: list[dict[str, str | int]] = []
    for variant in variants:
        for segment in split_reference_segments(variant):
            for raw_token in SEPARATOR_RE.split(segment):
                ref = parse_reference_token(
                    raw_token,
                    choice_index=choice_index,
                    context=context,
                )
                if ref is not None:
                    refs.append(ref)

    deduped: list[dict[str, str | int]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref.get("lawTitle"),
            ref.get("article"),
            ref.get("paragraph"),
            ref.get("item"),
            ref.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def reference_to_display(reference: dict[str, Any]) -> str:
    title = str(reference.get("lawTitle") or reference.get("lawAlias") or "").strip()
    article = str(reference.get("article") or "").strip()
    paragraph = str(reference.get("paragraph") or "").strip()
    item = str(reference.get("item") or "").strip()
    parts = [title]
    if article:
        parts.append(f"第{article}")
    if paragraph:
        parts.append(f"第{paragraph}")
    if item:
        parts.append(item)
    return "".join(parts)


def unique_reference_displays(law_references: list[list[dict[str, Any]]]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for choice_refs in law_references:
        for reference in choice_refs:
            display = reference_to_display(reference)
            if display:
                ordered.setdefault(display, None)
    return list(ordered.keys())


def build_choice_explanation(question: dict[str, Any], choice_index: int) -> str:
    snippets = question.get("explanation_choice_snippets") or []
    choice_texts = question.get("choiceTextList") or []
    selected = choose_best_snippet(snippets[choice_index] if choice_index < len(snippets) else [])
    cleaned = clean_explanation_text(selected)
    if cleaned:
        return cleaned

    choice_text = str(choice_texts[choice_index] if choice_index < len(choice_texts) else "")
    labels = question.get("correctChoiceText") or []
    label = str(labels[choice_index]) if choice_index < len(labels) else ""
    if label == "正しい":
        return f"この記述は正しいです。{choice_text}"
    if label == "間違い":
        return f"この記述は誤りです。{choice_text}"
    return choice_text


def classify_question_theme(question_body_text: str) -> str:
    text = question_body_text or ""
    if "用語" in text or "定義" in text:
        return "definition"
    if any(keyword in text for keyword in ("確認済証", "検査済証", "届", "許可", "申請", "手続")):
        return "procedure"
    if any(keyword in text for keyword in ("防火", "避難", "排煙", "内装", "準防火", "耐火")):
        return "fire"
    if any(keyword in text for keyword in ("容積率", "建蔽率", "日影", "斜線", "高さ制限")):
        return "planning"
    if any(keyword in text for keyword in ("建築士", "建築士事務所", "士法")):
        return "architect"
    if any(keyword in text for keyword in ("鉄筋", "コンクリート", "筋かい", "構造", "耐力壁")):
        return "structure"
    return "generic"


def build_suggested_questions(
    question: dict[str, Any],
    law_references: list[list[dict[str, Any]]],
    explanations: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    theme = classify_question_theme(str(question.get("questionBodyText") or ""))
    refs_text = "、".join(unique_reference_displays(law_references)[:4]) or "関連条文"
    choice_texts = [str(choice or "") for choice in (question.get("choiceTextList") or [])]
    wrong_terms = []
    labels = question.get("correctChoiceText") or []
    for idx, label in enumerate(labels):
        if label == "間違い" and idx < len(choice_texts):
            wrong = extract_wrong_phrase(choice_texts[idx], explanations[idx] if idx < len(explanations) else "")
            if wrong:
                wrong_terms.append(wrong)
    wrong_terms_text = "、".join(wrong_terms[:3]) or "主語・数値・例外条件"

    if theme == "definition":
        questions = [
            "この用語はどこで切って覚える？",
            "ひっかけになりやすい語句はどこ？",
            "現行法では何を正本に覚える？",
        ]
        answers = [
            f"定義問題は、対象範囲と除外条件を一緒に押さえると崩れません。今回の起点は {refs_text} です。",
            f"ひっかけは {wrong_terms_text} の差し替えです。似た用語でも要件が一語違うと結論が変わります。",
            f"まず {refs_text} を正本にして覚えるのが安全です。過去問の言い回しは補助に回してください。",
        ]
    elif theme == "procedure":
        questions = [
            "確認・届出・許可はどう切り分ける？",
            "この問題の例外条件はどこ？",
            "現行法ではどう整理すると速い？",
        ]
        answers = [
            f"まず行為の種類を確認して、確認・届出・許可のどれが必要かを分けます。今回の判断の起点は {refs_text} です。",
            f"例外条件は {wrong_terms_text} のような主語や面積条件に入っています。原則と例外を一文で対にして覚えると安定します。",
            f"現行法では、主体・対象行為・例外条件の順で整理すると速く判定できます。根拠は {refs_text} を追えば十分です。",
        ]
    elif theme == "fire":
        questions = [
            "防火・避難の論点は何を先に見る？",
            "数値や対象部分のひっかけはどこ？",
            "現行法ではどの条文から確認する？",
        ]
        answers = [
            f"防火・避難では、対象用途、対象部分、必要性能の3点を先に見ます。今回の起点は {refs_text} です。",
            f"ひっかけは {wrong_terms_text} のような対象部分や面積条件です。外壁・軒裏・廊下・竪穴などの対象を混ぜないことが重要です。",
            f"まず {refs_text} を見て、対象用途と適用除外を確認してください。現行法ベースで覚えると他の法規問題にも再利用できます。",
        ]
    elif theme == "planning":
        questions = [
            "面積や高さの計算は何から確認する？",
            "この問題の除外規定はどこ？",
            "現行法ではどう覚えると迷いにくい？",
        ]
        answers = [
            f"計画規制は、用途地域・防火地域・面積条件の順で整理すると判断しやすいです。今回の起点は {refs_text} です。",
            f"除外規定は {wrong_terms_text} のような区域や建築物種別の条件に入っています。加算・適用除外の順を固定すると崩れません。",
            f"現行法では、まず主条文、その後に政令や別表の条件を見る流れが安定です。{refs_text} を軸に確認してください。",
        ]
    elif theme == "architect":
        questions = [
            "建築士法の論点は何を分けて覚える？",
            "罰則・届出・講習の違いは？",
            "現行法ではどこを起点に見る？",
        ]
        answers = [
            f"建築士法は、資格範囲、事務所登録、講習、罰則を分けて覚えると整理しやすいです。今回の起点は {refs_text} です。",
            f"罰則・届出・講習は主体と期限を取り違えやすいです。{wrong_terms_text} の差し替えがないかを先に見てください。",
            f"現行法では、まず建築士法、その後に施行規則の細目を見る流れが安全です。今回なら {refs_text} が入口です。",
        ]
    elif theme == "structure":
        questions = [
            "構造法規は何を先に押さえる？",
            "数値基準のひっかけはどこ？",
            "現行法ではどの条文を起点に見る？",
        ]
        answers = [
            f"構造法規は、対象構造、適用条件、数値基準の順で整理します。今回の起点は {refs_text} です。",
            f"ひっかけは {wrong_terms_text} のような数値や対象部位の差です。材料名と寸法を一緒に覚えると精度が上がります。",
            f"現行法では、建築基準法施行令の条文を正本にして、例外やただし書を見落とさないのが重要です。{refs_text} を軸に見てください。",
        ]
    else:
        questions = [
            "この問題はどの条文から確認する？",
            "ひっかけになっている条件はどこ？",
            "現行法ではどう整理すると良い？",
        ]
        answers = [
            f"まず {refs_text} を見て、主語と要件を切り分けるのが基本です。",
            f"ひっかけは {wrong_terms_text} のような例外条件や数値の差です。設問文と条文のズレを一つずつ確認してください。",
            f"現行法を正本にして、過去問の言い回しは補助として扱うのが安全です。今回も {refs_text} から確認できます。",
        ]

    details = [
        {"question": question_text, "answer": answer_text}
        for question_text, answer_text in zip(questions, answers)
    ]
    return questions, details


def build_patch_entry(question: dict[str, Any]) -> dict[str, Any]:
    choice_count = len(question.get("choiceTextList") or [])
    explanations = [build_choice_explanation(question, idx) for idx in range(choice_count)]
    law_references = [
        parse_law_references(
            idx,
            question,
            (question.get("explanation_choice_snippets") or [])[idx]
            if idx < len(question.get("explanation_choice_snippets") or [])
            else [],
        )
        for idx in range(choice_count)
    ]
    suggested_questions, suggested_details = build_suggested_questions(question, law_references, explanations)
    return {
        "original_question_id": question["original_question_id"],
        "question_url": question["question_url"],
        "explanationText": explanations,
        "suggestedQuestions": suggested_questions,
        "suggestedQuestionDetails": suggested_details,
        "lawReferences": law_references,
    }


def collect_patch_entries_for_list_group(list_group_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source_path in sorted((list_group_dir / SOURCE_SUBDIR).glob("question_*.json")):
        payload = load_json(source_path)
        question_bodies = payload.get("question_bodies")
        if not isinstance(question_bodies, list):
            raise ValueError(f"question_bodies missing: {source_path}")
        for question in question_bodies:
            if not isinstance(question, dict) or not is_target_question(question):
                continue
            entries.append(build_patch_entry(question))
    return entries


def build_patch_for_list_group(list_group_dir: Path) -> tuple[Path | None, int]:
    entries = collect_patch_entries_for_list_group(list_group_dir)
    if not entries:
        return None, 0
    list_group_id = list_group_dir.name
    output_path = (
        list_group_dir
        / PATCH_SUBDIR
        / f"question_{list_group_id}_law_merged_explanationText_added_{TIMESTAMP}.json"
    )
    dump_json(output_path, entries)
    return output_path, len(entries)


def main() -> int:
    generated_files = []
    total_entries = 0
    for list_group_dir in sorted(path for path in ROOT_DIR.iterdir() if path.is_dir() and path.name.isdigit()):
        if not (list_group_dir / SOURCE_SUBDIR).exists():
            continue
        output_path, count = build_patch_for_list_group(list_group_dir)
        if count == 0:
            continue
        assert output_path is not None
        generated_files.append((output_path, count))
        total_entries += count

    print(f"generated {len(generated_files)} patch files")
    for path, count in generated_files:
        print(f"{path} ({count} entries)")
    print(f"total_entries={total_entries}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
