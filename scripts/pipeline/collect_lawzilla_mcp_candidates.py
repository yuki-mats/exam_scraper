#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "lawzilla-mcp-candidate-collection/v1"
JST = timezone(timedelta(hours=9))

GAS_LAW_IDS = {
    "gas_business_act": "329AC0000000051",
    "gas_business_act_order": "329CO0000000068",
    "gas_business_act_regulation": "345M50000400097",
    "gas_facility_technical_standard": "412M50000400111",
    "high_pressure_gas_safety_act": "326AC0000000204",
    "gas_reporting_rule": "429M60000400016",
}

SEARCH_PHRASES = (
    "一般ガス導管事業者",
    "特定ガス導管事業者",
    "認定高度保安実施一般ガス導管事業者",
    "ガス主任技術者",
    "保安規程",
    "工事計画",
    "自主検査",
    "託送供給約款",
    "最終保障供給",
    "災害時連携計画",
    "供給条件",
    "熱量",
    "圧力",
    "燃焼性",
    "ガス事故速報",
    "ガス事故詳報",
    "消費機器",
    "保安業務規程",
    "周知",
    "調査",
    "ガス調理機器",
    "ガス栓",
    "着脱",
    "過流出安全機構",
    "ガス湯沸器",
    "ガスふろがま",
    "排気筒",
    "排気扇",
    "給排気部",
    "有効断面積",
    "密閉燃焼式",
    "自然排気式",
    "強制排気式",
    "外壁",
    "鳥",
    "排気フード",
    "接続部",
    "隙間",
    "屋内",
    "製造所",
    "ガス発生設備",
    "移動式ガス発生設備",
    "附帯設備",
    "製造設備",
    "損傷",
    "計測",
    "確認",
    "置換",
    "供給所",
    "導管",
    "整圧器",
    "遮断装置",
    "ガス漏れ警報",
    "液化ガス用貯槽",
    "主要材料",
    "最高使用温度",
    "最低使用温度",
    "機械的性質",
    "耐圧試験",
    "気密試験",
    "溶接",
    "防食",
    "掘削",
    "特定ガス用品",
    "ガス用品",
    "特定工事",
    "特定ガス消費機器",
    "ガス消費機器設置工事監督者",
    "高圧ガス保安法",
    "ガス製造事業",
    "液化ガス貯蔵設備",
    "遮断",
    "自動的に遮断",
    "十二キロワット",
    "七キロワット",
)

STOP_TERMS = {
    "次",
    "記述",
    "正しい",
    "誤っている",
    "もの",
    "いくつ",
    "ある",
    "こと",
    "ただし",
    "この",
    "その",
    "当該",
    "場合",
    "限る",
    "定める",
    "規定",
    "経済産業省令",
    "経済産業大臣",
    "消費機器",
    "屋内",
}


def now_jst() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_json(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def parse_sse_json(data: bytes) -> dict[str, Any]:
    if not data:
        return {}
    text = data.decode("utf-8")
    if not text.strip():
        return {}
    json_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            json_lines.append(line.split(":", 1)[1].lstrip())
    if not json_lines:
        return json.loads(text)
    return json.loads("\n".join(json_lines))


class McpClient:
    def __init__(self, endpoint: str, api_key: str, *, timeout: int = 60) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "exam-scraper-lawzilla-candidate-collector/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return parse_sse_json(response.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MCP HTTP {exc.code}: {body_text[:500]}") from exc

    def initialize(self) -> None:
        self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "exam-scraper-lawzilla-candidate-collector",
                        "version": "0.1.0",
                    },
                },
            }
        )
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict[str, Any], *, request_id: int) -> dict[str, Any]:
        response = self.post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )
        if "error" in response:
            raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
        result = response.get("result", {})
        for content in result.get("content", []):
            if isinstance(content, dict) and content.get("type") == "text":
                text = content.get("text")
                if isinstance(text, str):
                    return json.loads(text)
        return result


