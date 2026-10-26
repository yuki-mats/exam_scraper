"""図表を含む一問の候補領域を原画像と照合して特定する。内容は書き換えない。"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from scripts.scrape.knoun_images import BOOK_ID, REPO_ROOT, atomic_json, now, read_manifest
from scripts.scrape.knoun_review import load_pair, reviewed_pair, validate_pair_receipt
from tools.question_review_console.codex_app_server import CodexAppServerClient
from tools.question_review_console.turn_budget import GlobalTurnBudget


def binding(record, question, explanation):
    content = json.dumps({"question": question, "explanation": explanation}, ensure_ascii=False, sort_keys=True)
    return {"sourceQuestionId": record["sourceQuestionId"],
            "questionImageSha256": record["questionImage"]["sha256"],
            "explanationImageSha256": record["explanationImage"]["sha256"],
            "transcriptionSha256": hashlib.sha256(content.encode()).hexdigest()}


def layout_path(root, group, source_id):
    return root / "source_layouts" / f"knoun-2026-mock-{group}" / (Path(source_id).stem + ".json")


def validate_layout(value, record, question, explanation):
    if value.get("schemaVersion") != "knoun-source-layout/v1" or value.get("binding") != binding(record, question, explanation):
        raise ValueError("図表の選択肢領域と画像・転記の対応が一致しません")
    candidate = value["candidate"]
    if any(candidate.get(k) != v for k, v in value["binding"].items()):
        raise ValueError("図表の領域候補が別問題の識別情報を返しました")
    method = value.get("reviewMethod", "model_image_pair")
    if method == "manual_visual_pair":
        review = value.get("manualReview", {})
        if (not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
                or not review.get("reviewedAt")
                or any(review.get(key) != expected for key, expected in value["binding"].items())):
            raise ValueError("手動の領域照合記録と当該画像対・転記が一致しません")
    elif method == "model_image_pair":
        validate_pair_receipt(record, value["receipt"])
    else:
        raise ValueError("領域照合の方法が未確認です")
    if candidate.get("reviewOutcome") != "verified" or not all(candidate.get("checks", {}).get(k) is True for k in ("allChoices", "onlyChoiceRegion", "answerTarget")):
        raise ValueError("図表を含む問題の選択肢領域が未確認です")
    from scripts.scrape.knoun import parse_transcription
    parse_transcription(question, explanation, choice_block=candidate["choiceBlock"])


def load_layout(root, group, record, question, explanation):
    path = layout_path(root, group, record["sourceQuestionId"])
    if not path.exists():
        raise ValueError("図表を含む問題は原画像を保持し、選択肢領域を照合してから登録してください")
    value = json.loads(path.read_text())
    if value.get("status") != "validated_source_layout":
        raise ValueError("図表を含む問題の選択肢領域は未確定です")
    validate_layout(value, record, question, explanation)
    return value


def save_manual_layout(root, group, source_id, *, question_hash, explanation_hash,
                       choice_block, reviewer, checks):
    """Save one explicitly witnessed pair, without inventing a model receipt."""
    record, question, explanation = reviewed_pair(root, group, source_id)
    if (record["questionImage"]["sha256"], record["explanationImage"]["sha256"]) != (question_hash, explanation_hash):
        raise ValueError("目視した画像対のhashと一致しません")
    bound = binding(record, question, explanation)
    value = {"schemaVersion": "knoun-source-layout/v1", "createdAt": now(),
             "reviewMethod": "manual_visual_pair", "binding": bound,
             "candidate": bound | {"reviewOutcome": "verified", "choiceBlock": choice_block,
                                   "checks": dict(checks), "notes": []},
             "manualReview": bound | {"reviewer": reviewer, "reviewedAt": now()}}
    validate_layout(value, record, question, explanation)
    value["status"] = "validated_source_layout"
    path = layout_path(root, group, source_id)
    attempts = path.parent / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    if path.exists():
        atomic_json(attempts / f"{path.stem}_prior_{uuid4().hex}.json", json.loads(path.read_text()))
    atomic_json(attempts / f"{path.stem}_manual_{uuid4().hex}.json", value)
    atomic_json(path, value)
    return value


PROMPT = """原画像の一問を構造化する読取処理です。添付1枚目が問題、2枚目が当該問題の解答・解説です。
入力の文字起こしは画像と照合済みです。図表の前後に置かれた「今回の全選択肢」の領域を、
文字起こし内の一つの連続部分として特定し、choiceBlockへそのまま返してください。
印字された最初の候補番号①や⑯から、最後の候補の末尾までを含めます。語群見出し、設問文、
「①〜④の記述」のような範囲指定、共有図表のラベルや出典、赤字の回答対象の指定は含めません。
ただし、各選択肢自身に添付されたマーク・図の中の文字は、その候補の一部として残します。
候補文の間の「【図中】JAS」などを取り除いて連結してはいけません。転記内の連続領域を選びます。
番号と本文は修正・補完せず、意味を変えず、全候補を一つずつ原画像で読みます。段組の候補は
入力転記にある番号順の並びを用います。図表や本文を丸ごとchoiceBlockへ入れません。
文字起こしの表記を変えず、単なる折返しの改行・空白の違いだけを許容します。入力に存在しない
候補を作らず、正答の事実修正や説明の改善もしません。赤字等の回答対象を確認し、別欄へ広げません。
原画像と転記の不一致や領域の曖昧さがあればneeds_manual_reviewとし、理由をnotesへ書きます。
外部検索、command、file操作、subagentを使わず、指定JSONだけを返してください。
入力JSON:
"""


def create_layout(client, root, group, source_id):
    record, question, explanation = reviewed_pair(root, group, source_id)
    bound = binding(record, question, explanation)
    properties = {key: {"type": "string", "enum": [v]} for key, v in bound.items()}
    properties.update({"reviewOutcome": {"type": "string", "enum": ["verified", "needs_manual_review"]},
                       "choiceBlock": {"type": "string"}, "notes": {"type": "array", "items": {"type": "string"}},
                       "checks": {"type": "object", "additionalProperties": False,
                                  "properties": {k: {"type": "boolean"} for k in ("allChoices", "onlyChoiceRegion", "answerTarget")},
                                  "required": ["allChoices", "onlyChoiceRegion", "answerTarget"]}})
    schema = {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}
    images_root = root / "question_images" / f"knoun-2026-mock-{group}"
    result = client.run_turn(PROMPT + json.dumps(bound | {"questionText": question, "explanationText": explanation}, ensure_ascii=False),
        work_type="maintenance_knoun_source_layout_candidate", sandbox="read-only", emit=lambda _: None,
        image_paths=[images_root / record[k]["relativePath"] for k in ("questionImage", "explanationImage")],
        output_schema=schema, turn_group="eco-kentei-source-layout")
    value = {"schemaVersion": "knoun-source-layout/v1", "createdAt": now(), "binding": bound,
             "candidate": json.loads(result.final_message), "receipt": asdict(result), "status": "unvalidated"}
    target = layout_path(root, group, source_id)
    attempt = target.parent / "attempts" / (target.stem + "_" + uuid4().hex + ".json")
    atomic_json(attempt, value)
    validate_layout(value, record, question, explanation)
    current, current_q, current_a = reviewed_pair(root, group, source_id)
    if binding(current, current_q, current_a) != bound:
        raise ValueError("領域照合中に画像又は転記が変わりました")
    value["status"] = "validated_source_layout"
    atomic_json(target, value)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", nargs="+", type=int, default=list(range(1, 6)))
    parser.add_argument("--source-id")
    parser.add_argument("--max-questions", type=int)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--wait-console-url")
    args = parser.parse_args(argv)
    if not set(args.groups) <= set(range(1, 6)):
        parser.error("groupsは1〜5で指定してください")
    root = REPO_ROOT / "output/eco-kentei"
    targets = []
    observed = set()
    for group in args.groups:
        manifest = read_manifest(root / "question_images" / f"knoun-2026-mock-{group}" / "capture_manifest.json", group, BOOK_ID)
        observed.update(manifest["records"])
        for sid in manifest["records"]:
            if args.source_id and sid != args.source_id:
                continue
            record, drafts = load_pair(root, group, sid, verified_manifest=manifest)
            if not all(d[1].get("visualReview", {}).get("questionImageContent") == "contains_figure" for d in drafts.values()):
                continue
            q, a = (drafts[k][1]["verifiedText"] for k in ("questionImage", "explanationImage"))
            try:
                load_layout(root, group, record, q, a)
            except (ValueError, FileNotFoundError):
                targets.append((group, sid))
    if args.source_id and args.source_id not in observed:
        parser.error("取得manifestに存在しないsource IDです")
    if args.max_questions is not None:
        if args.max_questions < 1:
            parser.error("max-questionsは1以上です")
        targets = targets[:args.max_questions]
    print(f"[LAYOUT_PLAN] pending={len(targets)} concurrency=1 run={args.run}", flush=True)
    if not args.run:
        return 0
    lock = (root / ".visual_review.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = CodexAppServerClient(REPO_ROOT, turn_budget=GlobalTurnBudget(1))
    report = {"createdAt": now(), "targetCount": len(targets), "status": "running", "holds": [], "completed": []}
    report_path = root / "reports" / ("source_layout_" + uuid4().hex + ".json")
    atomic_json(report_path, report)
    provider_failures = 0
    try:
        for group, sid in targets:
            report.update(currentSourceQuestionId=sid, heartbeatAt=now())
            atomic_json(report_path, report)
            if args.wait_console_url:
                from scripts.scrape.knoun_visual_review import wait_for_console_idle
                wait_for_console_idle(args.wait_console_url)
            try:
                path = create_layout(client, root, group, sid)
                report["completed"].append(sid)
                provider_failures = 0
                print(f"[LAYOUT_OK] {Path(sid).stem}: {path}", flush=True)
            except Exception as exc:
                report["holds"].append({"sourceQuestionId": sid, "reason": str(exc)})
                provider_failures = 0 if isinstance(exc, ValueError) else provider_failures + 1
                print(f"[LAYOUT_HOLD] {Path(sid).stem}: {type(exc).__name__}", flush=True)
            atomic_json(report_path, report)
            if provider_failures >= 3:
                raise RuntimeError("読取基盤の連続3回失敗。確定済み領域を保持して停止します")
        report.update(status="finished_with_holds" if report["holds"] else "selected_scope_complete", finishedAt=now())
        atomic_json(report_path, report)
    except BaseException as exc:
        report.update(status="interrupted", error=str(exc), finishedAt=now())
        atomic_json(report_path, report)
        raise
    finally:
        client.close()
        lock.close()
    return int(bool(report["holds"]))


if __name__ == "__main__":
    raise SystemExit(main())
