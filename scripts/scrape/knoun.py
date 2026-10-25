"""目視照合済みのKnoun転記だけを標準00_sourceへ登録する（ブラウザ不使用）。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from scripts.scrape.common import load_local_secure_env, make_public_question_id
from scripts.scrape.knoun_images import BOOK_ID, QUALIFICATION, REPO_ROOT, atomic_json, now, read_manifest
from scripts.scrape.knoun_review import load_pair, reviewed_pair

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def parse_transcription(question: str, explanation: str) -> dict:
    """当該一問の転記内の明示記号だけを読む。未対応の形式は推測しない。"""
    answer = re.search(r"^正答[・.…\s]*([" + CIRCLED + r"])([^\n]*)$", explanation, re.M)
    if not answer:
        raise ValueError("一問の明示正答記号を読み取れません")
    answer_number = CIRCLED.index(answer[1]) + 1
    pool = re.search(r"(?:【語群】|［語群］|\[語群\])\n", question)
    if pool:
        body, tail = question[:pool.start()].rstrip(), question[pool.end():]
        footer = re.search(r"^［([ア-ン])］にあてはまる最も適切な語句の番号を以下に入力してください。$", tail, re.M)
        if not footer or tail[footer.end():].strip():
            raise ValueError("語群の解答対象欄が一意に確定しません")
        choice_lines = [line.strip() for line in tail[:footer.start()].splitlines() if line.strip()]
        choices = []
        for line in choice_lines:
            if line[0] not in CIRCLED or CIRCLED.index(line[0]) != len(choices) or not line[1:].strip():
                raise ValueError("語群の丸数字・順序・本文が不正です")
            choices.append(line[1:].strip())
        if not 1 <= answer_number <= len(choices):
            raise ValueError("正答番号が語群外です")
        if re.sub(r"\s+", "", answer[2]) != re.sub(r"\s+", "", choices[answer_number - 1]):
            raise ValueError("同じ問題内で正答記号と正答語句が一致しません")
        body += "\n" + footer[0]
    elif "内容が正しいものには①を、誤っているものには②を選びなさい。" in question:
        if answer[2].strip() or answer_number not in (1, 2):
            raise ValueError("正誤問題の明示解答が不正です")
        body, choices = question, ["正しい", "誤っている"]
    else:
        first_choice = re.search(r"^①[^\n]+", question, re.M)
        if not first_choice:
            raise ValueError("未対応の転記形式です。一問の原画像と構造を確認してください")
        body, tail = question[:first_choice.start()].rstrip(), question[first_choice.start():]
        numbered = re.findall(r"^([" + CIRCLED + r"])([^\n]*(?:\n(?![" + CIRCLED + r"])[^\n]*)*)", tail, re.M)
        choices = []
        for label, text in numbered:
            if CIRCLED.index(label) != len(choices) or not text.strip():
                raise ValueError("選択肢の丸数字又は順序が不正です")
            choices.append(text.strip())
        if not 1 <= answer_number <= len(choices):
            raise ValueError("正答番号が選択肢外です")
        if answer[2].strip() and re.sub(r"\s+", "", answer[2]) != re.sub(r"\s+", "", choices[answer_number - 1]):
            raise ValueError("同じ問題内で正答記号と正答語句が一致しません")
    return {"questionBodyText": body, "choiceTextList": choices,
            "answer_result_text": answer[0],
            "answer_result_inferred_correct_choice_numbers": [answer_number],
            "explanation_common_prefix": [explanation[answer.end():].strip()] if explanation[answer.end():].strip() else [],
            "explanation_common_summary": [], "explanation_choice_snippets": [[] for _ in choices]}


def source_record(root: Path, group: int, source_id: str, *, verified_manifest: dict | None = None) -> dict:
    record, question, explanation = reviewed_pair(root, group, source_id, verified_manifest=verified_manifest)
    _, drafts = load_pair(root, group, source_id, verified_manifest=verified_manifest)
    if any(d[1]["visualReview"].get("questionImageContent") != "text_only" for d in drafts.values()):
        raise ValueError("図表を含む又は未分類の画像です。図表の保持方法を個別に確認してください")
    result = parse_transcription(question, explanation)
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