def load_batch_items(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return str(payload.get("batchId") or path.stem), [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return path.stem, [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"unsupported batch shape: {path}")


def firestore_ids_from_review_id(value: Any) -> set[str]:
    if not isinstance(value, str) or not value.startswith("firestore:"):
        return set()
    return {part.strip() for part in value.removeprefix("firestore:").split(",") if part.strip()}


def question_matches_item(question: dict[str, Any], item: dict[str, Any]) -> bool:
    public_id = str(item.get("publicQuestionId") or "").strip()
    original_id = str(item.get("originalQuestionId") or "").strip()
    review_id = str(item.get("reviewQuestionId") or "").strip()
    firestore_ids = firestore_ids_from_review_id(review_id)
    question_ids = {
        str(question.get("public_question_id") or "").strip(),
        str(question.get("original_question_id") or "").strip(),
        str(question.get("originalQuestionId") or "").strip(),
        str(question.get("questionId") or "").strip(),
        str(question.get("source_original_question_id") or "").strip(),
        str(question.get("sourceOriginalQuestionId") or "").strip(),
    }
    question_ids.discard("")
    if public_id and public_id in question_ids:
        return True
    if original_id and original_id in question_ids:
        return True
    record_firestore_ids = set()
    for key in ("original_question_id", "originalQuestionId"):
        record_firestore_ids.update(firestore_ids_from_review_id(question.get(key)))
    record_firestore_ids = {
        str(value).strip()
        for value in (question.get("firestoreQuestionIds") or [])
        if str(value).strip()
    } | record_firestore_ids
    if firestore_ids and record_firestore_ids and firestore_ids.issubset(record_firestore_ids):
        return True
    if firestore_ids and str(question.get("questionId") or "").strip() in firestore_ids:
        return True
    return False


def load_question_from_stage(path: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    payload = load_json(path)
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("question_bodies"), list):
        candidates = [item for item in payload["question_bodies"] if isinstance(item, dict)]
    elif isinstance(payload, list):
        candidates = [item for item in payload if isinstance(item, dict)]
    for question in candidates:
        if question_matches_item(question, item):
            return question
    return None


def stage_question(item: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    stage_files = item.get("stagePatchFiles") if isinstance(item.get("stagePatchFiles"), dict) else {}
    for key in ("explanation", "correctChoice", "questionSet"):
        raw_path = stage_files.get(key)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        question = load_question_from_stage(repo_root / raw_path, item)
        if question:
            return question
    question = load_question_from_stage(repo_root / str(item.get("sourceFile")), item)
    if question:
        return question
    raise ValueError(
        "question not found for "
        f"publicQuestionId={item.get('publicQuestionId')!r}, "
        f"originalQuestionId={item.get('originalQuestionId')!r}, "
        f"reviewQuestionId={item.get('reviewQuestionId')!r}"
    )


def infer_law_ids(question_text: str, choice_text: str) -> str:
    text = question_text + "\n" + choice_text
    ids: list[str] = []
    if any(term in text for term in ("消費機器", "排気筒", "給排気部", "周知", "調査", "保安業務規程")):
        ids.append(GAS_LAW_IDS["gas_business_act_regulation"])
    if any(term in text for term in ("技術基準", "ガス工作物", "製造所", "供給所", "導管", "整圧器", "液化ガス用貯槽", "溶接")):
        ids.append(GAS_LAW_IDS["gas_facility_technical_standard"])
        ids.append(GAS_LAW_IDS["gas_business_act_regulation"])
    if any(term in text for term in ("事故", "報告", "速報", "詳報")):
        ids.append(GAS_LAW_IDS["gas_reporting_rule"])
        ids.append(GAS_LAW_IDS["gas_business_act_regulation"])
    if "高圧ガス保安法" in text:
        ids.append(GAS_LAW_IDS["high_pressure_gas_safety_act"])
    if any(term in text for term in ("ガス用品", "特定ガス用品", "小売供給", "託送供給", "最終保障供給", "ガス主任技術者", "保安規程")):
        ids.append(GAS_LAW_IDS["gas_business_act"])
        ids.append(GAS_LAW_IDS["gas_business_act_order"])
        ids.append(GAS_LAW_IDS["gas_business_act_regulation"])
    if not ids:
        ids = list(GAS_LAW_IDS.values())
    return ",".join(dict.fromkeys(ids))


def normalized_terms(text: str) -> list[str]:
    normalized = (
        text.replace("12kW", "十二キロワット")
        .replace("12KW", "十二キロワット")
        .replace("7kW", "七キロワット")
        .replace("7KW", "七キロワット")
    )
    terms: list[str] = []
    for phrase in SEARCH_PHRASES:
        if phrase in normalized:
            terms.append(phrase)
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?(?:kW|MPa|Pa|m|cm|時間|日|年|戸|℃)?", text):
        term = match.group(0)
        if term in STOP_TERMS or len(term) < 2:
            continue
        terms.append(term)
    return list(dict.fromkeys(terms))


def build_query(question: dict[str, Any], choice_text: str) -> str:
    priority_terms = normalized_terms(choice_text)
    if len(priority_terms) < 2:
        priority_terms.extend(normalized_terms(str(question.get("questionBodyText") or "")))
    return " ".join(priority_terms[:6]) or str(choice_text)[:80]


def compact_candidate(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "law_id",
        "law_name",
        "LawName",
        "address",
        "representative_address",
        "representative_kanjiaddress",
        "representative_title",
        "kanjiaddress",
        "title",
        "snippet",
        "keyword_counts",
        "total_count",
    )
    return {key: result.get(key) for key in keys if result.get(key) not in (None, "", {})}


def seed_article_targets(question: dict[str, Any], choice_text: str) -> list[str]:
    question_text = str(question.get("questionBodyText") or "")
    combined = question_text + "\n" + choice_text
    targets: list[str] = []
    if "消費機器の技術上の基準" in question_text or any(
        term in combined for term in ("排気筒", "給排気部", "密閉燃焼式", "自然排気式")
    ):
        targets.append("345M50000400097@ln202")
    if any(term in question_text for term in ("周知", "消費機器に関する周知")):
        targets.append("345M50000400097@ln197")
    if "調査" in question_text:
        targets.append("345M50000400097@ln200")
    return list(dict.fromkeys(targets))


def summarize_article_response(response: dict[str, Any], *, target: str, query_terms: list[str]) -> dict[str, Any]:
    documents = response.get("documents") if isinstance(response.get("documents"), list) else []
    scored_snippets: list[tuple[int, dict[str, Any]]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        text_value = str(document.get("text") or document.get("body") or "")
        title = str(document.get("title") or "")
        haystack = title + "\n" + text_value
        score_terms = [term for term in query_terms if term and term not in STOP_TERMS]
        score = sum(1 for term in score_terms if term in haystack)
        if query_terms and score == 0:
            continue
        scored_snippets.append(
            (
                score,
                {
                    "title": title,
                    "textSnippet": text_value[:240],
                    "links": document.get("links")[:5] if isinstance(document.get("links"), list) else [],
                    "backlinks": document.get("backlinks")[:5] if isinstance(document.get("backlinks"), list) else [],
                    "matchScore": score,
                },
            )
        )
    scored_snippets.sort(key=lambda item: item[0], reverse=True)
    snippets = [item[1] for item in scored_snippets[:8]]
    if not snippets:
        for document in documents[:3]:
            if not isinstance(document, dict):
                continue
            text_value = str(document.get("text") or document.get("body") or "")
            snippets.append(
            {
                "title": document.get("title"),
                "textSnippet": text_value[:240],
            }
            )
    return {
        "target": target,
        "status": response.get("status"),
        "documentCount": len(documents),
        "responseHash": sha256_json(response),
        "snippets": snippets,
    }


def collect_records(
    *,
    client: McpClient,
    batch_path: Path,
    repo_root: Path,
    max_items: int | None,
    sleep_seconds: float,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    batch_id, items = load_batch_items(batch_path)
    if max_items is not None:
        items = items[:max_items]
    generated_at = now_jst()
    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    request_id = 100
    article_cache: dict[str, dict[str, Any]] = {}

    for item in items:
        question = stage_question(item, repo_root)
        choices = question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else []
        correct = question.get("correctChoiceText") if isinstance(question.get("correctChoiceText"), list) else item.get("correctChoiceTextCompact", "").split("|")
        for choice_index, choice in enumerate(choices):
            choice_text = str(choice)
            query = build_query(question, choice_text)
            query_terms = query.split()
            law_ids = infer_law_ids(str(question.get("questionBodyText") or ""), choice_text)
            base_params = {
                "query": query,
                "match_scope": "paragraph",
                "detail": "snippet",
                "law_ids": law_ids,
                "limit": limit,
            }
            record: dict[str, Any] = {
                "schemaVersion": SCHEMA_VERSION,
                "generatedAt": generated_at,
                "batchId": batch_id,
                "queueSequence": item.get("queueSequence"),
                "priority": item.get("priority"),
                "targetTracks": item.get("targetTracks"),
                "qualification": item.get("qualification"),
                "listGroupId": item.get("listGroupId"),
                "examYear": item.get("examYear"),
                "questionLabel": item.get("questionLabel"),
                "originalQuestionId": item.get("originalQuestionId"),
                "publicQuestionId": item.get("publicQuestionId"),
                "reviewQuestionId": item.get("reviewQuestionId"),
                "sourceFile": item.get("sourceFile"),
                "stagePatchFiles": item.get("stagePatchFiles"),
                "choiceIndex": choice_index,
                "choiceText": choice_text,
                "correctChoiceText": correct[choice_index] if choice_index < len(correct) else None,
                "lawzillaSearchParams": {**base_params, "mode": "AND"},
                "lawzillaSeedTargets": seed_article_targets(question, choice_text),
                "workflowDecision": "candidate_only_needs_primary_evidence_verification",
            }
            try:
                search_attempts: list[dict[str, Any]] = []
                response: dict[str, Any] = {}
                results: list[Any] = []
                for mode in ("AND", "OR"):
                    params = {**base_params, "mode": mode}
                    response = client.call_tool("lawzilla_search", {"params": params}, request_id=request_id)
                    request_id += 1
                    results = response.get("results") if isinstance(response.get("results"), list) else []
                    search_attempts.append(
                        {
                            "mode": mode,
                            "resultCount": response.get("result_count"),
                            "totalResults": response.get("total_results"),
                            "responseHash": sha256_json(response),
                        }
                    )
                    if results or mode == "OR":
                        break
                results = response.get("results") if isinstance(response.get("results"), list) else []
                seed_article_candidates: list[dict[str, Any]] = []
                for target in record["lawzillaSeedTargets"]:
                    if target not in article_cache:
                        article_cache[target] = client.call_tool(
                            "lawzilla_article",
                            {"params": {"target": target}},
                            request_id=request_id,
                        )
                        request_id += 1
                    seed_article_candidates.append(
                        summarize_article_response(
                            article_cache[target],
                            target=target,
                            query_terms=query_terms,
                        )
                    )
                record.update(
                    {
                        "lawzillaStatus": response.get("status"),
                        "lawzillaSearchParams": params,
                        "lawzillaSearchAttempts": search_attempts,
                        "resultCount": response.get("result_count"),
                        "totalResults": response.get("total_results"),
                        "topCandidates": [compact_candidate(result) for result in results[:limit] if isinstance(result, dict)],
                        "seedArticleCandidates": seed_article_candidates,
                        "lawzillaResponseHash": sha256_json(response),
                        "impactOnExistingSearch": "query_rewrite_needed" if not results and not seed_article_candidates else "candidate_review_needed",
                    }
                )
                counts["choices_searched"] += 1
                if results:
                    counts["choices_with_candidates"] += 1
                else:
                    counts["choices_without_candidates"] += 1
                if seed_article_candidates:
                    counts["choices_with_seed_articles"] += 1
            except Exception as exc:  # noqa: BLE001 - keep batch progress and record the failure.
                record.update(
                    {
                        "lawzillaStatus": "error",
                        "error": str(exc),
                        "impactOnExistingSearch": "lawzilla_call_error",
                    }
                )
                counts["choices_with_errors"] += 1
            records.append(record)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    summary = {
        "schemaVersion": "lawzilla-mcp-candidate-collection-summary/v1",
        "generatedAt": generated_at,
        "batchPath": str(batch_path.relative_to(repo_root) if batch_path.is_relative_to(repo_root) else batch_path),
        "batchId": batch_id,
        "itemCount": len(items),
        "choiceRecordCount": len(records),
        "counts": dict(counts),
        "status": "ok" if counts.get("choices_with_errors", 0) == 0 else "needs_review",
    }
    return records, summary


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    no_hit = [record for record in records if not record.get("topCandidates")]
    lines = [
        "# Lawzilla MCP candidate collection",
        "",
        f"- generatedAt: {summary['generatedAt']}",
        f"- batchId: `{summary['batchId']}`",
        f"- itemCount: {summary['itemCount']}",
        f"- choiceRecordCount: {summary['choiceRecordCount']}",
        f"- status: {summary['status']}",
        "",
        "## Counts",
        "",
        "| key | count |",
        "| --- | ---: |",
    ]
    for key, value in sorted(summary["counts"].items()):
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## No-hit Choices", ""])
    if no_hit:
        lines.extend(["| qualification | year | label | choice | id | query |", "| --- | ---: | --- | ---: | --- | --- |"])
        for record in no_hit[:80]:
            query = str(record.get("lawzillaSearchParams", {}).get("query") or "").replace("|", "/")
            display_id = record.get("publicQuestionId") or record.get("originalQuestionId") or record.get("reviewQuestionId")
            lines.append(
                "| `{}` | {} | {} | {} | `{}` | {} |".format(
                    record.get("qualification"),
                    record.get("examYear"),
                    record.get("questionLabel"),
                    int(record.get("choiceIndex", 0)) + 1,
                    display_id,
                    query[:120],
                )
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This artifact stores Lawzilla candidates only.",
            "- It does not verify lawReferences, change correctChoiceText, or modify 00_source.",
            "- Verified lawReferences still require primary evidence comparison.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Lawzilla MCP candidate law references for a question batch.")
    parser.add_argument("--batch", required=True, help="Batch JSON created from the latest question maintenance queue.")
    parser.add_argument("--output-jsonl", required=True, help="Output JSONL path.")
    parser.add_argument("--summary-json", required=True, help="Output summary JSON path.")
    parser.add_argument("--summary-md", required=True, help="Output markdown summary path.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--endpoint-env", default="LAWZILLA_MCP_URL", help="Environment variable containing MCP endpoint URL.")
    parser.add_argument("--api-key-env", default="LAWZILLA_API_KEY", help="Environment variable containing Lawzilla API key.")
    parser.add_argument("--max-items", type=int, default=None, help="Limit number of batch items, for trial runs.")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Delay between MCP calls.")
    parser.add_argument("--limit", type=int, default=5, help="Candidate limit per choice.")
    args = parser.parse_args()

    endpoint = os.environ.get(args.endpoint_env)
    api_key = os.environ.get(args.api_key_env)
    if not endpoint:
        raise SystemExit(f"missing endpoint env var: {args.endpoint_env}")
    if not api_key:
        raise SystemExit(f"missing API key env var: {args.api_key_env}")

    repo_root = Path(args.repo_root).expanduser().resolve()
    batch_path = Path(args.batch).expanduser()
    if not batch_path.is_absolute():
        batch_path = repo_root / batch_path

    client = McpClient(endpoint, api_key)
    client.initialize()
    records, summary = collect_records(
        client=client,
        batch_path=batch_path,
        repo_root=repo_root,
        max_items=args.max_items,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
    )

    output_jsonl = Path(args.output_jsonl).expanduser()
    summary_json = Path(args.summary_json).expanduser()
    summary_md = Path(args.summary_md).expanduser()
    for path in (output_jsonl, summary_json, summary_md):
        if not path.is_absolute():
            path = repo_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_jsonl if output_jsonl.is_absolute() else repo_root / output_jsonl
    summary_json = summary_json if summary_json.is_absolute() else repo_root / summary_json
    summary_md = summary_md if summary_md.is_absolute() else repo_root / summary_md

    write_jsonl(output_jsonl, records)
    summary["outputJsonl"] = str(output_jsonl.relative_to(repo_root) if output_jsonl.is_relative_to(repo_root) else output_jsonl)
    summary["summaryJson"] = str(summary_json.relative_to(repo_root) if summary_json.is_relative_to(repo_root) else summary_json)
    summary["summaryMarkdown"] = str(summary_md.relative_to(repo_root) if summary_md.is_relative_to(repo_root) else summary_md)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_summary_markdown(summary_md, summary, records)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
