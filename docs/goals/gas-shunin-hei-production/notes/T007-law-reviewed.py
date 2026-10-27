"""主担当が直接執筆した法令候補を既存の独立法令監査・保存契約へ渡す実行記録。"""
import copy
import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
TOOLS = RUN / "tool_snapshot"
sys.path.insert(0, str(TOOLS))
from scripts.common.question_identity import load_source_record_inventory
from scripts.common.law_audit_sidecar_contract import law_audit_sidecar_metadata_errors
from scripts.common.question_learning_patterns import QUESTION_LEARNING_PATTERN_IDS
from scripts.common.explanation_references import explanation_reference_errors
from scripts.fix.materialize_minimal_patch import SourcePatchInput, materialize_entries
from tools.question_review_console.codex_app_server import CodexAppServerClient, QUESTION_MAINTENANCE_AUDIT_MODEL
from tools.question_review_console.explanation_quality import explanation_style_issues, law_evidence_utilization_issues
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.law_audit_verification import (
    verification_bundle, review_prompt, review_schema, parse_review,
    required_review_count, promote_candidate, candidate_fields, statement_judgment_guidance,
)
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver
from tools.question_review_console.question_candidate import QuestionCandidate, CandidateUpdate
from tools.question_review_console.qualification_runs import _server_law_audit_fields
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.review_store import atomic_write


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def freeze(path, value):
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f"固定入力が変更された。新しいrevisionを使う: {path}")
    dump(path, value)


