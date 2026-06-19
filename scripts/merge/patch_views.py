from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from scripts.common.question_identity import review_question_id
from scripts.merge.merge_utils import (
    build_manual_output_path,
    maybe_split_for_manual_output,
)


EXPLANATION_FIELDS = [
    "explanationText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawGroundedExplanationNotNeeded",
    "explanation_common_prefix",
    "explanation_common_prefix_inferred_correct_choice",
    "explanation_common_summary",
    "explanation_choice_snippets",
    "explanation_choice_correctness",
]
NEGATIVE_PROMPT_PHRASES = (
    "最も不適当なもの",
    "最も不適当",
    "不適当なもの",
    "不適当なの",
    "不適切なもの",
    "不適切なの",
    "適さない",
    "適切でない",
    "適切でないもの",
    "適切でないの",
    "適当でない",
    "適当でないもの",
    "適当でないの",
    "誤っている",
    "誤っているもの",
    "誤っているの",
    "誤っている記述",
    "誤っている組合せ",
    "誤っている組み合わせ",
    "誤っている配列",
    "誤ったもの",
    "誤った組合せ",
    "誤った組み合わせ",
    "誤った配列",
    "誤りのあるもの",
    "誤りのある記述",
    "誤りはどれか",
    "正しくないもの",
    "正しくないの",
    "考えられない",
    "考えにくい",
    "認められない",
    "診られない",
    "共通しない",
    "属さない",
    "栄養されない",
    "通らない",
    "存在しない",
    "増えない",
    "原因とならない",
    "原因でない",
    "指標とならない",
    "特徴としない",
    "合併しない",
    "関与しない",
    "受けていない",
    "によらない",
    "行われない",
    "みられない",
    "認めにくい",
    "できない",
    "きたさない",
    "起こらない",
    "起こしにくい",
    "起こりにくい",
    "減弱しない",
    "関係ない",
    "関係のない",
    "関連の低い",
    "使用しない",
    "含まれない",
    "含まれないもの",
    "含まれないの",
    "でないのは",
    "有用でない",
    "ない経穴",
    "診断できない",
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_patch_entries(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    return []


def normalize_question_ids(data: dict) -> None:
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        return
    for question in questions:
        if not isinstance(question, dict):
            continue
        if not question.get("original_question_id") and question.get("public_question_id"):
            question["original_question_id"] = question["public_question_id"]


def build_patch_map_from_paths(
    patch_paths: Iterable[Path],
    *,
    value_key: str | None = None,
def patch_target_id(question: Mapping[str, Any]) -> str:
    return review_question_id(question)


    key_fields: Iterable[str] = ("original_question_id",),
) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {}
    for patch_path in patch_paths:
        patch_data = load_json(patch_path)
        for entry in extract_patch_entries(patch_data):
            key_value = None
            for key_field in key_fields:
                value = entry.get(key_field)
                if value:
                    key_value = str(value)
                    break
            if key_value is None:
                continue
            mapping[key_value] = entry if value_key is None else entry.get(value_key)
    return mapping


def apply_question_type(
    data: dict,
    qtype_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        patch_entry = qtype_map.get(str(question_id))
        if isinstance(patch_entry, dict):
            changed = False
            new_type = patch_entry.get("questionType")
            if new_type is not None and question.get("questionType") != new_type:
                question["questionType"] = new_type
                changed = True
            new_body = patch_entry.get("questionBodyText")
            if new_body is not None and question.get("questionBodyText") != new_body:
                question["questionBodyText"] = new_body
                changed = True
            new_choices = patch_entry.get("choiceTextList")
            if new_choices is not None and question.get("choiceTextList") != new_choices:
                question["choiceTextList"] = new_choices
                changed = True
            if changed:
                updated += 1
            continue
        new_type = patch_entry
        if new_type is not None:
            question["questionType"] = new_type
            updated += 1
            continue

        # 追加: choiceTextListが全て空欄ならgroup_choiceにする
        choice_list = question.get("choiceTextList")
        if isinstance(choice_list, list) and all((c is None or str(c).strip() == "") for c in choice_list):
            question["questionType"] = "group_choice"
            updated += 1
            continue
    return updated


def apply_explanation_fields(
    data: dict,
    explanation_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = explanation_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in EXPLANATION_FIELDS:
            if field in entry and entry[field] is not None:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_question_set(
    data: dict,
    question_set_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        new_value = question_set_map.get(str(question_id))
        if new_value is None:
            continue
        question["questionSetId"] = new_value
        updated += 1
    return updated


def apply_correct_choice(
    data: dict,
    correct_choice_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        new_value = correct_choice_map.get(str(question_id))
        if new_value is None:
            continue
        question["correctChoiceText"] = new_value
        updated += 1
    return updated


def apply_answer_result_overrides(
    data: dict,
    override_map: Mapping[str, dict],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        entry = override_map.get(str(question_id))
        if not isinstance(entry, dict):
            continue
        for field in (
            "answer_result_text",
            "answer_result_inferred_correct_choice_numbers",
            "manualQuestionIntentOverride",
        ):
            if field in entry and entry[field] is not None and question.get(field) != entry[field]:
                question[field] = entry[field]
                updated += 1
    return updated


def apply_question_intent(
    data: dict,
    question_intent_map: Mapping[str, Any],
) -> int:
    normalize_question_ids(data)
    updated = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("question_bodies が見つかりません")
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = patch_target_id(question)
        if not question_id:
            continue
        new_value = question_intent_map.get(str(question_id))
        if new_value is None:
            continue
        question["questionIntent"] = new_value
        updated += 1
    return updated


FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
ANSWER_RESULT_RE = re.compile(r"正解は\s*([0-9０-９]+(?:\s*,\s*[0-9０-９]+)*)\s*です。")


def parse_answer_numbers(answer_result_text: Any) -> list[int]:
    text = str(answer_result_text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
    match = ANSWER_RESULT_RE.search(text)
    if not match:
        return []
    numbers: list[int] = []
    for part in match.group(1).split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        n = int(part)
        if n not in numbers:
            numbers.append(n)
    return numbers


def infer_question_intent_from_text(question_body_text: Any) -> str | None:
    text = str(question_body_text or "").strip()
    if not text:
        return None

    positive_select_keywords = (
        "使用しない機材",
    )
    positive_required_keywords = (
        "見落としてはならない",
        "見逃してはならない",
        "しなければならない",
        "行わなければならない",
        "伝えなければならない",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    intent_text = text[-240:]
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if "どれか" not in line and "選べ" not in line:
            continue
        focus_end = max(line.rfind("どれか"), line.rfind("選べ"))
        if focus_end >= 0:
            focus_start = max(0, focus_end - 48)
            focus_text = line[focus_start : focus_end + 3]
        else:
            focus_text = line
        if any(phrase in focus_text for phrase in NEGATIVE_PROMPT_PHRASES):
            intent_text = focus_text
            break
        if not line.startswith(("のは", "は")) and len(line) > 8:
            intent_text = focus_text
            break
        previous = lines[index - 1] if index > 0 else ""
        intent_text = f"{previous}\n{focus_text}".strip() or focus_text
        break

    if any(keyword in intent_text for keyword in positive_required_keywords):
        return "select_correct"
    if any(keyword in intent_text for keyword in positive_select_keywords):
        return "select_correct"
    if any(phrase in intent_text for phrase in NEGATIVE_PROMPT_PHRASES):
        return "select_incorrect"
    return "select_correct"


def normalize_true_false_intent_and_correct_choice(
    data: dict,
) -> tuple[int, int]:
    """
    questionIntent / correctChoiceText を一次情報から整合する。

    設計変更（ユーザー要望）:
      - correctChoiceText は answer_result_text（優先）/ answer_result_inferred_correct_choice_numbers と questionIntent で決定する。
      - 正解番号が複数ある場合、その件数が「正しい」または「間違い」の件数になる。

    - questionIntent は questionBodyText（無ければ originalQuestionBodyText）から推定して上書き（推定できる場合のみ）
    - correctChoiceText は answer_numbers + questionIntent から再計算して上書き
    """
    normalize_question_ids(data)
    intent_updates = 0
    correct_choice_updates = 0
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        return (0, 0)

    for question in questions:
        if not isinstance(question, dict):
            continue
        question_type = question.get("questionType")
        if question_type == "fill_in_blank":
            continue
        if question_type != "true_false":
            continue

        manual_intent_override = question.get("manualQuestionIntentOverride") is True
        if manual_intent_override:
            inferred_intent = None
        else:
            inferred_intent = infer_question_intent_from_text(
                question.get("questionBodyText") or question.get("originalQuestionBodyText") or ""
            )
            if inferred_intent and question.get("questionIntent") != inferred_intent:
                question["questionIntent"] = inferred_intent
                intent_updates += 1

        intent = question.get("questionIntent")
        if intent not in {"select_correct", "select_incorrect"}:
            continue

        answer_numbers = parse_answer_numbers(question.get("answer_result_text"))
        if not answer_numbers:
            inferred_numbers = question.get("answer_result_inferred_correct_choice_numbers")
            if isinstance(inferred_numbers, list) and inferred_numbers:
                answer_numbers = []
                for v in inferred_numbers:
                    if isinstance(v, int):
                        answer_numbers.append(v)
                    elif str(v).isdigit():
                        answer_numbers.append(int(str(v)))
                # 重複除外
                normalized: list[int] = []
                for n in answer_numbers:
                    if n >= 1 and n not in normalized:
                        normalized.append(n)
                answer_numbers = normalized
        if not answer_numbers:
            continue

        choice_count = None
        choice_list = question.get("choiceTextList")
        if isinstance(choice_list, list) and choice_list:
            choice_count = len(choice_list)
        elif isinstance(question.get("correctChoiceText"), list) and question.get("correctChoiceText"):
            choice_count = len(question.get("correctChoiceText"))
        if not choice_count:
            continue
        if any((n < 1 or n > choice_count) for n in answer_numbers):
            continue

        if intent == "select_incorrect":
            expected = ["正しい"] * choice_count
            for n in answer_numbers:
                expected[n - 1] = "間違い"
        else:
            expected = ["間違い"] * choice_count
            for n in answer_numbers:
                expected[n - 1] = "正しい"

        current = question.get("correctChoiceText")
        if current != expected:
            question["correctChoiceText"] = expected
            correct_choice_updates += 1
        if manual_intent_override:
            question.pop("manualQuestionIntentOverride", None)

    return (intent_updates, correct_choice_updates)


def materialize_view_files(
    source_paths: Iterable[Path],
    output_dir: Path,
    *,
    qtype_map: Mapping[str, Any] | None = None,
    explanation_map: Mapping[str, dict] | None = None,
    question_set_map: Mapping[str, Any] | None = None,
    correct_choice_map: Mapping[str, Any] | None = None,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    for source_path in source_paths:
        data = load_json(source_path)
        if qtype_map:
            apply_question_type(data, qtype_map)
        if explanation_map:
            apply_explanation_fields(data, explanation_map)
        if question_set_map:
            apply_question_set(data, question_set_map)
        if correct_choice_map:
            apply_correct_choice(data, correct_choice_map)

        output_path = output_dir / source_path.name
        valid_data, manual_data = maybe_split_for_manual_output(data, output_path)
        save_json(valid_data, output_path)
        if manual_data:
            manual_path = build_manual_output_path(output_path)
            save_json(manual_data, manual_path)
        written[source_path.name] = output_path

    return written
