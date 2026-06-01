#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


QUALIFICATION = "gas-shunin-otsu"
ROOT_DIR = Path("output") / QUALIFICATION / "questions_json"
PATCH_SUBDIR = "21_explanationText_added"
SOURCE_SUBDIR = "20_merged_1_law_only"

TODAY = date(2026, 6, 1).isoformat()
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M")

REFERENCE_PATTERN = re.compile(r"📌 関連:\s*(.+)")
WRONG_MARK_PATTERN = re.compile(r"\[wrong\](.*?)\[/wrong\]")
ARTICLE_PATTERN = re.compile(r"(?P<article>\d+条(?:の\d+)?)")
PARAGRAPH_PATTERN = re.compile(r"(?P<paragraph>\d+項)")
ITEM_PATTERN = re.compile(r"(?P<item>[一二三四五六七八九十百]+号)")


LAW_METADATA: dict[str, dict[str, str]] = {
    "法": {
        "lawId": "329AC0000000051",
        "lawRevisionId": "329AC0000000051_20251225_506AC0000000067",
        "lawTitle": "ガス事業法",
    },
    "政令": {
        "lawId": "329CO0000000068",
        "lawRevisionId": "329CO0000000068_20251225_506CO0000000374",
        "lawTitle": "ガス事業法施行令",
    },
    "規": {
        "lawId": "345M50000400097",
        "lawRevisionId": "345M50000400097_20251225_507M60000400006",
        "lawTitle": "ガス事業法施行規則",
    },
    "規則": {
        "lawId": "345M50000400097",
        "lawRevisionId": "345M50000400097_20251225_507M60000400006",
        "lawTitle": "ガス事業法施行規則",
    },
    "技省令": {
        "lawId": "412M50000400111",
        "lawRevisionId": "412M50000400111_20240427_506M60000400035",
        "lawTitle": "ガス工作物の技術上の基準を定める省令",
    },
    "高圧ガス保安法": {
        "lawId": "326AC0000000204",
        "lawRevisionId": "326AC0000000204_20261221_504AC0000000074",
        "lawTitle": "高圧ガス保安法",
    },
    "ガス関係報告規": {
        "lawId": "429M60000400016",
        "lawRevisionId": "429M60000400016_20251225_507M60000400006",
        "lawTitle": "ガス関係報告規則",
    },
    "ガス関係報告規則": {
        "lawId": "429M60000400016",
        "lawRevisionId": "429M60000400016_20251225_507M60000400006",
        "lawTitle": "ガス関係報告規則",
    },
    "報告規則": {
        "lawId": "429M60000400016",
        "lawRevisionId": "429M60000400016_20251225_507M60000400006",
        "lawTitle": "ガス関係報告規則",
    },
    "特監法": {
        "lawId": "354AC0000000033",
        "lawRevisionId": "354AC0000000033_20200612_502AC0000000049",
        "lawTitle": "特定ガス消費機器の設置工事の監督に関する法律",
    },
    "特監令": {
        "lawId": "354CO0000000231",
        "lawRevisionId": "354CO0000000231_20191216_501CO0000000183",
        "lawTitle": "特定ガス消費機器の設置工事の監督に関する法律施行令",
    },
    "特監規則": {
        "lawId": "354M50000400077",
        "lawRevisionId": "354M50000400077_20240401_506M60000400030",
        "lawTitle": "特定ガス消費機器の設置工事の監督に関する法律施行規則",
    },
    "技告示": {
        "lawTitle": "ガス工作物の技術上の基準の細目を定める告示",
    },
}

ALIASES = sorted(LAW_METADATA.keys(), key=len, reverse=True)


@dataclass
class QuestionContext:
    raw_default_alias: str | None = None
    raw_default_article: str | None = None
    raw_default_paragraph: str | None = None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def source_files(root: Path) -> list[Path]:
    return sorted(root.glob(f"*/{SOURCE_SUBDIR}/question_*_merged_law_only.json"))


def choice_is_incorrect(snippet_text: str) -> bool:
    if "正しくは:" in snippet_text:
        return True
    if "規定なし" in snippet_text:
        return True
    if "そのような規定はない" in snippet_text:
        return True
    return False


def extract_reference_text(snippet_text: str) -> str | None:
    match = REFERENCE_PATTERN.search(snippet_text)
    if not match:
        return None
    return match.group(1).strip()


