#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")

LAW_KEYWORDS = [
    "医師法",
    "医療法",
    "保健師助産師看護師法",
    "薬機法",
    "薬事法",
    "精神保健福祉法",
    "感染症法",
    "予防接種法",
    "学校保健安全法",
    "母子保健法",
    "介護保険法",
    "健康保険法",
    "労働安全衛生法",
    "障害者",
    "生活保護",
    "介護保険",
    "医療保険",
    "公費",
    "社会保障",
    "保健所",
    "都道府県知事",
    "市町村",
    "届出",
    "届出義務",
    "届け出",
    "診断書",
    "死亡診断書",
    "死体検案書",
    "診療録",
    "カルテ",
    "守秘義務",
    "個人情報",
    "医療保護入院",
    "措置入院",
    "任意入院",
    "応急入院",
    "行政",
    "法的",
    "通報",
    "虐待",
    "インフォームドコンセント",
]

KEYWORD_BOOSTS = [
    ("妊娠|妊婦|胎盤|羊水|流産|異所性妊娠|子宮内発育", "mk_bp_general_04_01", 0.035),
    ("分娩|陣痛|帝王切開|産褥|産後", "mk_bp_general_04_02", 0.030),
    ("新生児|在胎|出生|低出生体重|乳児|小児|発達", "mk_bp_general_04_05", 0.030),
    ("統合失調|うつ|躁|認知症|せん妄|不安|精神|妄想|幻覚", "mk_bp_general_06_08", 0.035),
    ("皮疹|紅斑|水疱|皮膚|母斑", "mk_bp_general_06_02", 0.025),
    ("視力|眼|網膜|角膜|緑内障|白内障|耳|難聴|鼻|咽頭|喉頭", "mk_bp_general_06_03", 0.025),
    ("咳|喀痰|呼吸|喘息|肺|胸水|PaO2|PaCO2", "mk_bp_general_06_04", 0.025),
    ("心電図|胸痛|心雑音|心不全|血圧|不整脈|心筋|弁", "mk_bp_general_06_04", 0.025),
    ("腹痛|下痢|嘔吐|肝|胆|膵|胃|腸|便|黄疸", "mk_bp_general_06_05", 0.025),
    ("貧血|白血球|血小板|凝固|出血|リンパ節", "mk_bp_general_06_06", 0.025),
    ("尿|腎|蛋白尿|血尿|排尿|前立腺|精巣|子宮|卵巣|月経", "mk_bp_general_06_07", 0.025),
    ("麻痺|しびれ|筋力|歩行|反射|頭痛|意識|脳|神経|関節|骨折", "mk_bp_general_06_09", 0.025),
    ("血糖|糖尿病|甲状腺|副腎|下垂体|ホルモン|電解質|肥満|栄養", "mk_bp_general_06_10", 0.025),
    ("血液検査|尿検査|培養|抗体|PCR|CRP|Hb|AST|ALT|クレアチニン", "mk_bp_general_08_01", 0.025),
    ("心電図|脳波|呼吸機能|超音波|心エコー|聴力|眼底", "mk_bp_general_08_02", 0.025),
    ("CT|MRI|X線|エックス線|画像|造影|シンチグラフィ", "mk_bp_general_08_06", 0.035),
    ("内視鏡|胃カメラ|大腸鏡", "mk_bp_general_08_07", 0.035),
    ("薬|投与|抗菌薬|副作用|禁忌|治療薬|インスリン", "mk_bp_general_09_02", 0.030),
    ("輸液|輸血|透析|血液浄化|補液", "mk_bp_general_09_03", 0.030),
    ("手術|切除|術|麻酔|縫合|ドレナージ", "mk_bp_general_09_04", 0.030),
    ("救急|ショック|心肺蘇生|意識障害|外傷|蘇生", "mk_bp_general_09_10", 0.030),
    ("感染|細菌|ウイルス|真菌|寄生虫|発熱|肺炎|結核|髄膜炎", "mk_bp_general_05_04", 0.020),
    ("癌|がん|腫瘍|悪性|転移|腺癌|扁平上皮癌", "mk_bp_general_05_06", 0.020),
    (
        "医師法|医療法|診断書|診療録|届出|保健所|感染症法|介護保険|医療保護入院|措置入院",
        "mk_bp_general_01_05",
        0.055,
    ),
    ("疫学|感度|特異度|リスク比|オッズ比|コホート|症例対照", "mk_bp_general_02_03", 0.045),
    ("人口|死亡率|出生率|平均寿命|統計", "mk_bp_general_02_02", 0.040),
    ("産業医|職業|作業環境|有機溶剤|じん肺", "mk_bp_general_02_11", 0.045),
    ("学校保健|学校医|出席停止", "mk_bp_general_02_10", 0.045),
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def get_questions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        questions = payload.get("question_bodies") or payload.get("questions") or []
    else:
        questions = payload
    return [question for question in questions if isinstance(question, dict)]


def original_question_id(question: dict[str, Any]) -> str:
    value = (
        question.get("original_question_id")
        or question.get("public_question_id")
        or question.get("question_url")
    )
    return str(value or "")


def collect_question_sets(node: Any, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        question_set_id = node.get("questionSetId")
        if question_set_id:
            out[str(question_set_id)] = str(
                node.get("name")
                or node.get("questionSetName")
                or node.get("title")
                or question_set_id
            )
        for value in node.values():
            if isinstance(value, (dict, list)):
                collect_question_sets(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_question_sets(item, out)


def normalize_snippet_text(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^選択肢\s*[0-9０-９]+\s*[\.．、:：]?\s*", "", value)
    value = re.sub(
        r"^(正しい|誤り|間違い|適切|不適切|正解|不正解)\s*[。．、:：]?\s*",
        "",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def answer_numbers(question: dict[str, Any]) -> list[int]:
    inferred = question.get("answer_result_inferred_correct_choice_numbers")
    numbers: list[int] = []
    if isinstance(inferred, list):
        for value in inferred:
            text = str(value).translate(FULLWIDTH_DIGIT_TRANS)
            if text.isdigit():
                number = int(text)
                if number > 0 and number not in numbers:
                    numbers.append(number)
    if numbers:
        return numbers

    text = str(question.get("answer_result_text") or "").translate(
        FULLWIDTH_DIGIT_TRANS
    )
    match = re.search(r"正解は\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*です", text)
    if not match:
        return numbers
    for part in match.group(1).split(","):
        token = part.strip()
        if token.isdigit():
            number = int(token)
            if number > 0 and number not in numbers:
                numbers.append(number)
    return numbers


def factual_label(question: dict[str, Any], choice_index: int) -> str:
    selected = choice_index + 1 in set(answer_numbers(question))
    if question.get("questionIntent") == "select_incorrect":
        return "間違い" if selected else "正しい"
    return "正しい" if selected else "間違い"


def explanation_for_choice(question: dict[str, Any], choice_index: int) -> str:
    snippets = question.get("explanation_choice_snippets")
    detail = ""
    if isinstance(snippets, list) and choice_index < len(snippets):
        item = snippets[choice_index]
        if isinstance(item, list) and item:
            detail = " ".join(str(value).strip() for value in item if str(value).strip())
        elif isinstance(item, str):
            detail = item

    detail = normalize_snippet_text(detail)
    if not detail:
        summary = question.get("explanation_common_summary")
        if isinstance(summary, list):
            detail = " ".join(str(value).strip() for value in summary if str(value).strip())
        elif isinstance(summary, str):
            detail = summary.strip()
    if not detail:
        detail = "正答番号と選択肢別解説を照合して判断する。"
    return f"{factual_label(question, choice_index)}。\n\n{detail}"


def explanation_for_fill_in_blank(question: dict[str, Any]) -> str:
    parts: list[str] = []
    prefix = question.get("explanation_common_prefix")
    if isinstance(prefix, list):
        parts.extend(str(value).strip() for value in prefix if str(value).strip())
    elif isinstance(prefix, str) and prefix.strip():
        parts.append(prefix.strip())

    summary = question.get("explanation_common_summary")
    if isinstance(summary, list):
        parts.extend(str(value).strip() for value in summary if str(value).strip())
    elif isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())

    if not parts:
        answer = question.get("answer_result_text")
        if isinstance(answer, str) and answer.strip():
            parts.append(answer.strip())
    if not parts:
        parts.append("設問文の条件と解説を照合して解答する。")

    return "解答。\n\n" + "\n".join(parts)


def law_grounded_explanation_not_needed(
    question: dict[str, Any], explanations: list[str]
) -> bool:
    haystack = "\n".join(
        [
            str(question.get("questionBodyText") or ""),
            " ".join(map(str, question.get("choiceTextList") or [])),
            "\n".join(explanations),
        ]
    )
    return not any(keyword in haystack for keyword in LAW_KEYWORDS)


def suggested_questions(
    question: dict[str, Any], occurrence: str
) -> tuple[list[str], list[dict[str, str]]]:
    label = str(question.get("questionLabel") or f"{occurrence}回の問題")
    questions = [
        f"{label}で問われる判断基準は何か。",
        f"{label}の正答選択肢と誤答選択肢の違いは何か。",
    ]
    details = [
        {
            "question": questions[0],
            "answer": "設問文の条件、検査所見、病態の対応関係を選択肢別解説に沿って整理する。",
        },
        {
            "question": questions[1],
            "answer": "正答番号と各選択肢の解説を対応させ、正しい記述と誤った記述の根拠を確認する。",
        },
    ]
    return questions, details


def question_text(question: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("questionLabel", "questionBodyText", "examBlock"):
        value = question.get(key)
        if isinstance(value, str):
            parts.append(value)

    choices = question.get("choiceTextList")
    if isinstance(choices, list):
        parts.extend(str(value) for value in choices if value is not None)

    explanations = question.get("explanationText")
    if isinstance(explanations, list):
        parts.extend(str(value) for value in explanations if value is not None)

    snippets = question.get("explanation_choice_snippets")
    if isinstance(snippets, list):
        for item in snippets:
            if isinstance(item, list):
                parts.extend(str(value) for value in item if value is not None)
            elif item is not None:
                parts.append(str(item))

    summary = question.get("explanation_common_summary")
    if isinstance(summary, list):
        parts.extend(str(value) for value in summary if value is not None)
    elif isinstance(summary, str):
        parts.append(summary)

    return " ".join(parts)


def resolve_question_type(question: dict[str, Any]) -> str:
    choices = question.get("choiceTextList")
    if not isinstance(choices, list) or not any(str(choice).strip() for choice in choices):
        return "fill_in_blank"
    question_type = str(question.get("questionType") or "").strip()
    if question_type == "free_text":
        return "fill_in_blank"
    return question_type or "true_false"


def resolve_question_intent(question: dict[str, Any]) -> str:
    if resolve_question_type(question) == "fill_in_blank":
        return "free_text"
    return str(question.get("questionIntent") or "select_correct")


def ngrams(text: str) -> Counter[str]:
    compact = re.sub(r"\s+", "", text)
    counts: Counter[str] = Counter()
    if not compact:
        return counts

    for size in (2, 3, 4):
        if len(compact) < size:
            continue
        for index in range(len(compact) - size + 1):
            gram = compact[index : index + size]
            if gram.strip():
                counts[gram] += 1

    for token in re.findall(r"[A-Za-z][A-Za-z0-9+\-]{1,}|[0-9]+(?:\.[0-9]+)?", compact):
        counts[token.lower()] += 2
    return counts


class QuestionSetClassifier:
    def __init__(
        self,
        *,
        base_dir: Path,
        category_path: Path,
        target_occurrence: str,
        train_start: int,
        train_end: int,
        excluded_occurrences: set[str],
    ) -> None:
        self.base_dir = base_dir
        self.target_occurrence = target_occurrence
        self.train_start = train_start
        self.train_end = train_end
        self.excluded_occurrences = excluded_occurrences | {target_occurrence}
        self.question_set_names: dict[str, str] = {}
        collect_question_sets(load_json(category_path), self.question_set_names)
        self.valid_question_sets = set(self.question_set_names)
        self.allowed_grams: set[str] = set()
        self.idf: dict[str, float] = {}
        self.centroids: dict[str, dict[str, float]] = {}
        self.train_docs = 0
        self._keyword_patterns = [
            (re.compile(pattern), question_set_id, boost)
            for pattern, question_set_id, boost in KEYWORD_BOOSTS
            if question_set_id in self.valid_question_sets
        ]
        self._fit()

    def _fit(self) -> None:
        train: list[tuple[str, Counter[str]]] = []
        for occurrence_dir in sorted(
            self.base_dir.iterdir(),
            key=lambda path: int(path.name) if path.name.isdigit() else 9999,
        ):
            if not occurrence_dir.name.isdigit():
                continue
            if occurrence_dir.name in self.excluded_occurrences:
                continue
            occurrence = int(occurrence_dir.name)
            if occurrence < self.train_start or occurrence > self.train_end:
                continue

            files = sorted(
                path
                for path in (occurrence_dir / "30_merged_2").glob("*.json")
                if path.is_file() and "manual" not in path.name
            )
            if not files:
                continue
            for question in get_questions(load_json(files[-1])):
                question_set_id = str(question.get("questionSetId") or "")
                if question_set_id and question_set_id in self.valid_question_sets:
                    train.append((question_set_id, ngrams(question_text(question))))

        if not train:
            raise RuntimeError("questionSetId training data not found")

        self.train_docs = len(train)
        df: Counter[str] = Counter()
        for _, vector in train:
            df.update(vector.keys())

        max_df = max(3, int(self.train_docs * 0.45))
        self.allowed_grams = {
            gram for gram, count in df.items() if count >= 2 and count <= max_df
        }
        self.idf = {
            gram: math.log((self.train_docs + 1) / (df[gram] + 1)) + 1.0
            for gram in self.allowed_grams
        }

        class_sums: dict[str, defaultdict[str, float]] = {}
        class_counts: Counter[str] = Counter()
        for question_set_id, counts in train:
            vector = self._vectorize(counts)
            if not vector:
                continue
            dest = class_sums.setdefault(question_set_id, defaultdict(float))
            class_counts[question_set_id] += 1
            for gram, weight in vector.items():
                dest[gram] += weight

        for question_set_id, sums in class_sums.items():
            if class_counts[question_set_id] <= 0:
                continue
            averaged = {
                gram: weight / class_counts[question_set_id]
                for gram, weight in sums.items()
            }
            norm = math.sqrt(sum(weight * weight for weight in averaged.values())) or 1.0
            self.centroids[question_set_id] = {
                gram: weight / norm for gram, weight in averaged.items()
            }

    def _vectorize(self, counts: Counter[str]) -> dict[str, float]:
        total = sum(counts[gram] for gram in counts if gram in self.allowed_grams) or 1
        vector: dict[str, float] = {}
        norm = 0.0
        for gram, count in counts.items():
            if gram not in self.allowed_grams:
                continue
            weight = (count / total) * self.idf[gram]
            if weight <= 0:
                continue
            vector[gram] = weight
            norm += weight * weight
        if norm <= 0:
            return {}
        inv_norm = 1.0 / math.sqrt(norm)
        return {gram: weight * inv_norm for gram, weight in vector.items()}

    def classify(self, text: str) -> list[tuple[str, float]]:
        vector = self._vectorize(ngrams(text))
        scores: dict[str, float] = {}
        for question_set_id, centroid in self.centroids.items():
            if len(vector) < len(centroid):
                score = sum(weight * centroid.get(gram, 0.0) for gram, weight in vector.items())
            else:
                score = sum(vector.get(gram, 0.0) * weight for gram, weight in centroid.items())
            scores[question_set_id] = score

        for pattern, question_set_id, boost in self._keyword_patterns:
            if pattern.search(text):
                scores[question_set_id] = scores.get(question_set_id, 0.0) + boost

        for question_set_id, name in self.question_set_names.items():
            if (
                question_set_id in scores
                and name
                and len(name) >= 2
                and name in text
            ):
                scores[question_set_id] += 0.025

        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:5]


def generate_patches(
    *,
    occurrence: str,
    base_dir: Path,
    category_path: Path,
    train_start: int,
    train_end: int,
    excluded_occurrences: set[str],
) -> dict[str, Any]:
    source_path = base_dir / occurrence / "00_source" / f"question_{occurrence}.json"
    source_questions = get_questions(load_json(source_path))
    if not source_questions:
        raise RuntimeError(f"source questions not found: {source_path}")
    original_ids = [original_question_id(question) for question in source_questions]
    if len(set(original_ids)) != len(original_ids):
        raise RuntimeError(f"duplicate original_question_id in source: {source_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    occurrence_dir = base_dir / occurrence

    qtype_formal = [
        {
            "questionBodyText": question.get("questionBodyText", ""),
            "choiceTextList": question.get("choiceTextList", []),
            "questionType": resolve_question_type(question),
            "original_question_id": original_question_id(question),
            "question_url": question.get("question_url", ""),
        }
        for question in source_questions
    ]
    qtype_path = (
        occurrence_dir
        / "10_questionType_fixed"
        / f"question_{occurrence}_questionType_fixed_{timestamp}.json"
    )
    save_json(qtype_path, qtype_formal)

    intent_formal = [
        {
            "questionIntent_changed": False,
            "questionIntent_change_detail": "",
            "original_question_id": original_question_id(question),
            "questionIntent": resolve_question_intent(question),
            "questionIntent_change_reason": "",
        }
        for question in source_questions
    ]
    intent_path = (
        occurrence_dir
        / "15_correctChoiceText_fixed"
        / f"question_{occurrence}_correctChoiceText_fixed_{timestamp}.json"
    )
    save_json(intent_path, intent_formal)

    explanation_formal: list[dict[str, Any]] = []
    for question in source_questions:
        choices = question.get("choiceTextList") or []
        if choices:
            explanations = [
                explanation_for_choice(question, index) for index in range(len(choices))
            ]
        else:
            explanations = [explanation_for_fill_in_blank(question)]
        suggested, suggested_details = suggested_questions(question, occurrence)
        explanation_formal.append(
            {
                "explanationText": explanations,
                "suggestedQuestions": suggested,
                "suggestedQuestionDetails": suggested_details,
                "original_question_id": original_question_id(question),
                "question_url": question.get("question_url", ""),
                "lawGroundedExplanationNotNeeded": law_grounded_explanation_not_needed(
                    question, explanations
                ),
            }
        )
    explanation_path = (
        occurrence_dir
        / "21_explanationText_added"
        / f"question_{occurrence}_explanationText_added_{timestamp}.json"
    )
    save_json(explanation_path, explanation_formal)

    classifier = QuestionSetClassifier(
        base_dir=base_dir,
        category_path=category_path,
        target_occurrence=occurrence,
        train_start=train_start,
        train_end=train_end,
        excluded_occurrences=excluded_occurrences,
    )
    qset_raw: list[dict[str, str]] = []
    report_rows: list[dict[str, Any]] = []
    for question in source_questions:
        top = classifier.classify(question_text(question))
        question_set_id = top[0][0] if top else ""
        margin = top[0][1] - (top[1][1] if len(top) > 1 else 0.0) if top else 0.0
        qset_raw.append(
            {
                "original_question_id": original_question_id(question),
                "questionSetId": question_set_id,
            }
        )
        report_rows.append(
            {
                "original_question_id": original_question_id(question),
                "questionLabel": question.get("questionLabel"),
                "questionSetId": question_set_id,
                "questionSetName": classifier.question_set_names.get(
                    question_set_id, ""
                ),
                "autoTop": [
                    {
                        "questionSetId": candidate,
                        "score": round(score, 6),
                        "name": classifier.question_set_names.get(candidate, ""),
                    }
                    for candidate, score in top
                ],
                "overridden": False,
                "margin": round(margin, 6),
            }
        )

    qset_raw_path = (
        occurrence_dir
        / "22_questionSetId_linked"
        / f"question_{occurrence}_questionSetId_raw_{timestamp}.json"
    )
    qset_path = (
        occurrence_dir
        / "22_questionSetId_linked"
        / f"question_{occurrence}_questionSetId_linked_{timestamp}.json"
    )
    qset_report_path = (
        occurrence_dir
        / "22_questionSetId_linked"
        / f"question_{occurrence}_questionSetId_auto_report_{timestamp}.json"
    )
    save_json(qset_raw_path, qset_raw)
    save_json(
        qset_path,
        [
            {
                "questionSetId": raw["questionSetId"],
                "original_question_id": original_question_id(question),
                "question_url": question.get("question_url", ""),
            }
            for question, raw in zip(source_questions, qset_raw)
        ],
    )
    save_json(
        qset_report_path,
        {
            "trainDocs": classifier.train_docs,
            "trainQuestionSets": len(classifier.centroids),
            "targetCount": len(source_questions),
            "distribution": dict(Counter(row["questionSetId"] for row in report_rows)),
            "rows": report_rows,
        },
    )

    return {
        "occurrence": occurrence,
        "timestamp": timestamp,
        "sourceCount": len(source_questions),
        "trainDocs": classifier.train_docs,
        "trainQuestionSets": len(classifier.centroids),
        "lawGroundedExplanationNotNeeded": dict(
            Counter(
                entry["lawGroundedExplanationNotNeeded"]
                for entry in explanation_formal
            )
        ),
        "qsetDistributionTop20": [
            {
                "questionSetId": question_set_id,
                "count": count,
                "name": classifier.question_set_names.get(question_set_id, ""),
            }
            for question_set_id, count in Counter(
                row["questionSetId"] for row in report_rows
            ).most_common(20)
        ],
        "paths": {
            "questionType": str(qtype_path),
            "correctChoiceText": str(intent_path),
            "explanationText": str(explanation_path),
            "questionSetIdRaw": str(qset_raw_path),
            "questionSetId": str(qset_path),
            "questionSetIdReport": str(qset_report_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate mecnet-kokushi patch artifacts for one occurrence."
    )
    parser.add_argument("occurrence", help="exam occurrence/list_group_id, e.g. 103")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("output/mecnet-kokushi/questions_json"),
        help="questions_json root",
    )
    parser.add_argument(
        "--category-json",
        type=Path,
        default=Path("output/mecnet-kokushi/category/category.json"),
    )
    parser.add_argument("--train-start", type=int, default=69)
    parser.add_argument("--train-end", type=int, default=120)
    parser.add_argument(
        "--exclude-occurrence",
        action="append",
        default=["101"],
        help="occurrence to exclude from questionSetId training; can be repeated",
    )
    args = parser.parse_args()

    summary = generate_patches(
        occurrence=str(args.occurrence),
        base_dir=args.base_dir,
        category_path=args.category_json,
        train_start=args.train_start,
        train_end=args.train_end,
        excluded_occurrences={str(value) for value in args.exclude_occurrence},
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
