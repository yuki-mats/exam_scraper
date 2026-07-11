#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_DIR = (
    ROOT_DIR / "output" / "gas-shunin-all" / "review" / "manual_law_explanation_audit"
)
DEFAULT_REPORT = (
    ROOT_DIR
    / "output"
    / "gas-shunin-all"
    / "review"
    / "manual_law_explanation_audit"
    / "gas_shunin_law_explanation_finalize_report.json"
)

TF_LABELS = {"正しい", "間違い"}
METI_NOTICE_URL = (
    "https://www.meti.go.jp/policy/safety_security/industrial_safety/law/files/gikokuji.pdf"
)


@dataclass(frozen=True)
class LawSpec:
    title: str
    law_id: str | None
    source: str = "egov_law"
    source_url: str | None = None


LAW_SPECS: dict[str, LawSpec] = {
    "ガス事業法": LawSpec("ガス事業法", "329AC0000000051"),
    "ガス事業法施行令": LawSpec("ガス事業法施行令", "329CO0000000068"),
    "ガス事業法施行規則": LawSpec("ガス事業法施行規則", "345M50000400097"),
    "ガス工作物の技術上の基準を定める省令": LawSpec(
        "ガス工作物の技術上の基準を定める省令", "412M50000400111"
    ),
    "ガス工作物の技術上の基準の細目を定める告示": LawSpec(
        "ガス工作物の技術上の基準の細目を定める告示",
        None,
        source="meti_official_pdf",
        source_url=METI_NOTICE_URL,
    ),
    "ガス関係報告規則": LawSpec("ガス関係報告規則", "429M60000400016"),
    "ガス用品の技術上の基準等に関する省令": LawSpec(
        "ガス用品の技術上の基準等に関する省令", "346M50000400027"
    ),
    "特定ガス消費機器の設置工事の監督に関する法律": LawSpec(
        "特定ガス消費機器の設置工事の監督に関する法律", "354AC0000000033"
    ),
    "特定ガス消費機器の設置工事の監督に関する法律施行令": LawSpec(
        "特定ガス消費機器の設置工事の監督に関する法律施行令", "354CO0000000231"
    ),
    "特定ガス消費機器の設置工事の監督に関する法律施行規則": LawSpec(
        "特定ガス消費機器の設置工事の監督に関する法律施行規則",
        "354M50000400077",
    ),
    "高圧ガス保安法": LawSpec("高圧ガス保安法", "326AC0000000204"),
}

ACT_RULES = {
    "ガス事業法": "ガス事業法施行規則",
    "特定ガス消費機器の設置工事の監督に関する法律": (
        "特定ガス消費機器の設置工事の監督に関する法律施行規則"
    ),
}