def extract_correction(snippet_text: str) -> str | None:
    first_line = snippet_text.splitlines()[0].strip() if snippet_text else ""
    if not first_line.startswith("正しくは:"):
        return None
    return first_line.replace("正しくは:", "", 1).strip()


def extract_wrong_phrase(marked_text: Any) -> str | None:
    if not isinstance(marked_text, str):
        return None
    match = WRONG_MARK_PATTERN.search(marked_text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def normalize_ref_token(token: str) -> str:
    token = token.strip()
    token = token.replace("（", "(").replace("）", ")")
    token = token.replace("　", "")
    token = token.rstrip("。")
    token = re.sub(r"\s+", "", token)
    return token


def infer_question_context(question: dict[str, Any]) -> QuestionContext:
    body = str(question.get("questionBodyText") or "")
    if "事故報告" in body or "事故速報" in body or "事故詳報" in body:
        return QuestionContext(
            raw_default_alias="ガス関係報告規則",
            raw_default_article="4条",
        )
    if "保安規程" in body:
        return QuestionContext(
            raw_default_alias="法",
            raw_default_article="64条",
        )
    return QuestionContext()


def split_reference_parts(raw_reference_text: str) -> list[str]:
    normalized = raw_reference_text.replace("，", "、").replace(",", "、")
    return [normalize_ref_token(part) for part in normalized.split("、") if normalize_ref_token(part)]


def parse_reference_part(
    part: str,
    *,
    choice_index: int,
    context: QuestionContext,
    inherited: dict[str, str | None],
) -> dict[str, str | int] | None:
    alias = next((value for value in ALIASES if part.startswith(value)), None)
    remainder = part
    if alias:
        remainder = part[len(alias):]
    else:
        alias = inherited.get("alias") or context.raw_default_alias

    if not alias:
        return None

    article_match = ARTICLE_PATTERN.search(remainder)
    article = article_match.group("article") if article_match else None
    if not article:
        article = inherited.get("article") or context.raw_default_article

    paragraph_match = PARAGRAPH_PATTERN.search(remainder)
    paragraph = paragraph_match.group("paragraph") if paragraph_match else None
    if not paragraph and part in {"1項", "2項", "3項", "4項", "5項"}:
        paragraph = part
    if not paragraph:
        paragraph = inherited.get("paragraph") if ARTICLE_PATTERN.search(remainder) is None else None
    if paragraph is None and context.raw_default_paragraph:
        paragraph = context.raw_default_paragraph

    item_match = ITEM_PATTERN.search(remainder)
    item = item_match.group("item") if item_match else None
    if item is None and re.fullmatch(r"[一二三四五六七八九十百]+号", part):
        item = part

    metadata = LAW_METADATA.get(alias, {})
    verification_status = "verified" if metadata.get("lawId") and alias != "技告示" else "candidate"

    reason = part
    if alias == context.raw_default_alias and raw_ref_is_contextual_only(part):
        verification_status = "candidate"
        reason = f"{part}（設問文脈から {context.raw_default_alias}{context.raw_default_article or ''} を補完）"

    reference = {
        "role": "current_basis",
        "scope": "choice",
        "choiceIndex": choice_index,
        "lawTitle": metadata.get("lawTitle", alias),
        "lawAlias": alias,
        "referenceDate": TODAY,
        "verificationStatus": verification_status,
        "reason": reason,
    }
    if metadata.get("lawId"):
        reference["lawId"] = metadata["lawId"]
    if metadata.get("lawRevisionId"):
        reference["lawRevisionId"] = metadata["lawRevisionId"]
    if article:
        reference["article"] = article
    if paragraph:
        reference["paragraph"] = paragraph
    if item:
        reference["item"] = item

    inherited["alias"] = alias
    if article:
        inherited["article"] = article
        inherited["paragraph"] = paragraph
    elif paragraph:
        inherited["paragraph"] = paragraph
    return reference


def raw_ref_is_contextual_only(part: str) -> bool:
    return (
        re.fullmatch(r"[一二三四五六七八九十百]+号(?:事故)?", part) is not None
        or re.fullmatch(r"\d+項", part) is not None
        or part in {"一号、五号", "三号、七号", "ロ", "2項ただし書き三号", "3項一号", "3項三号"}
    )


def parse_law_references(question: dict[str, Any], choice_index: int, snippet_text: str) -> list[dict[str, str | int]]:
    raw_reference_text = extract_reference_text(snippet_text)
    if not raw_reference_text or raw_reference_text == "規定なし":
        return []

    context = infer_question_context(question)
    inherited: dict[str, str | None] = {
        "alias": context.raw_default_alias,
        "article": context.raw_default_article,
        "paragraph": context.raw_default_paragraph,
    }

    references: list[dict[str, str | int]] = []
    for part in split_reference_parts(raw_reference_text):
        reference = parse_reference_part(
            part,
            choice_index=choice_index,
            context=context,
            inherited=inherited,
        )
        if reference is None:
            continue
        references.append(reference)

    deduped: list[dict[str, str | int]] = []
    seen: set[tuple[Any, ...]] = set()
    for reference in references:
        key = (
            reference.get("lawAlias"),
            reference.get("article"),
            reference.get("paragraph"),
            reference.get("item"),
            reference.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reference)
    return deduped


def reference_to_display(reference: dict[str, Any]) -> str:
    title = str(reference.get("lawTitle") or reference.get("lawAlias") or "").strip()
    article = str(reference.get("article") or "").strip()
    paragraph = str(reference.get("paragraph") or "").strip()
    item = str(reference.get("item") or "").strip()
    return "".join(part for part in [title, f"第{article}" if article else "", f"第{paragraph}" if paragraph else "", item] if part)


def unique_reference_displays(law_references: list[list[dict[str, Any]]]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for choice_refs in law_references:
        for reference in choice_refs:
            display = reference_to_display(reference)
            if display:
                ordered.setdefault(display, None)
    return list(ordered.keys())


def build_choice_explanation(question: dict[str, Any], choice_index: int) -> str:
    choice_text_list = question.get("choiceTextList") or []
    marked_list = question.get("choiceTextMarkedList") or []
    snippets = question.get("explanation_choice_snippets") or []

    choice_text = str(choice_text_list[choice_index] or "")
    marked_text = marked_list[choice_index] if choice_index < len(marked_list) else choice_text
    snippet_entry = snippets[choice_index] if choice_index < len(snippets) else []
    snippet_text = "\n".join(snippet_entry) if isinstance(snippet_entry, list) else str(snippet_entry or "")

    incorrect = choice_is_incorrect(snippet_text)
    correction = extract_correction(snippet_text)
    wrong_phrase = extract_wrong_phrase(marked_text)
    references = parse_law_references(question, choice_index, snippet_text)
    reference_text = "、".join(unique_reference_displays([references]))

    if incorrect:
        pieces = ["この記述は間違いです。"]
        if wrong_phrase:
            pieces.append(f"誤りは「{wrong_phrase}」です。")
        if correction:
            if correction.endswith("。"):
                pieces.append(f"正しくは{correction}")
            else:
                pieces.append(f"正しくは「{correction}」です。")
        if reference_text:
            pieces.append(f"根拠は{reference_text}です。")
        return "".join(pieces)

    pieces = ["この記述は正しいです。"]
    if reference_text:
        pieces.append(f"{reference_text}の定めに沿っています。")
    else:
        pieces.append("選択肢の内容は条文趣旨と整合しています。")
    if choice_text and len(choice_text) > 18:
        pieces.append("主体・数値・手続の対応を崩さずに覚えると再現しやすいです。")
    return "".join(pieces)


def classify_question_theme(question_body_text: str) -> str:
    text = question_body_text or ""
    if "事故" in text and ("速報" in text or "詳報" in text or "事故報告" in text):
        return "accident"
    if "定義" in text or "用語" in text:
        return "definition"
    if "保安規程" in text:
        return "safety_rule"
    if "ガス主任技術者" in text:
        return "chief_engineer"
    if "特定ガス消費機器" in text or "特監法" in text:
        return "special_consumer"
    if "工事計画" in text or "検査" in text:
        return "inspection"
    if "技術基準" in text or "ガス工作物" in text or "整圧器" in text or "導管" in text:
        return "technical"
    return "generic"


def build_suggested_questions(question: dict[str, Any], law_references: list[list[dict[str, Any]]]) -> tuple[list[str], list[dict[str, str]]]:
    body = str(question.get("questionBodyText") or "")
    theme = classify_question_theme(body)
    references = unique_reference_displays(law_references)
    references_text = "、".join(references[:4]) if references else "関連条文"
    wrong_terms = [
        extract_wrong_phrase(marked)
        for marked in question.get("choiceTextMarkedList") or []
    ]
    wrong_terms = [term for term in wrong_terms if term][:3]
    wrong_terms_text = "、".join(wrong_terms) if wrong_terms else "主体・数値・手続"
    corrections = []
    for snippet_entry in question.get("explanation_choice_snippets") or []:
        snippet_text = "\n".join(snippet_entry) if isinstance(snippet_entry, list) else str(snippet_entry or "")
        correction = extract_correction(snippet_text)
        if correction:
            corrections.append(correction)
    corrections_text = "、".join(corrections[:3]) if corrections else "条文どおりの表現"

    if theme == "accident":
        questions = [
            "どの事故なら報告対象になる？",
            "速報と詳報はどう見分ける？",
            "この問題のひっかけはどこ？",
        ]
        answers = [
            f"まず事故類型が報告規則の列挙に入るかを確認します。今回の起点は {references_text} で、人身事故、供給支障戸数、製造支障時間などの要件を一つずつ照合すると判定しやすいです。",
            "速報か詳報かは、報告規則上の事故類型に当たるかを先に見て、その後に戸数・時間・被害態様のしきい値を確認します。似た事故名でも、供給支障戸数や製造支障時間が変わると結論が変わります。",
            f"ひっかけは {wrong_terms_text} のような要件の差し替えです。事故の主体、損壊箇所、死傷の有無、戸数や時間の閾値を条文どおりに読み分けるのがポイントです。",
        ]
    elif theme == "definition":
        questions = [
            "この問題はどの定義を優先して覚える？",
            "数値や主体のひっかけはどこ？",
            "関連条文はどこを確認すればよい？",
        ]
        answers = [
            f"定義問題では、用語そのものよりも対象範囲と例外条件を先に押さえると崩れません。今回なら {references_text} が起点で、定義の主語と限定条件をまとめて覚えるのが有効です。",
            f"ひっかけは {wrong_terms_text} のような数値・主体・対象範囲の差し替えです。定義条文では一語ずれるだけで別概念になるので、語尾まで含めて確認してください。",
            f"まず {references_text} を見て、条文上の定義が何を含み何を除くかを確認すると判断が安定します。現行法の定義を正本にして、過去問の表現差は補足として整理するのが安全です。",
        ]
    elif theme == "safety_rule":
        questions = [
            "保安規程では何を先に見る？",
            "届出や命令の主体はどう整理する？",
            "この問題で外しやすい語句は？",
        ]
        answers = [
            f"保安規程の問題では、誰が定めるか、いつ届け出るか、何を命じられるかの3点を先に分けて見ます。今回の根拠は {references_text} で、条ごとの役割を混ぜないのが重要です。",
            f"主体は一般ガス導管事業者・ガス小売事業者・経済産業大臣のどれかで整理します。届出義務と命令権限を取り違えると失点しやすいので、条文ごとの主語を固定して覚えてください。",
            f"この設問では {wrong_terms_text} のような手続語が外しやすいです。開始前、遅滞なく、命ずることができる、といった文言差に注意すると精度が上がります。",
        ]
    elif theme == "chief_engineer":
        questions = [
            "ガス主任技術者の論点は何を整理すればいい？",
            "免状・実務経験・選任の関係は？",
            "この問題の誤りはどこに出やすい？",
        ]
        answers = [
            f"ガス主任技術者では、選任できる人の要件、事業区分ごとの範囲、行政処分の根拠を分けて整理します。今回の根拠は {references_text} です。",
            "免状の有無だけでなく、事業区分に応じた実務経験や選任義務の主体まで追う必要があります。資格・経験・届出の順で読むと混乱しにくいです。",
            f"誤りは {wrong_terms_text} のように主体や要件を少しずらして出やすいです。誰を選任するのか、誰が届出るのか、どの処分が可能かを一つずつ切り分けてください。",
        ]
    elif theme == "inspection":
        questions = [
            "工事計画や検査はどの順で確認する？",
            "届出・保存・検査の違いは？",
            "この問題で先に見るべき語句は？",
        ]
        answers = [
            f"まず工事計画の届出、次に検査の実施主体、最後に記録保存義務の順で見ると整理しやすいです。今回の条文起点は {references_text} です。",
            f"届出義務、検査義務、記録保存義務は別の論点です。{corrections_text} のように手続の種類が差し替えられていないかを確認してください。",
            f"先に見るべきなのは {wrong_terms_text} のような手続語です。『受理した日から』『保存しなければならない』のような文言差がそのまま正誤に直結します。",
        ]
    elif theme == "special_consumer":
        questions = [
            "特監法では何を覚えるべき？",
            "軽微な工事や対象機器はどう見分ける？",
            "この問題の判断軸は？",
        ]
        answers = [
            f"特監法では、対象機器、特定工事に当たるか、監督者の義務や講習を分けて覚えるのが基本です。今回の根拠は {references_text} です。",
            "軽微な工事かどうか、特定ガス消費機器に当たるかどうかで規制の有無が変わります。法律本体と施行規則の双方を見て判断してください。",
            f"判断軸は {wrong_terms_text} のような対象範囲の違いです。法律・施行令・施行規則の役割を混同しないことが重要です。",
        ]
    elif theme == "technical":
        questions = [
            "この技術基準は何を防ぐための規定？",
            "数値や対象設備はどう見分ける？",
            "関連条文はどこを確認すればよい？",
        ]
        answers = [
            f"技術基準は漏えい、破損、逆流、過圧、着火など、どの危険を防ぐ規定かで整理すると覚えやすいです。今回の根拠は {references_text} です。",
            f"数値や対象設備の差し替えが典型的なひっかけです。{wrong_terms_text} や {corrections_text} のような表現を、設備の種類とセットで確認してください。",
            f"まず {references_text} を確認し、要求されている設備・措置・適用除外を見ます。技省令と告示が分かれている場合は、省令が大枠、告示が細目です。",
        ]
    else:
        questions = [
            "この問題は何を先に確認すると解きやすい？",
            "ひっかけになっている語句はどこ？",
            "関連条文はどこを確認すればよい？",
        ]
        answers = [
            f"まず主体、手続、数値のどこが論点かを切り分けます。今回の条文起点は {references_text} で、主語と義務の対応を崩さないことが重要です。",
            f"ひっかけは {wrong_terms_text} のような一語の差し替えです。語句の違和感を見つけたら、条文どおりの表現かどうかを確認してください。",
            f"関連条文は {references_text} を起点に見ると整理しやすいです。現行法の条文を正本にし、その上で出題当時の表現差があれば補足として押さえるのが安全です。",
        ]

    details = [
        {
            "question": question_text,
            "answer": answer_text,
        }
        for question_text, answer_text in zip(questions, answers)
    ]
    return questions, details


def build_patch_entry(question: dict[str, Any]) -> dict[str, Any]:
    explanations = [build_choice_explanation(question, idx) for idx in range(len(question.get("choiceTextList") or []))]
    law_references = [
        parse_law_references(
            question,
            idx,
            "\n".join(snippet_entry) if isinstance(snippet_entry, list) else str(snippet_entry or ""),
        )
        for idx, snippet_entry in enumerate(question.get("explanation_choice_snippets") or [])
    ]
    suggested_questions, suggested_details = build_suggested_questions(question, law_references)
    entry = {
        "original_question_id": question["original_question_id"],
        "question_url": question["question_url"],
        "explanationText": explanations,
        "suggestedQuestions": suggested_questions,
        "suggestedQuestionDetails": suggested_details,
        "lawReferences": law_references,
    }
    return entry


def build_patch_filename(source_file: Path) -> str:
    base = source_file.name.replace("_merged_law_only.json", "_merged.json")
    return f"{base[:-5]}_explanationText_added_{TIMESTAMP}.json"


def process_source_file(source_file: Path) -> Path:
    data = load_json(source_file)
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"{source_file} に question_bodies がありません")
    entries = [build_patch_entry(question) for question in questions if isinstance(question, dict)]
    patch_dir = source_file.parent.parent / PATCH_SUBDIR
    patch_path = patch_dir / build_patch_filename(source_file)
    dump_json(patch_path, entries)
    return patch_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR,
        help=f"default: {ROOT_DIR}",
    )
    args = parser.parse_args()

    generated_paths: list[Path] = []
    zero_choice_questions: list[tuple[int | str, str, str]] = []
    for source_file in source_files(args.root):
        data = load_json(source_file)
        for question in data.get("question_bodies", []):
            if not isinstance(question, dict):
                continue
            if len(question.get("choiceTextList") or []) == 0:
                zero_choice_questions.append(
                    (
                        question.get("examYear") or "",
                        question.get("questionLabel") or "",
                        question.get("original_question_id") or "",
                    )
                )
        generated_paths.append(process_source_file(source_file))

    print(f"generated {len(generated_paths)} patch files")
    for path in generated_paths:
        print(path)
    if zero_choice_questions:
        print(f"[WARN] choiceTextList が空の law question: {len(zero_choice_questions)}")
        for year, label, question_id in zero_choice_questions:
            print(f"  - {year} {label} {question_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
