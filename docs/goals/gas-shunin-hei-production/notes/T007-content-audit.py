"""主担当が直接書いた丙種候補を、一問ずつ別のread-only sessionで確認する。"""
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

REVISION = sys.argv[2] if len(sys.argv) > 2 else "r1"

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
sys.path.insert(0, str(RUN / "tool_snapshot"))
from tools.question_review_console.codex_app_server import (
    CodexAppServerClient, QUESTION_MAINTENANCE_AUDIT_MODEL,
)
from tools.question_review_console.inventory import QuestionInventory


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def review(candidate):
    records = json.loads((RUN / "records.json").read_text())
    taxonomy = json.loads((ROOT / "output/gas-shunin-hei/category/category.json").read_text())
    key = candidate["sourceQuestionId"]
    packet = records[key]
    current = QuestionInventory(ROOT).projected_input("gas-shunin-hei", key.split(":")[0], packet["sourceIdentity"]["sourceRecordRef"])
    if current.errors:
        raise ValueError(current.errors)
    packet["record"] = current.record
    packet["recordHash"] = hashlib.sha256(json.dumps(current.record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    year = key.split(":")[0]
    directory = RUN / "content_audits" / ((key.replace(":", "_")) if REVISION == "r1" else key.replace(":", "_") + "_" + REVISION)
    directory.mkdir(parents=True, exist_ok=True)
    pages = sorted(set(packet["questionPages"]) | set(candidate["officialPagesVisuallyReviewed"]))
    paths = [ROOT / f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{page:02}.png"
             for page in pages]
    paths.append(ROOT / f"output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png")
    images = [{"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
              for path in paths]
    bundle = {"candidate": candidate, "fixedQuestion": packet, "category": taxonomy, "images": images}
    input_hash = sha(bundle)
    prompt = (
        "主担当が直接作成した一問の問題・正答・解説・分類の独立内容監査である。"
        "ファイル、Git、外部状態を変更せず、subagentを使わない。"
        "添付した当該年度丙種の公式問題頁と正答表を読み、科目・問番号を照合する。"
        "各選択肢を完全命題として独立判定し、公式番号を各肢へ機械的に割り当てない。"
        "choiceJudgments.verdictは選択肢自身の完全命題の真偽であり、候補解説やrationaleの正しさではない。"
        "例えば、rationaleが『この候補は条件を満たさず誤り』ならverdictは『間違い』とする。"
        "組合せでは元の組合せ表と各memberを確認する。非memberの真偽を公式番号だけから補完しない。"
        "計算は問題の条件から独立に式・単位・代入・結果を導き、候補の理由も検証する。"
        "標準状態の温度・圧力をその問題の指定どおりに扱う。"
        "説明は初学者に十分か、日本語が自然か、数式のdelimiterと変数定義が適切かも確認する。"
        "共通03仕様ではtrue_falseは各肢の解説、flash_card・group_choiceは共通解説を1本とし、"
        "共通解説は必ず『正しい。』で始める。この必須接頭辞自体は文章不備ではない。"
        "分類は固定categoryのdescriptionと全選択肢から確かめる。"
        "候補がquestionType・isCalculationQuestion・questionIntentを指定している場合は、元の値を根拠にせず、"
        "回答操作・計算の要否・設問方向から独立に検証する。不適切なものはneeds_reworkにする。"
        "法令は現在と出題時の一次根拠を確認し、資料不足はholdにする。"
        "根拠なく既存候補を承認しない。全肢の確認を終えて指定JSONだけを返す。\n"
        "explanationScoreは100点満点（90点以上が合格、十分な説明は95〜100点）。5段階評価ではない。\n"
        + json.dumps(bundle, ensure_ascii=False) + "\ninputHash=" + input_hash
    )
    judgment = object_schema({"verdict": {"type": "string", "enum": ["正しい", "間違い"]},
                             "rationale": {"type": "string"}})
    choices = {sha(text): judgment for text in packet["record"]["choiceTextList"]}
    schema = object_schema({
        "sourceQuestionId": {"type": "string", "const": key},
        "inputHash": {"type": "string", "const": input_hash},
        "decision": {"type": "string", "enum": ["approve", "needs_rework", "hold"]},
        "officialAnswerNumber": {"type": "integer"},
        "choiceJudgments": object_schema(choices),
        "explanationScore": {"type": "integer", "minimum": 0, "maximum": 100},
        "issues": {"type": "array", "items": {"type": "string"}},
        "classificationApproved": {"type": "boolean"},
        "classificationRationale": {"type": "string"},
        "evidenceSummary": {"type": "string"},
    })
    fixed = {"bundle": bundle, "inputHash": input_hash, "prompt": prompt, "schema": schema}
    fixed_path = directory / "input.json"
    if fixed_path.exists() and json.loads(fixed_path.read_text()) != fixed:
        raise ValueError("固定inputが変わった候補は別のaudit directoryで再確認する")
    fixed_path.write_text(json.dumps(fixed, ensure_ascii=False, indent=2) + "\n")
    result_path = directory / "native.json"
    if result_path.exists():
        native = json.loads(result_path.read_text())
    else:
        client = CodexAppServerClient(ROOT)
        try:
            native = dataclasses.asdict(client.run_turn(
                prompt, model=QUESTION_MAINTENANCE_AUDIT_MODEL, work_type="official_source_review",
                sandbox="read-only", emit=lambda message: None, image_paths=paths,
                output_schema=schema, cwd=ROOT, turn_group="gas-shunin-hei-direct-audit",
            ))
        finally:
            client.close()
        result_path.write_text(json.dumps(native, ensure_ascii=False, indent=2) + "\n")
    if native.get("changed_files") or native.get("subagent_thread_ids"):
        raise ValueError("read-only監査境界を超えた")
    if not all(native.get(name) for name in ("session_id", "thread_id", "turn_id")):
        raise ValueError("実session証跡がない")
    result = json.loads(native["final_message"])
    if len(packet["record"]["choiceTextList"]) != len(candidate["correctChoiceText"]):
        raise ValueError("同一問の選択肢と判定の件数が一致しない")
    expected = {sha(text): verdict for text, verdict in zip(packet["record"]["choiceTextList"], candidate["correctChoiceText"])}
    agreement = all(result["choiceJudgments"][key_hash]["verdict"] == verdict for key_hash, verdict in expected.items())
    accepted = (result["inputHash"] == input_hash and result["sourceQuestionId"] == key
                and result["decision"] == "approve" and result["classificationApproved"]
                and result["explanationScore"] >= 90 and not result["issues"]
                and result["officialAnswerNumber"] == candidate["officialAnswerNumber"] and agreement)
    output = {"inputHash": input_hash, "accepted": accepted, "review": result,
              "nativePath": str(result_path.relative_to(ROOT)),
              "nativeSha256": hashlib.sha256(result_path.read_bytes()).hexdigest()}
    (directory / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(key, "accepted" if accepted else "needs review", result["issues"], flush=True)
    return output


if __name__ == "__main__":
    candidates = json.loads(Path(sys.argv[1]).read_text())
    with ThreadPoolExecutor(max_workers=min(5, len(candidates))) as pool:
        results = list(pool.map(review, candidates))
    print("accepted", sum(result["accepted"] for result in results), "/", len(results), flush=True)