TITLE_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        {
            **{title: title for title in LAW_SPECS},
            "技術基準省令": "ガス工作物の技術上の基準を定める省令",
            "技省令": "ガス工作物の技術上の基準を定める省令",
            "技術基準細目告示": "ガス工作物の技術上の基準の細目を定める告示",
            "技告示": "ガス工作物の技術上の基準の細目を定める告示",
            "報告規則": "ガス関係報告規則",
            "ガス事故報告規則": "ガス関係報告規則",
            "特監法施行規則": "特定ガス消費機器の設置工事の監督に関する法律施行規則",
            "特監法施行令": "特定ガス消費機器の設置工事の監督に関する法律施行令",
            "特監法": "特定ガス消費機器の設置工事の監督に関する法律",
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

BASIS_TITLE_MARKERS = tuple(
    sorted(
        {
            *(alias for alias, _ in TITLE_ALIASES),
            "同法施行規則",
            "同施行規則",
            "同法",
            "同規則",
            "同省令",
            "同令",
            "施行規則",
        },
        key=len,
        reverse=True,
    )
)
BASIS_TITLE_MARKER_RE = re.compile("|".join(re.escape(marker) for marker in BASIS_TITLE_MARKERS))

ARTICLE_RE = re.compile(r"第(?P<article>\d+)条(?:の(?P<branch>\d+))?")
PARAGRAPH_RE = re.compile(r"第(?P<paragraph>\d+)項")
ITEM_RE = re.compile(r"第(?P<item>\d+|[一二三四五六七八九十百]+)号")
VERDICT_PREFIX_RE = re.compile(
    r"^(?:正しい|間違い|正解|不正解|この記述は正しいです|この記述は間違いです)[。\s]*"
)


@dataclass
class BasisContext:
    last_title: str | None = None
    last_act: str | None = None
    last_rule: str | None = None
    last_order: str | None = None
    last_decree: str | None = None
    last_ref: dict[str, Any] | None = None

    def remember(self, ref: dict[str, Any]) -> None:
        title = str(ref.get("lawTitle") or "").strip()
        if not title:
            return
        self.last_title = title
        if title.endswith(("法", "法律")):
            self.last_act = title
        if title.endswith("規則"):
            self.last_rule = title
        if title.endswith("省令"):
            self.last_order = title
        if title.endswith("施行令"):
            self.last_decree = title
        self.last_ref = ref


@dataclass
class FinalizeStats:
    review_records: int = 0
    matched_questions: int = 0
    updated_questions: int = 0
    updated_choices: int = 0
    changed_files: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\\n", "\n").replace("\\r", "\r")
    return re.sub(r"\s+", "", text).strip()


def normalize_label(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"正しい", "正解"}:
        return "正しい"
    if text in {"間違い", "不正解", "誤り"}:
        return "間違い"
    return text


def ensure_sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    return text if text.endswith("。") else text + "。"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_review_records(review_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(review_dir.glob("*.jsonl")):
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            records.append(parsed)
            continue
        if isinstance(parsed, list):
            if not all(isinstance(item, dict) for item in parsed):
                raise ValueError(f"review array must contain objects: {path}")
            records.extend(parsed)
            continue
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"review must be object: {path}:{line_number}")
            records.append(record)
    return records


def resolve_patch_path(patch_value: str) -> Path:
    path = (ROOT_DIR / patch_value).resolve()
    if path.is_file():
        return path
    if path.suffix == ".json" and not path.stem.endswith("_explanationText_added"):
        candidate = path.with_name(path.stem + "_explanationText_added.json")
        if candidate.is_file():
            return candidate
    parent = path.parent
    if parent.is_dir():
        stem = path.stem.replace("_explanationText_added", "")
        candidates = sorted(parent.glob(stem + "*_explanationText_added.json"))
        if len(candidates) == 1:
            return candidates[0]
    return path


def find_patch_entry(payload: list[Any], review: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    body = normalize_text(review.get("sourceQuestionBodyText"))
    labels = review.get("sourceCorrectChoiceText")
    expected_count = len(labels) if isinstance(labels, list) else -1
    source_key = str(review.get("sourceQuestionKey") or "")
    key_match = re.fullmatch(r"gas-shunin:(kou|otsu):(\d{4}):law:q0*(\d+)", source_key)
    expected_source_id = None
    if key_match:
        grade = "koushu" if key_match.group(1) == "kou" else "otsu"
        expected_source_id = f"gasushunin-{grade}-hourei-{key_match.group(2)}-{int(key_match.group(3))}"
    question_label = normalize_text(review.get("questionLabel"))

    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            continue
        choices = entry.get("choiceTextList")
        if not isinstance(choices, list) or len(choices) != expected_count:
            continue
        candidates.append((index, entry))

    matches = candidates
    if body:
        body_matches = [pair for pair in matches if normalize_text(pair[1].get("questionBodyText")) == body]
        if body_matches:
            matches = body_matches
    if expected_source_id:
        id_matches = [
            pair
            for pair in matches
            if normalize_text(pair[1].get("source_original_question_id")) == normalize_text(expected_source_id)
        ]
        if id_matches:
            matches = id_matches
    if question_label:
        label_matches = [
            pair
            for pair in matches
            if normalize_text(pair[1].get("questionLabel")) == question_label
        ]
        if label_matches:
            matches = label_matches
    law_matches = [
        pair
        for pair in matches
        if "法令" in str(pair[1].get("examLabel") or "")
        or ":law:" in str(pair[1].get("sourceQuestionKey") or "")
    ]
    if law_matches:
        matches = law_matches
    if len(matches) != 1:
        raise ValueError(
            f"patch entry match count={len(matches)}: {review.get('sourceQuestionKey')} "
            f"body={str(review.get('sourceQuestionBodyText') or '')[:80]}"
        )
    return matches[0]


def explicit_title(raw: str) -> tuple[str | None, str]:
    for alias, title in TITLE_ALIASES:
        if raw.startswith(alias):
            return title, raw[len(alias) :]
    return None, raw


def relative_title(raw: str, context: BasisContext) -> tuple[str | None, str]:
    if raw.startswith("同法施行規則"):
        return ACT_RULES.get(context.last_act or ""), raw[len("同法施行規則") :]
    if raw.startswith("同施行規則"):
        return context.last_rule or ACT_RULES.get(context.last_act or ""), raw[len("同施行規則") :]
    if raw.startswith("同法"):
        return context.last_act, raw[len("同法") :]
    if raw.startswith("同規則"):
        return ACT_RULES.get(context.last_act or "") or context.last_rule, raw[len("同規則") :]
    if raw.startswith("同省令"):
        return context.last_order, raw[len("同省令") :]
    if raw.startswith("同令"):
        return context.last_decree or context.last_order or context.last_title, raw[len("同令") :]
    if raw.startswith("施行規則"):
        return ACT_RULES.get(context.last_act or "") or context.last_rule, raw[len("施行規則") :]
    if raw.startswith("第"):
        return context.last_title, raw
    return None, raw


def split_basis_clauses(raw: str) -> list[str]:
    starts: list[int] = []
    for match in BASIS_TITLE_MARKER_RE.finditer(raw):
        start = match.start()
        if start == 0 or raw[:start].rstrip().endswith(("、", "，", "；", ";", "及び", "並びに")):
            starts.append(start)
    if len(starts) <= 1:
        return [raw]

    clauses: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(raw)
        clause = raw[start:end].strip("、，；; \t")
        clause = re.sub(r"(?:及び|並びに)$", "", clause).strip("、，；; \t")
        if clause:
            clauses.append(clause)
    return clauses or [raw]


def copy_relative_ref(raw: str, context: BasisContext) -> dict[str, Any] | None:
    if not raw.startswith(("同条", "同項", "同号", "同表")):
        return None
    if context.last_ref is None:
        return None
    ref = dict(context.last_ref)
    paragraph = PARAGRAPH_RE.search(raw)
    item = ITEM_RE.search(raw)
    if paragraph:
        ref["paragraph"] = paragraph.group("paragraph")
    if item:
        ref["item"] = item.group("item")
    ref["display"] = display_reference(
        str(ref.get("lawTitle") or ""),
        str(ref.get("article") or ""),
        ref.get("paragraph"),
        ref.get("item"),
    )
    if "ただし書" in raw:
        ref["display"] += "ただし書"
    elif "の表" in raw or raw.startswith("同表"):
        ref["display"] += "の表"
    return ref


def refs_from_existing(existing_refs: Iterable[Any], title: str | None = None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for value in existing_refs:
        if not isinstance(value, dict):
            continue
        law_title = str(value.get("lawTitle") or "").strip()
        article = str(value.get("article") or "").strip()
        if not law_title or not article:
            continue
        if title and law_title != title:
            continue
        spec = LAW_SPECS.get(law_title)
        if spec is None:
            continue
        refs.append(
            {
                "lawTitle": law_title,
                "lawId": spec.law_id,
                "article": article,
                "paragraph": str(value.get("paragraph") or "").strip() or None,
                "item": str(value.get("item") or "").strip() or None,
                "display": display_reference(law_title, article, value.get("paragraph"), value.get("item")),
                "source": spec.source,
                "sourceUrl": spec.source_url,
            }
        )
    return refs


def special_basis_refs(raw: str, context: BasisContext) -> list[dict[str, Any]]:
    if raw in {
        "ガス事業法の小売登録・導管許可・供給能力・最終保障供給規定",
        "同法の小売登録・導管許可・供給能力・最終保障供給規定",
    }:
        return [
            make_resolved_ref("ガス事業法", article)
            for article in ("3", "13", "35", "51")
        ]
    return []


def make_resolved_ref(
    title: str,
    article: str,
    paragraph: str | None = None,
    item: str | None = None,
    suffix: str | None = None,
) -> dict[str, Any]:
    spec = LAW_SPECS[title]
    display = display_reference(title, article, paragraph, item)
    if suffix:
        display += suffix
    return {
        "lawTitle": title,
        "lawId": spec.law_id,
        "article": article,
        "paragraph": paragraph,
        "item": item,
        "display": display,
        "source": spec.source,
        "sourceUrl": spec.source_url,
    }


def display_reference(title: str, article: str, paragraph: Any = None, item: Any = None) -> str:
    value = f"{title}第{article}条"
    if paragraph not in (None, ""):
        value += f"第{paragraph}項"
    if item not in (None, ""):
        item_text = str(item)
        value += f"第{item_text}号" if item_text.isdigit() else f"{item_text}号"
    return value


def resolve_basis(
    raw_value: Any,
    *,
    context: BasisContext,
    existing_refs: Iterable[Any],
) -> list[dict[str, Any]]:
    raw = str(raw_value or "").strip()
    if not raw or raw.startswith("JIA"):
        return []
    if raw == "整圧器の作動原理":
        return []
    if raw in {"整圧器の選定要件", "整圧器の故障原因"}:
        return []

    special = special_basis_refs(raw, context)
    if special:
        for ref in special:
            context.remember(ref)
        return special

    if raw == "ガス事業法に基づく保安物件区分":
        ref = make_resolved_ref(
            "ガス工作物の技術上の基準の細目を定める告示", "3"
        )
        context.remember(ref)
        return [ref]

    clauses = split_basis_clauses(raw)
    if len(clauses) > 1:
        resolved: list[dict[str, Any]] = []
        for clause in clauses:
            resolved.extend(resolve_basis(clause, context=context, existing_refs=existing_refs))
        return resolved

    relative = copy_relative_ref(raw, context)
    if relative is not None:
        context.remember(relative)
        return [relative]

    title, remainder = explicit_title(raw)
    if title is None:
        title, remainder = relative_title(raw, context)

    if title is None:
        return []

    matches = list(ARTICLE_RE.finditer(remainder))
    if not matches:
        return []

    resolved: list[dict[str, Any]] = []
    for match_index, article_match in enumerate(matches):
        segment_end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(remainder)
        segment = remainder[article_match.start() : segment_end]
        article = article_match.group("article")
        branch = article_match.group("branch")
        if branch:
            article += "の" + branch
        paragraph_match = PARAGRAPH_RE.search(segment)
        item_match = None if "別表" in segment else ITEM_RE.search(segment)
        paragraph = paragraph_match.group("paragraph") if paragraph_match else None
        item = item_match.group("item") if item_match else None
        suffix = None
        if "ただし書" in segment:
            suffix = "ただし書"
        elif "別表" in segment:
            suffix = "・" + segment[segment.index("別表") :]
        elif "の表" in segment or "表第" in segment:
            suffix = "の表"
        ref = make_resolved_ref(title, article, paragraph, item, suffix)
        resolved.append(ref)
        context.remember(ref)
    return resolved


def dedupe_refs(refs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref.get("lawTitle"),
            ref.get("article"),
            ref.get("paragraph"),
            ref.get("item"),
            ref.get("display"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def firestore_law_reference(
    ref: dict[str, Any],
    *,
    choice_index: int,
    reason: str,
    comparison_status: str,
) -> dict[str, Any]:
    title = str(ref["lawTitle"])
    spec = LAW_SPECS[title]
    article = str(ref["article"])
    result: dict[str, Any] = {
        "role": "current_basis",
        "scope": "choice",
        "choiceIndex": choice_index,
        "lawTitle": title,
        "lawAlias": title,
        "article": article,
        "referenceDate": date.today().isoformat(),
        "verificationStatus": "verified",
        "comparisonStatus": comparison_status,
        "source": spec.source,
        "reason": reason,
    }
    if ref.get("paragraph"):
        result["paragraph"] = str(ref["paragraph"])
    if ref.get("item"):
        result["item"] = str(ref["item"])
    if spec.law_id:
        result["lawId"] = spec.law_id
        result["sourceUrl"] = f"https://laws.e-gov.go.jp/law/{spec.law_id}"
        result["apiUrl"] = (
            f"https://laws.e-gov.go.jp/api/1/articles;lawId={spec.law_id};article="
            + article.replace("の", "_")
        )
        result["appLinkMode"] = "egov_api"
    else:
        result["sourceUrl"] = spec.source_url
        result["appLinkMode"] = "source_url"
        result["externalPrimarySource"] = True
    return result


def strip_verdict_prefix(text: Any) -> str:
    return VERDICT_PREFIX_RE.sub("", str(text or "").strip(), count=1).strip()


def wrong_lead(existing_explanation: Any) -> str:
    body = strip_verdict_prefix(existing_explanation)
    sentences = [piece.strip() for piece in body.split("。") if piece.strip()]
    markers = (
        "点が誤り",
        "が誤り",
        "記述は誤り",
        "としているため誤り",
        "ではなく",
        "ものではない",
        "該当しない",
        "一致しない",
    )
    candidates = [sentence for sentence in sentences if any(marker in sentence for marker in markers)]
    if candidates:
        explicit = [sentence for sentence in candidates if "誤り" in sentence]
        if explicit:
            selected = min(explicit, key=len)
        else:
            selected = ""
        if selected and selected not in {"記述は誤り", "この記述は誤り"}:
            return ensure_sentence(selected)
    return "選択肢の記載が誤り。"


def basis_display(refs: list[dict[str, Any]], raw_bases: list[str]) -> str:
    displays = [str(ref.get("display") or "").strip() for ref in refs]
    displays = [value for value in displays if value]
    if displays:
        return "、".join(dict.fromkeys(displays))
    safe_raw = [value for value in raw_bases if value and not value.startswith("JIA")]
    return "、".join(dict.fromkeys(safe_raw))


def build_explanation(
    *,
    verdict: str,
    existing_explanation: Any,
    display: str,
    basis_text: str,
) -> str:
    reason = ensure_sentence(strip_verdict_prefix(basis_text))
    if verdict == "正しい":
        if display:
            return f"正しい。{display}は、{reason}"
        return f"正しい。{reason}"
    lead = wrong_lead(existing_explanation)
    if display:
        return f"間違い。{lead}{display}は、{reason}"
    return f"間違い。{lead}{reason}"


def ensure_basis_chip(entry: dict[str, Any], displays: list[str]) -> None:
    displays = [value for value in dict.fromkeys(displays) if value]
    if not displays:
        return

    raw_questions = entry.get("suggestedQuestions")
    raw_details = entry.get("suggestedQuestionDetails")
    questions = [
        question.strip()
        for question in raw_questions
        if isinstance(question, str) and question.strip()
    ] if isinstance(raw_questions, list) else []
    details_by_question: dict[str, dict[str, str]] = {}
    if isinstance(raw_details, list):
        for detail in raw_details:
            if not isinstance(detail, dict):
                continue
            question = str(detail.get("question") or "").strip()
            answer = str(detail.get("answer") or "").strip()
            if question and answer and question not in details_by_question:
                details_by_question[question] = {"question": question, "answer": answer}

    aligned_questions: list[str] = []
    aligned_details: list[dict[str, str]] = []
    for question in questions:
        detail = details_by_question.get(question)
        if detail is None or question in aligned_questions:
            continue
        aligned_questions.append(question)
        aligned_details.append(dict(detail))
    for question, detail in details_by_question.items():
        if question in aligned_questions:
            continue
        aligned_questions.append(question)
        aligned_details.append(dict(detail))

    questions = aligned_questions
    details = aligned_details
    index = next(
        (
            idx
            for idx, question in enumerate(questions)
            if isinstance(question, str) and ("根拠" in question or "条文" in question)
        ),
        None,
    )
    answer = "根拠は、" + "、".join(displays) + "。"
    if index is None:
        question = "この問題の根拠条文は？"
        questions.append(question)
        details.append({"question": question, "answer": answer})
    else:
        detail = details[index]
        detail["question"] = questions[index]
        detail["answer"] = answer
    entry["suggestedQuestions"] = questions
    entry["suggestedQuestionDetails"] = details


def validate_review(review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    labels = review.get("sourceCorrectChoiceText")
    choices = review.get("choiceReviews")
    if not isinstance(labels, list) or not labels or any(normalize_label(label) not in TF_LABELS for label in labels):
        errors.append("sourceCorrectChoiceText must be non-empty 正しい/間違い list")
    if not isinstance(choices, list) or len(choices) != len(labels or []):
        errors.append("choiceReviews length must match sourceCorrectChoiceText")
        return errors
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            errors.append(f"choiceReviews[{index}] must be object")
            continue
        if choice.get("choiceIndex") != index:
            errors.append(f"choiceReviews[{index}].choiceIndex mismatch")
        if normalize_label(choice.get("verdict")) != normalize_label(labels[index]):
            errors.append(f"choiceReviews[{index}].verdict mismatch")
        if not isinstance(choice.get("directBasis"), list) or not choice.get("directBasis"):
            errors.append(f"choiceReviews[{index}].directBasis missing")
        if not str(choice.get("basisText") or "").strip():
            errors.append(f"choiceReviews[{index}].basisText missing")
    return errors


def finalize_review_record(
    review: dict[str, Any],
    *,
    payload: list[Any],
    apply: bool,
    stats: FinalizeStats,
) -> None:
    validation_errors = validate_review(review)
    if validation_errors:
        for error in validation_errors:
            stats.errors.append(f"{review.get('sourceQuestionKey')}: {error}")
        return

    try:
        _, entry = find_patch_entry(payload, review)
    except ValueError as exc:
        stats.errors.append(str(exc))
        return

    labels = [normalize_label(value) for value in review["sourceCorrectChoiceText"]]
    choice_reviews = list(review["choiceReviews"])
    old_explanations = entry.get("explanationText")
    old_refs = entry.get("lawReferences")
    if not isinstance(old_explanations, list) or len(old_explanations) != len(labels):
        stats.errors.append(f"{review.get('sourceQuestionKey')}: explanationText length mismatch")
        return
    if not isinstance(old_refs, list) or len(old_refs) != len(labels):
        old_refs = [[] for _ in labels]

    context = BasisContext()
    comparison_status = (
        "source_conflict"
        if review.get("officialAnswerVsStatuteStatus") == "conflict_requires_source_review"
        else "same_as_current"
    )
    new_explanations: list[str] = []
    new_law_refs: list[list[dict[str, Any]]] = []
    all_displays: list[str] = []

    for choice_index, choice_review in enumerate(choice_reviews):
        raw_bases = [str(value or "").strip() for value in choice_review["directBasis"]]
        resolved: list[dict[str, Any]] = []
        for raw_basis in raw_bases:
            resolved.extend(
                resolve_basis(
                    raw_basis,
                    context=context,
                    existing_refs=old_refs[choice_index] if isinstance(old_refs[choice_index], list) else [],
                )
            )
        resolved = dedupe_refs(resolved)
        display = basis_display(resolved, raw_bases)
        if not display:
            stats.errors.append(
                f"{review.get('sourceQuestionKey')} choice={choice_index}: directBasis could not be resolved"
            )
            continue
        explanation = build_explanation(
            verdict=labels[choice_index],
            existing_explanation=old_explanations[choice_index],
            display=display,
            basis_text=str(choice_review["basisText"]),
        )
        allow_non_law_basis = bool(review.get("lawGroundedExplanationNotNeeded"))
        if not resolved and not allow_non_law_basis:
            stats.errors.append(
                f"{review.get('sourceQuestionKey')} choice={choice_index}: no machine-readable law reference"
            )
            continue
        refs = (
            [
                firestore_law_reference(
                    ref,
                    choice_index=choice_index,
                    reason=explanation,
                    comparison_status=comparison_status,
                )
                for ref in resolved
            ]
            if resolved
            else []
        )
        new_explanations.append(explanation)
        new_law_refs.append(refs)
        all_displays.extend(str(ref.get("display") or "") for ref in resolved)

    if len(new_explanations) != len(labels) or len(new_law_refs) != len(labels):
        return

    stats.matched_questions += 1
    changed = new_explanations != old_explanations or new_law_refs != old_refs
    if changed:
        stats.updated_questions += 1
        stats.updated_choices += len(labels)
    if not apply:
        return

    entry["explanationText"] = new_explanations
    entry["suggestedExplanationText"] = list(new_explanations)
    entry["lawGroundedExplanationText"] = list(new_explanations)
    entry["lawReferences"] = new_law_refs
    has_refs = any(new_law_refs)
    entry["isLawRelated"] = has_refs or not bool(review.get("lawGroundedExplanationNotNeeded"))
    entry["lawGroundedExplanationNotNeeded"] = not has_refs
    ensure_basis_chip(entry, all_displays)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    records = load_review_records(args.review_dir)
    stats = FinalizeStats(review_records=len(records))
    payload_cache: dict[Path, list[Any]] = {}

    for review in records:
        patch_value = str(review.get("explanationPatchFile") or "").strip()
        if not patch_value:
            stats.errors.append(f"{review.get('sourceQuestionKey')}: explanationPatchFile missing")
            continue
        patch_path = resolve_patch_path(patch_value)
        if not patch_path.is_file():
            stats.errors.append(f"patch not found: {rel(patch_path)}")
            continue
        payload = payload_cache.get(patch_path)
        if payload is None:
            loaded = load_json(patch_path)
            if not isinstance(loaded, list):
                stats.errors.append(f"patch root must be list: {rel(patch_path)}")
                continue
            payload = loaded
            payload_cache[patch_path] = payload
        before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        finalize_review_record(review, payload=payload, apply=args.apply, stats=stats)
        after = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if args.apply and before != after:
            stats.changed_files.add(rel(patch_path))

    if not stats.errors and args.apply:
        for patch_path, payload in payload_cache.items():
            if rel(patch_path) in stats.changed_files:
                write_json(patch_path, payload)

    report = {
        "schemaVersion": "gas-shunin-law-explanation-finalize/v1",
        "generatedAt": utc_now(),
        "apply": bool(args.apply),
        "reviewDir": rel(args.review_dir),
        "reviewRecords": stats.review_records,
        "matchedQuestions": stats.matched_questions,
        "updatedQuestions": stats.updated_questions,
        "updatedChoices": stats.updated_choices,
        "changedFiles": sorted(stats.changed_files),
        "errorCount": len(stats.errors),
        "errors": stats.errors[:200],
        "warningCount": len(stats.warnings),
        "warnings": stats.warnings[:200],
    }
    if args.report and (args.apply or args.write_report_on_check):
        write_json(args.report, report)
    return (1 if stats.errors else 0), report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize gas-shunin law explanations from per-question manual audit records."
    )
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--apply", action="store_true", help="Update 21_explanationText_added patch files.")
    parser.add_argument(
        "--write-report-on-check",
        action="store_true",
        help="Write the report even without --apply.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.review_dir = args.review_dir.expanduser().resolve()
    args.report = args.report.expanduser().resolve() if args.report else None
    status, report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
