"""目視照合済みのKnoun転記だけを標準00_sourceへ登録する（ブラウザ不使用）。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from scripts.scrape.common import load_local_secure_env, make_public_question_id, make_storage_url
from scripts.scrape.knoun_images import BOOK_ID, QUALIFICATION, REPO_ROOT, atomic_json, now, read_manifest
from scripts.scrape.knoun_review import load_pair, reviewed_pair

CIRCLED = "".join(chr(point) for point in (*range(0x2460, 0x2474), *range(0x3251, 0x3260), *range(0x32B1, 0x32C0)))


def _answer_word(text: str) -> str:
    # 正答と同じ行に印刷された編集注記は、原文の解答欄へ残したまま語句と分ける。
    return re.sub(r"\s*[〈＜<]テキスト外[〉＞>]\s*$", "", text).strip()


def _numbered_choices(text: str) -> tuple[list[str], list[str]]:
    pattern = r"^([" + CIRCLED + r"])(?![ \t]*[〜～~])([^\n]*(?:\n(?![" + CIRCLED + r"])[^\n]*)*)"
    numbered = re.findall(pattern, text, re.M)
    labels, choices = [], []
    for label, value in numbered:
        if not value.strip() or (labels and CIRCLED.index(label) != CIRCLED.index(labels[-1]) + 1):
            raise ValueError("選択肢の丸数字又は順序が不正です")
        labels.append(label)
        choices.append(value.strip())
    if not choices:
        raise ValueError("番号付きの選択肢を読み取れません")
    return labels, choices


def _confirmed_word_discrepancy(label: str, printed: str, choice: str, review: dict | None) -> dict:
    expected = {"status": "confirmed_source_discrepancy", "printedLabel": label,
                "printedAnswerWord": printed, "printedChoiceText": choice}
    if not isinstance(review, dict) or any(review.get(k) != v for k, v in expected.items()) or not review.get("referenceUrls"):
        raise ValueError("同じ問題内で正答記号と正答語句が一致しません")
    # 原画像に実在すると確認した不一致を保存する。正答の文字列を補正しない。
    return {"sourceAnswerWordMismatch": {k: expected[k] for k in ("printedLabel", "printedAnswerWord", "printedChoiceText")}}


def locate_choice_block(question: str, choice_block: str) -> tuple[str, str]:
    """元転記の一意な文字spanだけを選択肢へ分離する。文字は修正しない。"""
    positions = [i for i, char in enumerate(question) if not char.isspace()]
    compact = "".join(question[i] for i in positions)
    selected = re.sub(r"\s+", "", choice_block)
    if not selected or compact.count(selected) != 1:
        raise ValueError("選択肢blockを当該転記の一意なspanへ対応できません")
    start = compact.index(selected)
    source_start, source_end = positions[start], positions[start + len(selected) - 1] + 1
    block = question[source_start:source_end]
    if not re.match(r"^[" + CIRCLED + r"](?![ \t]*[〜～~])", block):
        raise ValueError("選択肢blockは印字された候補番号から始めてください")
    prefix = re.sub(r"(?:【語群】|［語群］|\[語群\])\s*$", "", question[:source_start]).rstrip()
    suffix = question[source_end:].lstrip()
    body = "\n".join(part for part in (prefix, suffix) if part)
    if not body:
        raise ValueError("問題文を残さずに選択肢blockを指定しています")
    return body, block


def parse_transcription(question: str, explanation: str, *, choice_block: str | None = None,
                        answer_word_review: dict | None = None) -> dict:
    """当該一問の転記内の明示記号だけを読む。未対応の形式は推測しない。"""
    answer = re.search(r"^正答[・･.…⋯\s]*([" + CIRCLED + r"])([^\n]*)$", explanation, re.M)
    if not answer:
        raise ValueError("一問の明示正答記号を読み取れません")
    answer_number = CIRCLED.index(answer[1]) + 1
    answer_word = _answer_word(answer[2])
    extra = {}
    pool = re.search(r"(?:【語群】|［語群］|\[語群\])\n", question)
    if choice_block is not None:
        body, block = locate_choice_block(question, choice_block)
        labels, choices = _numbered_choices(block)
        if answer[1] not in labels:
            raise ValueError("正答番号が当該選択肢block外です")
        answer_number = labels.index(answer[1]) + 1
        if answer_word and re.sub(r"\s+", "", answer_word) != re.sub(r"\s+", "", choices[answer_number - 1]):
            # 画像選択肢には、図中の短いcaptionとは別の説明が解答欄に印字される。
            # 印字番号・図中文字・解答の説明を独立に残し、内容判断は05/02aへ渡す。
            extra["sourceAnswerDescription"] = {"printedLabel": answer[1], "printedAnswerWord": answer_word,
                                                "printedChoiceText": choices[answer_number - 1]}
        if labels[0] != "①":
            extra.update({"sourceChoiceLabels": labels, "sourceSelectedChoiceLabel": answer[1]})
    elif pool:
        body, tail = question[:pool.start()].rstrip(), question[pool.end():]
        footer = re.search(r"^(?:［[ア-ン]］|「[ア-ン]」|\[[ア-ン]\])にあてはまる最も適切な語句の番号を以下に入力してください。$", tail, re.M)
        if not footer or tail[footer.end():].strip():
            raise ValueError("語群の解答対象欄が一意に確定しません")
        labels, choices = _numbered_choices(tail[:footer.start()])
        if answer[1] not in labels:
            raise ValueError("正答番号が語群外です")
        # 印字番号⑯などを当該語群内の位置へ対応させる。別問の配列順は使わない。
        answer_number = labels.index(answer[1]) + 1
        if re.sub(r"\s+", "", answer_word) != re.sub(r"\s+", "", choices[answer_number - 1]):
            extra.update(_confirmed_word_discrepancy(answer[1], answer_word, choices[answer_number - 1], answer_word_review))
        if labels[0] != "①":
            extra.update({"sourceChoiceLabels": labels, "sourceSelectedChoiceLabel": answer[1]})
        body += "\n" + footer[0]
    elif "内容が正しいものには①を、誤っているものには②を選びなさい。" in question:
        if answer_word or answer_number not in (1, 2):
            raise ValueError("正誤問題の明示解答が不正です")
        body, choices = question, ["正しい", "誤っている"]
    else:
        first_choice = re.search(r"^①(?![ \t]*[〜～~])[^\n]+", question, re.M)
        if not first_choice:
            raise ValueError("未対応の転記形式です。一問の原画像と構造を確認してください")
        body, tail = question[:first_choice.start()].rstrip(), question[first_choice.start():]
        labels, choices = _numbered_choices(tail)
        if labels[0] != "①" or answer[1] not in labels:
            raise ValueError("正答番号が選択肢外です")
        answer_number = labels.index(answer[1]) + 1
        if answer_word and re.sub(r"\s+", "", answer_word) != re.sub(r"\s+", "", choices[answer_number - 1]):
            extra.update(_confirmed_word_discrepancy(answer[1], answer_word, choices[answer_number - 1], answer_word_review))
    return {"questionBodyText": body, "choiceTextList": choices,
            "answer_result_text": answer[0],
            "answer_result_inferred_correct_choice_numbers": [answer_number],
            "explanation_common_prefix": [explanation[answer.end():].strip()] if explanation[answer.end():].strip() else [],
            "explanation_common_summary": [], "explanation_choice_snippets": [[] for _ in choices], **extra}


def source_record(root: Path, group: int, source_id: str, *, verified_manifest: dict | None = None) -> dict:
    record, question, explanation = reviewed_pair(root, group, source_id, verified_manifest=verified_manifest)
    _, drafts = load_pair(root, group, source_id, verified_manifest=verified_manifest)
    content_types = {d[1]["visualReview"].get("questionImageContent") for d in drafts.values()}
    if content_types not in ({"text_only"}, {"contains_figure"}):
        raise ValueError("問題画像の図表有無が未分類又は不一致です")
    has_figure = content_types == {"contains_figure"}
    answer_reviews = [draft[1]["visualReview"].get("sourceAnswerWordReview") for draft in drafts.values()]
    if answer_reviews[0] != answer_reviews[1]:
        raise ValueError("問題と解説の原文不一致の確認記録が対応していません")
    layout = None
    if has_figure:
        from scripts.scrape.knoun_source_layout import load_layout
        layout = load_layout(root, group, record, question, explanation)
    result = parse_transcription(question, explanation, choice_block=layout["candidate"]["choiceBlock"] if layout else None,
                                 answer_word_review=answer_reviews[0])
    public_id = make_public_question_id(source_id)
    result.update({
        "examLabel": f"eco検定 2026年版 模擬問題{group}",
        "questionLabel": Path(source_id).stem,
        "list_group_id": f"knoun-2026-mock-{group}", "source_list_group_id": str(group),
        "question_url": source_id, "source_question_id": source_id,
        "public_question_id": public_id, "original_question_id": public_id,
        "source_public_question_id": public_id, "questionSourceSite": "knoun-jp",
        "question_id_policy_key": "source-question-id:hmac:v1", "question_id_policy_version": 1,
        "question_id_source_key_description": "stable Knoun question frame URL",
        "sourceUniqueKeys": [f"{source_id}:s{i:02d}" for i in range(1, len(result["choiceTextList"]) + 1)],
        # 文字だけのスキャンは転記の証跡。公開設問に必要な図表としては登録しない。
        "questionImageSourceUrls": [], "questionImageStorageUrls": [],
        "originalQuestionChoiceImageUrls": [[] for _ in result["choiceTextList"]],
        "explanationImageSourceUrls": [], "explanationImageStorageUrls": [],
        "sourceTranscription": {
            "questionText": question, "explanationText": explanation,
            "questionImage": record["questionImage"], "explanationImage": record["explanationImage"],
            "originalizationRequired": True,
        },
    })
    if has_figure:
        result["questionImageSourceUrls"] = [record["questionImage"]["sourceUrl"]]
        result["questionImageStorageUrls"] = [make_storage_url(record["questionImage"]["filename"], QUALIFICATION)]
        result["sourceTranscription"]["figurePreserved"] = True
        result["sourceTranscription"]["layoutBinding"] = layout["binding"]
    return result


def import_group(output: Path, group: int, max_questions: int | None = None, *, source_ids: list[str] | None = None) -> dict:
    root = output / QUALIFICATION
    group_id = f"knoun-2026-mock-{group}"
    manifest = read_manifest(root / "question_images" / group_id / "capture_manifest.json", group, BOOK_ID)
    ids = sorted(manifest["records"], key=lambda value: int(Path(value).stem.split("_")[-1]))
    if source_ids is not None:
        if max_questions is not None or not source_ids or len(source_ids) != len(set(source_ids)) or not set(source_ids) <= set(ids):
            raise ValueError("明示対象の問題IDが空・重複・対象外、又は上限指定と併用されています")
        ids = list(source_ids)
    elif max_questions is not None:
        if max_questions < 1 or max_questions > len(ids):
            raise ValueError("取得上限は1〜取得済み件数で指定してください")
        ids = ids[:max_questions]
    elif manifest.get("status") != "images_complete" or len(ids) != manifest["expectedCount"]:
        raise ValueError("原画像の全件取得が未完了です")
    source_dir = root / "questions_json" / group_id / "00_source"
    # 選択した全問題を検証してから書く。未照合の問で失敗した際に途中登録しない。
    pending = []
    existing_ids = set()
    for path in source_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        bodies = payload.get("question_bodies", [])
        if len(bodies) != 1:
            raise ValueError("既存Knoun sourceは一問一ファイルが必要です")
        source_id = bodies[0].get("source_question_id")
        if (source_id not in manifest["records"] or source_id in existing_ids
                or path.name != f"question_{Path(source_id).stem}.json"):
            raise ValueError("既存sourceのID、ファイル名又は取得manifestとの対応が不正です")
        existing_ids.add(source_id)
    for source_id in ids:
        value = {"question_bodies": [source_record(root, group, source_id, verified_manifest=manifest)]}
        target = source_dir / f"question_{Path(source_id).stem}.json"
        if target.exists():
            if json.loads(target.read_text(encoding="utf-8")) != value:
                raise ValueError(f"既存00_sourceと転記が異なります。取得元更新又はsource conflictの確認が必要です: {target.name}")
        else:
            pending.append((target, value))
    for target, value in pending:
        atomic_json(target, value)
    report = {"schemaVersion": "knoun-source-intake/v1", "createdAt": now(),
              "qualification": QUALIFICATION, "listGroupId": group_id,
              "selectedQuestionCount": len(ids), "expectedQuestionCount": manifest["expectedCount"],
              "newQuestionCount": len(pending), "unchangedQuestionCount": len(ids) - len(pending),
              "updatedSourceIds": [], "selectedSourceIds": ids,
              "scope": "full_group" if len(ids) == manifest["expectedCount"] else ("explicit_selection" if source_ids is not None else "limited"),
              "originalizationRequired": True, "status": "selected_scope_complete"}
    atomic_json(root / "reports" / f"knoun_source_intake_{group_id}.json", report)
    return report


def main() -> int:
    group_id = os.environ.get("SCRAPER_OUTPUT_LIST_GROUP_ID", "")
    match = re.fullmatch(r"knoun-2026-mock-([1-5])", group_id)
    if not match or os.environ.get("SCRAPER_QUALIFICATION_CODE", QUALIFICATION) != QUALIFICATION:
        raise ValueError("eco-kenteiのKnoun presetから実行してください")
    load_local_secure_env()
    limit = os.environ.get("SCRAPER_MAX_QUESTIONS")
    report = import_group(Path(os.environ.get("SCRAPER_OUTPUT_DIR", str(REPO_ROOT / "output"))).expanduser().resolve(),
                          int(match[1]), int(limit) if limit else None)
    print(f"[SOURCE_INTAKE] {group_id}: {report['selectedQuestionCount']}問 / scope={report['scope']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