def main(candidate_path, action, revision):
    authored = json.loads(candidate_path.read_text())
    if len(authored) != 1:
        raise ValueError("法令監査は一問単位で呼び出す")
    authored = authored[0]
    key = authored["sourceQuestionId"]
    year = key.split(":")[0]
    packet = json.loads((RUN / "records.json").read_text())[key]
    binding = packet["sourceIdentity"]
    source = next(x for x in load_source_record_inventory(ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source",
        qualification="gas-shunin-hei", list_group_id=year) if x.identity.binding.as_mapping() == binding)
    if hashlib.sha256(source.path.read_bytes()).hexdigest() != packet["sourceSha256"]:
        raise ValueError("00_source変更")
    projection = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, binding["sourceRecordRef"])
    if projection.errors:
        raise ValueError(projection.errors)
    current = projection.record
    if authored["questionLearningPatternId"] not in QUESTION_LEARNING_PATTERN_IDS:
        raise ValueError("未知の学習パターン")
    fields = {name: copy.deepcopy(authored[name]) for name in (
        "correctChoiceText", "explanationText", "lawReferences", "lawRevisionFacts", "auditStatus", "reviewState",
        "sourceSummary", "verificationSummary", "reconciliationStatus", "holdReason", "reviewNotes", "isLawRelated",
        "lawGroundedExplanationNotNeeded", "lawContextForExplanation", "suggestedQuestionDetailsByChoice",
        "examTimeDecision", "currentLawDecision")}
    if "explanationReferences" in authored:
        fields["explanationReferences"] = copy.deepcopy(authored["explanationReferences"])
    if fields["isLawRelated"] is not True or fields["reviewState"] != "needs_secondary_review":
        raise ValueError("未確定の主担当法令候補だけを扱う")
    logical = {**current, **fields}
    errors = explanation_style_issues(fields["explanationText"], fields["correctChoiceText"],
        choice_texts=current["choiceTextList"], question_type=current["questionType"],
        is_calculation_question=bool(current.get("isCalculationQuestion"))) + law_evidence_utilization_issues(logical)
    if errors:
        raise ValueError(errors)
    evidence = PrimaryLawEvidenceResolver(ROOT).resolve(logical,
        current_as_of=authored["primaryLawEvidence"]["currentAsOf"], qualification="gas-shunin-hei", list_group_id=year)
    if evidence != authored["primaryLawEvidence"] or evidence["status"] != "complete":
        raise ValueError("主担当の公式本文とresolver実取得が不一致又は未完了")
    candidate = QuestionCandidate(binding["reviewQuestionId"], "ready", "主担当の直接法令提案。独立確認は未完了。",
        (CandidateUpdate("direct-law-fields", fields, ()),))
    question = {"questionId": binding["reviewQuestionId"], "currentRecord": current,
        "primaryLawEvidence": evidence, "statementJudgmentGuidance": statement_judgment_guidance(ROOT),
        "officialQuestionPdf": packet["officialQuestionPdf"], "officialAnswerPdf": packet["officialAnswerPdf"],
        "sourceIdentity": binding, "officialAnswerNumber": authored["officialAnswerNumber"]}
    bundle = verification_bundle(question, candidate,
        reference_guidance=(ROOT / "prompt/qualification_docs/gas-shunin-hei/01_law_reference_policy.md").read_text(),
        proposed_primary_evidence=evidence)
    work = RUN / "law_audits" / (key.replace(":", "_") + "_" + revision)
    paths = [ROOT / f"output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{n:02}.png"
        for n in sorted(set(packet["questionPages"]) | set(authored["officialPagesVisuallyReviewed"]))]
    paths.append(ROOT / f"output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png")
    fixed = {"candidatePath": str(candidate_path.relative_to(ROOT)), "candidateHash": sha(authored),
        "sourceSha256": packet["sourceSha256"], "bundle": bundle,
        "images": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
    freeze(work / "input.json", fixed)
    parsed, executions = [], []
    for number in range(required_review_count(fields)):
        phase = "secondary" if number == 0 else "tertiary"
        prompt = review_prompt(bundle, "二次" if number == 0 else "三次", repo_root=ROOT)
        native_path = work / (phase + "-native.json")
        if native_path.exists():
            native = json.loads(native_path.read_text())
        elif action == "review":
            client = CodexAppServerClient(ROOT)
            try:
                native = dataclasses.asdict(client.run_turn(prompt, model=QUESTION_MAINTENANCE_AUDIT_MODEL,
                    work_type=f"maintenance_law_audit_{phase}_audit_candidate", sandbox="read-only",
                    emit=lambda _: None, output_schema=review_schema(bundle), image_paths=paths, cwd=ROOT,
                    turn_group="gas-shunin-hei-direct-law"))
            finally:
                client.close()
            dump(native_path, native)
        else:
            raise ValueError("実監査が未完了")
        if native.get("changed_files") or native.get("subagent_thread_ids") or not all(native.get(k) for k in ("session_id", "thread_id", "turn_id")):
            raise ValueError("実read-only sessionを確認できない")
        if any(x["threadId"] == native["thread_id"] for x in executions):
            raise ValueError("同じsessionを再利用している")
        review = parse_review(native["final_message"], bundle, repo_root=ROOT)
        parsed.append(review)
        executions.append({"phase": phase, "executionId": f"{native['thread_id']}:{native['turn_id']}",
            "threadId": native["thread_id"], "turnId": native["turn_id"], "model": native["model"], "review": review,
            "nativePath": str(native_path.relative_to(ROOT)), "nativeSha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
            "promptHash": hashlib.sha256(prompt.encode()).hexdigest()})
        if review["decision"] != "approve":
            break
    promoted = promote_candidate(candidate, parsed)
    verification = {"evidenceHash": bundle["evidenceHash"], "bundle": bundle, "reviews": executions}
    dump(work / "result.json", {"candidateHash": sha(authored), "promoted": dataclasses.asdict(promoted), "verification": verification})
    if promoted.status == "blocked":
        raise ValueError(promoted.summary)
    if action == "review":
        print(key, "独立法令監査承認", len(executions), "session(s)")
        return
    # 内容監査と法令監査は別session。採点・全肢判定も確定してから保存する。
    content_results = []
    for p in (RUN / "content_audits").glob(key.replace(":", "_") + "*/result.json"):
        item = json.loads(p.read_text()); content_input = json.loads(p.with_name("input.json").read_text())
        if item["accepted"] and content_input["bundle"]["candidate"] == authored and content_input["bundle"]["fixedQuestion"]["record"] == current:
            native_path = ROOT / item["nativePath"]
            if hashlib.sha256(native_path.read_bytes()).hexdigest() != item["nativeSha256"]:
                raise ValueError("内容監査の実応答hash変更")
            content_results.append((p, item))
    if not content_results:
        raise ValueError("同じ現行内容と候補の独立内容監査が未完了")
    policies = QualificationWorkflow(ROOT, QuestionInventory(ROOT)).versioned_policies("gas-shunin-hei")
    run_id = "direct-primary-" + sha(authored)
    values = _server_law_audit_fields(qualification="gas-shunin-hei", list_group_id=year, run_id=run_id,
        policy_version=policies["law_audit"]["policyVersion"], projected=current,
        candidate_fields=candidate_fields(promoted), verification=verification)
    values.update(schemaVersion="law-revision-audit/v2", examYear=int(year), **binding)
    metadata_errors = law_audit_sidecar_metadata_errors(values, expected_choice_count=len(current["choiceTextList"]),
        expected_qualification="gas-shunin-hei", expected_list_group_id=year)
    if metadata_errors:
        raise ValueError(metadata_errors)
    raw = {**authored, **values, "original_question_id": binding["reviewQuestionId"]}
    planned = {}
    tasks = [("correct_choice", "23_correctChoiceText_fixed", "correctChoiceText_fixed"),
        ("law_context", "18_law_context_prepared", "lawContext_prepared"),
        ("explanation", "21_explanationText_added", "explanationText_added"),
        ("question_set", "22_questionSetId_linked", "questionSetId_linked")]
    if "questionIntent" in authored:
        tasks.append(("question_intent", "15_correctChoiceText_fixed", "correctChoiceText_fixed"))
    if "questionType" in authored or "isCalculationQuestion" in authored:
        if not authored.get("questionType") or not isinstance(authored.get("isCalculationQuestion"), bool):
            raise ValueError("形式の変更には2fieldの直接判定が必要")
        tasks.append(("question_type", "10_questionType_fixed", "questionType_fixed"))
    for task, directory, suffix in tasks:
        target = source.path.parent.parent / directory / f"{source.path.stem}_merged_{suffix}.json"
        if task == "question_type":
            target = target.with_name(f"{source.path.stem}_{suffix}.json")
        legacy = target.with_name(f"{source.path.stem}_{suffix}.json")
        if legacy != target and legacy.exists():
            if target.exists():
                raise ValueError("patch名の重複")
            target = legacy
        rows = json.loads(target.read_text()) if target.exists() else []
        by_ref = {x["sourceRecordRef"]: x for x in rows}
        if len(rows) != len(by_ref):
            raise ValueError("既存patchのidentity重複")
        entry = materialize_entries(task, [SourcePatchInput(current, source.identity)], [raw])[0]
        if task == "explanation" and "explanationReferences" in raw:
            errors = explanation_reference_errors(raw["explanationReferences"])
            if errors:
                raise ValueError(errors)
            # 21層が既に所有するfieldを直接執筆値として保存する。
            # 固定materializerはこのoptional fieldを転記しないため、共有実装は変更しない。
            entry["explanationReferences"] = copy.deepcopy(raw["explanationReferences"])
        by_ref[binding["sourceRecordRef"]] = entry
        planned[target] = sorted(by_ref.values(), key=lambda x: int(x["sourceRecordRef"].rsplit("#", 1)[1]))
    sidecar = ROOT / f"output/gas-shunin-hei/review/law_revision_audit/{year}_law_revision_audit.jsonl"
    rows = [json.loads(line) for line in sidecar.read_text().splitlines() if line.strip()] if sidecar.exists() else []
    by_ref = {x["sourceRecordRef"]: x for x in rows}
    if len(by_ref) != len(rows):
        raise ValueError("既存sidecarのidentity重複")
    by_ref[binding["sourceRecordRef"]] = values
    # 書込み直前に実入力を再確認する。
    fresh = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, binding["sourceRecordRef"])
    if fresh.errors or fresh.record != current:
        raise ValueError("監査中の内容変更")
    for path in [*planned, sidecar]:
        if path.exists():
            backup = RUN / "patch_backups" / hashlib.sha256(path.read_bytes()).hexdigest()
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
    for path, payload in planned.items():
        dump(path, payload)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(sidecar, "".join(json.dumps(x, ensure_ascii=False) + "\n" for _, x in sorted(by_ref.items())))
    saved_projection = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, binding["sourceRecordRef"])
    if saved_projection.errors or any(saved_projection.record.get(name) != raw[name]
            for name in ("correctChoiceText", "explanationText", "questionSetId", "lawReferences")
            if name in raw):
        raise ValueError("正式patchのprojection読み戻しが直接執筆値と一致しない")
    if "explanationReferences" in raw and saved_projection.record.get("explanationReferences") != raw["explanationReferences"]:
        raise ValueError("21層の公式参照のprojection読み戻し不一致")
    receipt = {"candidates": [{"sourceQuestionId": key, "sourceIdentity": binding, "candidateHash": sha(authored),
        "auditPath": str(content_results[-1][0].relative_to(ROOT)), "nativeSha256": content_results[-1][1]["nativeSha256"],
        "lawAuditPath": str((work / "result.json").relative_to(ROOT))}],
        "patches": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [*planned, sidecar]]}
    dump(RUN / "saved" / (candidate_path.stem + ".json"), receipt)
    print("正式保存", key, len(planned), "patch files and law audit sidecar")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3])
