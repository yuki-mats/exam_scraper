"""Real contract risks for verified25 revisions and completion; no live writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from tools.question_bank import feedback_verified_delivery as delivery
from tools.question_bank import feedback_publication_result
from tools.question_review_console.projection import sha256_json


@pytest.fixture
def sample(tmp_path):
    root = tmp_path
    ids = [f"gas-shunin-otsu-2021-law-q02-s{i:02}" for i in range(1, 6)]
    rows = []
    for identity in [*ids, "gas-shunin-otsu-2021-law-q03-s01"]:
        rows.append({"questionId": identity, "qualificationId": "gas-shunin-otsu", "listGroupId": "2021", "examYear": 2021,
                     "originalQuestionId": "original" if identity in ids else "other", "questionType": "true_false",
                     "isDeleted": False, "isChoiceOnly": False, "questionBodyText": "記述の真偽を判断する。", "questionText": "本文と記述",
                     "originalQuestionBodyText": "公式本文", "originalQuestionChoiceText": identity + "記述",
                     "correctChoiceText": "正しい", "explanationText": "正しい。根拠は法令で確認できる。", "isLawRelated": True,
                     "suggestedQuestions": ["補足"], "suggestedQuestionDetails": [{"question": "補足", "answer": "回答"}]})
    base = {"schemaVersion": delivery.gas.SCHEMA_VERSION, "qualificationId": "gas-shunin-otsu", "list_group_id": "2021", "questions": rows, "total_count": len(rows)}
    def value(v):
        return {"present": True, "value": v, "reason": "本文と公式根拠から一肢ずつ判断した。", "evidenceKeys": ["law"]}
    candidate = {"schemaVersion": "feedback-verified-authoring-candidate/v1", "revisionKind": "authoring_only", "decisionsByDocument": {
        identity: {"isCalculationQuestion": value(False), "questionIntent": value("select_correct"), "questionLearningPatternId": value("conditions_scope")}
        for identity in ids}}
    source = root / "source.json"; source.write_text('{}\n')
    evidence = {"schemaVersion": "feedback-verified-delivery-evidence/v1", "referenceDate": datetime.now(timezone.utc).date().isoformat(),
                "sources": {"law": delivery._ref(source)}, "evidenceByDocument": {i: {"examLaw": ["law"], "currentLaw": ["law"], "category": ["law"]} for i in ids}}
    return root, ids, base, candidate, evidence, value


def test_authoring_revision_preserves_all_public_dtos_and_non_targets(sample):
    _, ids, base, candidate, evidence, _ = sample
    old = deepcopy(base)
    effective, deltas = delivery._revision(base, candidate, ids, evidence)
    assert len(deltas) == 15
    assert base == old
    assert effective["questions"][-1] == base["questions"][-1]
    assert [delivery.uploader.build_doc_data_base(r) for r in effective["questions"]] == [delivery.uploader.build_doc_data_base(r) for r in base["questions"]]


@pytest.mark.parametrize("mutation", ["extra_field", "missing_id", "empty_reason", "unbound_evidence", "absent", "wrong_bool", "unknown_pattern"])
def test_revision_rejects_wrong_scope_or_unreasoned_decisions(sample, mutation):
    _, ids, base, candidate, evidence, value = sample
    decisions = candidate["decisionsByDocument"][ids[0]]
    if mutation == "extra_field": decisions["correctChoiceText"] = value("間違い")
    elif mutation == "missing_id": del candidate["decisionsByDocument"][ids[1]]
    elif mutation == "empty_reason": decisions["questionIntent"]["reason"] = " "
    elif mutation == "unbound_evidence": decisions["questionIntent"]["evidenceKeys"] = ["not-fixed"]
    elif mutation == "absent": decisions["questionIntent"] = {**value(None), "present": False}
    elif mutation == "wrong_bool": decisions["isCalculationQuestion"]["value"] = 0
    else: decisions["questionLearningPatternId"]["value"] = "fabricated"
    with pytest.raises(ValueError): delivery._revision(base, candidate, ids, evidence)


def test_maintenance_revision_zero_supplements_preserves_outside_scope(sample):
    _, ids, base, candidate, evidence, value = sample
    candidate["revisionKind"] = "maintenance"
    for identity in ids:
        candidate["decisionsByDocument"][identity].update({"explanationText": value("正しい。具体的な条件を一つずつ説明する。"),
            "suggestedQuestions": value([]), "suggestedQuestionDetails": value([]), "explanationReferences": value([{"source": "公式法令"}])})
    effective, deltas = delivery._revision(base, candidate, ids, evidence)
    assert all(d["field"] in delivery.AUTHORING_FIELDS | delivery.MAINTENANCE_FIELDS for d in deltas)
    assert effective["questions"][-1] == base["questions"][-1]
    assert effective["questions"][0]["suggestedQuestions"] == []
    candidate["decisionsByDocument"][ids[0]]["suggestedQuestions"]["value"] = ["mirror mismatch"]
    with pytest.raises(ValueError, match="mirror"): delivery._revision(base, candidate, ids, evidence)


def test_primary_evidence_requires_fresh_all_sibling_bound_bytes(sample):
    root, ids, _, _, evidence, _ = sample
    delivery._evidence(root, evidence, ids)
    changed = deepcopy(evidence); changed["referenceDate"] = "2000-01-01"
    with pytest.raises(ValueError, match="fresh"): delivery._evidence(root, changed, ids)
    changed = deepcopy(evidence); del changed["evidenceByDocument"][ids[0]]
    with pytest.raises(ValueError): delivery._evidence(root, changed, ids)
    Path(evidence["sources"]["law"]["ref"]).write_text('{"changed":true}')
    with pytest.raises(ValueError, match="hash"): delivery._evidence(root, evidence, ids)


def test_revision_exclusive_replay_conflict_and_partial_resume(tmp_path):
    path = tmp_path / "nested/revision.json"
    ref = delivery._write(path, {"approved": "fixed"})
    assert delivery._write(path, {"approved": "fixed"}) == ref
    with pytest.raises(ValueError, match="conflict"): delivery._write(path, {"approved": "different"})
    assert json.loads(path.read_bytes()) == {"approved": "fixed"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_stage_owns_every_field_and_requires_no_remaining_diffs(sample):
    _, ids, base, candidate, evidence, value = sample
    effective, _ = delivery._revision(base, candidate, ids, evidence)
    plan = {"identity": {"siblingIds": ids}, "policies": {"maintenance": {"question_intent": {"issueFields": ["questionIntent"]}}}}
    output = {"schemaVersion": "feedback-verified-stage-decisions/v1", "stageId": "question_intent", "decisionsByDocument": {i: {"questionIntent": value("select_correct")} for i in ids}}
    assert delivery._stage_candidate(output, "question_intent", plan, effective, evidence) == output
    output["decisionsByDocument"][ids[0]]["questionIntent"]["value"] = "select_incorrect"
    with pytest.raises(ValueError, match="remaining"): delivery._stage_candidate(output, "question_intent", plan, effective, evidence)


def evaluation_result():
    return {"status": "passed", "explanationScore": 95, "criticalIssues": [], "summary": "全肢の一次根拠と説明を確認した。", "reworkItems": [],
            "choiceEvaluations": [{"choiceIndex": n, "verdict": "true", "reason": "一次根拠と一致する。", "evidence": [{"source": "公式", "locator": "条文", "summary": "規定の内容と一致する。"}]} for n in range(5)]}


def test_real_evaluation_schema_and_threshold_refuse_false_pass(sample):
    _, ids, base, candidate, evidence, _ = sample
    effective, _ = delivery._revision(base, candidate, ids, evidence)
    review = {"projected": {"choiceTextList": [str(i) for i in range(5)]}}
    context = {"officialOrder": [{"questionId": i} for i in ids]}
    raw = evaluation_result()
    assert delivery._evaluation(raw, review, effective, context)["status"] == "passed"
    raw["explanationScore"] = 50
    with pytest.raises(ValueError, match="evaluation"): delivery._evaluation(raw, review, effective, context)
    raw = evaluation_result(); raw["choiceEvaluations"][0]["verdict"] = "false"
    with pytest.raises(ValueError): delivery._evaluation(raw, review, effective, context)
    raw = evaluation_result(); raw["reworkItems"] = [{"stage": "03", "message": "要修正", "choiceIndexes": [0]}]
    with pytest.raises(ValueError): delivery._evaluation(raw, review, effective, context)


def publication_fixture(sample):
    root, ids, base, _, _, _ = sample
    before = {}; current = {}; docs = []
    for identity in ids:
        fields = {"originalQuestionId": "original", "explanationText": "before", "preserved": None, "createdById": "creator"}
        before[identity] = {"questionId": identity, "exists": True, "updateTimeExact": "2026-10-17T01:00:00.000000001Z", "fields": fields}
        current[identity] = {"questionId": identity, "exists": True, "updateTimeExact": "2026-10-17T01:01:00.000000002Z",
                             "fields": {**fields, "explanationText": "after", "updatedAt": "2026-10-17T01:01:00Z", "updatedById": delivery.uploader.UPDATED_BY_ID}}
        docs.append({"questionId": identity, "originalQuestionId": "original", "qualificationId": "gas-shunin-otsu", "explanationText": "after"})
    baseline = root / "baseline.json"; delivery._write(baseline, before)
    payload = {"questions": docs, "writeFieldsByDocument": {i: ["explanationText"] for i in ids},
               "expectedUpdateTimes": {i: before[i]["updateTimeExact"] for i in ids}, "expectedLiveFingerprint": "a" * 64}
    commit = {"schemaVersion": delivery.uploader.ATOMIC_MAINTENANCE_RESULT_SCHEMA, "status": "committed", "documentIds": ids,
              "changedDocumentIds": ids, "unchangedDocumentIds": [], "writeFieldsByDocument": payload["writeFieldsByDocument"],
              "expectedUpdateTimes": payload["expectedUpdateTimes"], "expectedLiveFingerprint": payload["expectedLiveFingerprint"],
              "writeResults": [{"questionId": i, "updateTime": current[i]["updateTimeExact"]} for i in ids]}
    plan = {"repoRoot": str(root), "identity": {"siblingIds": ids}}
    return plan, {"baseline": delivery._ref(baseline)}, payload, commit, current


def test_readback_exact_commit_all_fields_and_presence(sample):
    args = publication_fixture(sample)
    assert len(delivery._readback_match(*args)) == 5


@pytest.mark.parametrize("mutation", ["commit_version", "missing_id", "mask", "value", "outside_presence", "outside_value", "partial_commit", "duplicate_write", "writer"])
def test_publication_readback_refuses_unconfirmed_or_partial_result(sample, mutation):
    plan, candidate, payload, commit, current = publication_fixture(sample)
    identity = plan["identity"]["siblingIds"][0]
    if mutation == "commit_version": current[identity]["updateTimeExact"] = "2026-10-17T01:01:00.000000003Z"
    elif mutation == "missing_id": del current[identity]
    elif mutation == "mask": commit["writeFieldsByDocument"] = {identity: ["correctChoiceText"]}
    elif mutation == "value": current[identity]["fields"]["explanationText"] = "wrong"
    elif mutation == "outside_presence": del current[identity]["fields"]["preserved"]
    elif mutation == "outside_value": current[identity]["fields"]["createdById"] = "other"
    elif mutation == "partial_commit": commit["changedDocumentIds"] = [identity]
    elif mutation == "duplicate_write": commit["writeResults"][1] = deepcopy(commit["writeResults"][0])
    elif mutation == "writer": current[identity]["fields"]["updatedById"] = "other"
    with pytest.raises(ValueError): delivery._readback_match(plan, candidate, payload, commit, current)


def test_native_must_use_real_verifier_rejection_and_no_self_assertion(monkeypatch):
    monkeypatch.setattr(delivery.stages, "capture_native_guard", lambda approvals: {})
    verify = Mock(side_effect=ValueError("dedicated independent approval evidence required"))
    monkeypatch.setattr(delivery.native, "verify_independent_approval", verify)
    with pytest.raises(ValueError, match="independent"):
        delivery._native({"decision": "approved"}, {"planHash": "hash"}, {"reviewerProfile": None})
    verify.assert_called_once_with({"decision": "approved"}, {"planHash": "hash"}, production=True)


def test_law_audit_requires_three_distinct_sessions(monkeypatch):
    monkeypatch.setattr(delivery, "_scope", lambda *args: {})
    monkeypatch.setattr(delivery, "_native", lambda *args: {"reviewerSessionId": "same"})
    node = {"reviewCount": 3}
    with pytest.raises(ValueError, match="count"): delivery._node_bindings(Path("/"), Path("/"), node, [{}])
    with pytest.raises(ValueError, match="distinct"): delivery._node_bindings(Path("/"), Path("/"), node, [{}, {}, {}])


def test_completion_dedicated_dispatch_does_not_cast_as_old_save(monkeypatch):
    expected = {"runId": "native-run", "documentIds": ["one"], "liveReadbackMatched": True}
    verify = Mock(return_value=expected)
    monkeypatch.setattr(delivery, "verify_completion", verify)
    artifact = {"schemaVersion": delivery.PUBLICATION_SCHEMA}
    assert feedback_publication_result.verify_improvement_publication(Path("/"), artifact, task_id="task", source_question_ids={"source"}, task_requirement={}) == expected
    verify.assert_called_once()
    with pytest.raises(ValueError, match="dedicated task publication"):
        feedback_publication_result.verify_improvement_publication(Path("/"), {"schemaVersion": "fake"}, task_id="task", source_question_ids=set(), task_requirement={})


def test_subprocess_failure_is_not_a_gate_success(monkeypatch, tmp_path):
    monkeypatch.setattr(delivery.subprocess, "run", lambda *args, **kwargs: type("R", (), {"returncode": 7, "stdout": "fake pass", "stderr": "failure"})())
    with pytest.raises(ValueError, match="checker failed"):
        delivery._run(tmp_path, ["python", "checker.py"])


def test_graph_input_mutation_refused(tmp_path):
    output = tmp_path / "run"; path = output / "reviews/one/plan.json"
    delivery._write(path, {"node": "original"})
    snapshot = delivery._graph_snapshot(output)
    path.write_text('{"node":"changed"}')
    with pytest.raises(ValueError, match="graph changed"):
        delivery._check_graph(snapshot, tmp_path, {"inputs": {}})


def test_actual_native_profile_and_later_revocation_are_replayed(monkeypatch, tmp_path):
    from tests.test_native_independent_approval import native_audit
    scope = {"nativeCreationCall": "exact native scope call\n", "planHash": "fixed"}
    record = native_audit(tmp_path, scope, encrypted=False)
    child = Path(record["reviewerFinal"]["sessionPath"])
    profile = {"type": "turn_context", "payload": {"turn_id": "review-turn", "model": "fixture-model", "effort": "high"}}
    child.write_bytes(child.read_bytes() + (json.dumps(profile) + '\n').encode())
    monkeypatch.setattr(delivery.native, "NATIVE_SESSION_ROOT", tmp_path)
    node = {"reviewerProfile": {"model": "fixture-model", "reasoningEffort": "high"}}
    assert delivery._native(record, scope, node)["reviewerProfile"] == node["reviewerProfile"]
    bad_node = {"reviewerProfile": {"model": "other-model", "reasoningEffort": "high"}}
    with pytest.raises(ValueError, match="profile"): delivery._native(record, scope, bad_node)
    revoked = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [
        {"type": "output_text", "text": json.dumps({"scope": scope, "decision": "rejected"})}]}}
    child.write_bytes(child.read_bytes() + (json.dumps(revoked) + '\n').encode())
    with pytest.raises(ValueError, match="revoked"): delivery._native(record, scope, node)


@pytest.fixture
def rig(sample, monkeypatch):
    root, ids, base, candidate, evidence, value = sample
    output = root / delivery.context_api.saver.PRIVATE / "delivery"
    output.mkdir(parents=True)
    canonical = root / "output/gas-shunin-otsu/questions_json/2021/25_verified_publication/2021_verified_publication.json"
    delivery._write(canonical, base)
    effective, deltas = delivery._revision(base, candidate, ids, evidence)
    candidate_ref = delivery._write(output / "candidate.json", candidate)
    evidence_ref = delivery._write(output / "evidence.json", evidence)
    old_baseline = {}
    for row in base["questions"]:
        if row["questionId"] in ids:
            fields = delivery.uploader.build_doc_data_base(row)
            fields.update(explanationText="正しい。旧解説。", suggestedQuestions=["旧補足"], suggestedQuestionDetails=[{"question": "旧補足", "answer": "旧回答"}])
            old_baseline[row["questionId"]] = {"questionId": row["questionId"], "exists": True, "fields": fields, "updateTimeExact": "2026-10-17T01:00:00.000000001Z"}
    save_ref = delivery._write(output / "old-save-plan.json", {"baseline": old_baseline})
    save_receipt = delivery._write(output / "old-save-receipt.json", {"approvalEvidence": {"fixture": "historical"}})
    identity = {"canonical": delivery._ref(canonical), "siblingIds": ids, "originalQuestionId": "original", "qualificationId": "gas-shunin-otsu", "listGroupId": "2021"}
    required = ["question_intent", "law_audit"]
    policies = {"maintenance": {stage: {"issueFields": ["questionIntent"] if stage == "question_intent" else ["explanationText"], "agentPolicy": {}} for stage in required}}
    context = {"identity": identity, "save": {"plan": save_ref, "receipt": save_receipt}, "officialOrder": [{"questionId": i} for i in ids],
               "fieldScopes": [{"questionId": i, "writeFields": ["explanationText", "suggestedQuestionDetails"]} for i in ids]}
    context_ref = delivery._write(output / "context.json", context)
    plan = delivery._seal({"schemaVersion": delivery.PLAN_SCHEMA, "repoRoot": str(root), "taskId": "task", "taskRequirement": {"requirementHash": "purpose"},
                          "identity": identity, "contextFiles": {"context.json": context_ref}, "candidate": candidate_ref, "evidence": evidence_ref,
                          "allDocumentIds": [r["questionId"] for r in base["questions"]], "allDocumentCount": len(base["questions"]), "effectiveHash": sha256_json(effective),
                          "publicProjectionHash": "base", "effectivePublicProjectionHash": "effective", "revisionDeltas": deltas, "revisionKind": "authoring_only",
                          "requiredStages": required, "policies": policies, "inputs": {}})
    delivery._write(output / "plan.json", plan)
    review = {"projected": {"choiceTextList": [str(i) for i in range(5)]}}
    monkeypatch.setattr(delivery, "_environment", lambda *a, **k: (deepcopy(plan), deepcopy(context), deepcopy(review), deepcopy(effective), deepcopy(evidence)))
    monkeypatch.setattr(delivery, "task_requirement_binding", lambda *a, **k: deepcopy(plan["taskRequirement"]))
    monkeypatch.setattr(delivery.stages, "capture_native_guard", lambda *a, **k: {})
    monkeypatch.setattr(delivery.stages, "assert_native_guard", lambda *a, **k: None)
    monkeypatch.setattr(delivery, "_native", lambda a, scope, node: {"reviewerSessionId": a["session"], "reviewerTurnId": "turn", "scopeHash": sha256_json(scope)})
    db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE improvement_tasks(task_id TEXT PRIMARY KEY,status TEXT)")
    db.execute("INSERT INTO improvement_tasks VALUES('task','open')"); db.commit()
    yield {"root": root, "output": output, "plan": plan, "context": context, "effective": effective, "evidence": evidence,
           "ids": ids, "db": db, "value": value, "baseline": old_baseline}
    db.close()


def save_revision(rig, hook=None):
    delivery.prepare(rig["root"], rig["output"], "revision", connection=rig["db"])
    return delivery.record(rig["root"], rig["output"], "revision", [{"session": "revision-author-independent"}], connection=rig["db"], failure_hook=hook)


@pytest.mark.parametrize("phase", ["intent_saved", "revision_saved", "receipt_saved"])
def test_revision_record_crash_resume_is_exclusive_and_idempotent(rig, phase):
    def hook(point):
        if point == phase: raise RuntimeError("simulated crash")
    with pytest.raises(RuntimeError): save_revision(rig, hook)
    receipt = save_revision(rig)
    assert receipt == save_revision(rig)
    assert receipt["revision"] == delivery._ref(delivery._revision_path(rig["root"], rig["plan"]))
    assert delivery._verify_revision(rig["root"], rig["output"], rig["plan"], rig["db"]) == receipt
    with pytest.raises(ValueError, match="conflict"):
        delivery.record(rig["root"], rig["output"], "revision", [{"session": "other-approved-review"}], connection=rig["db"])


def prepare_flow(rig, monkeypatch):
    save_revision(rig)
    for stage_id in rig["plan"]["requiredStages"]:
        field = "questionIntent" if stage_id == "question_intent" else "explanationText"
        rows = {r["questionId"]: r for r in rig["effective"]["questions"]}
        candidate = {"schemaVersion": "feedback-verified-stage-decisions/v1", "stageId": stage_id,
                     "decisionsByDocument": {i: {field: rig["value"](rows[i][field])} for i in rig["ids"]}}
        path = rig["output"] / (stage_id + "-candidate.json"); delivery._write(path, candidate)
        delivery.prepare(rig["root"], rig["output"], "stage", path, connection=rig["db"], stage_id=stage_id)
        approvals = [{"session": stage_id + str(n)} for n in range(delivery._review_count("stage", stage_id))]
        delivery.record(rig["root"], rig["output"], "stage", approvals, connection=rig["db"], stage_id=stage_id)
    path = rig["output"] / "evaluation-candidate.json"; delivery._write(path, evaluation_result())
    delivery.prepare(rig["root"], rig["output"], "evaluation", path, connection=rig["db"])
    with pytest.raises(ValueError, match="new independent"):
        delivery.record(rig["root"], rig["output"], "evaluation", [{"session": "question_intent0"}], connection=rig["db"])
    delivery.record(rig["root"], rig["output"], "evaluation", [{"session": "independent-evaluator"}], connection=rig["db"])
    # The three isolated mandatory checker subprocesses are exercised separately;
    # this integration fixture pins their gate boundary without invoking real
    # production-ledger/native dependencies or generating repository artifacts.
    monkeypatch.setattr(delivery, "_verify_checks", lambda *a, **k: {"verified": True})
    delivery._write(rig["output"] / "checks/receipt.json", {"verified": "fixture boundary"})
    monkeypatch.setattr(delivery, "_git", lambda *a, **k: {"head": "pushed-main"})
    monkeypatch.setattr(delivery, "_completion_contract", lambda *a, **k: {"expectedStatus": "open", "reason": delivery.COMPLETION_REASON})
    snapshots = {"current": deepcopy(rig["baseline"])}
    monkeypatch.setattr(delivery, "_snapshot", lambda *a, **k: deepcopy(snapshots["current"]))
    delivery.prepare(rig["root"], rig["output"], "publication", connection=rig["db"])
    delivery.record(rig["root"], rig["output"], "publication", [{"session": "publication-auditor"}], connection=rig["db"])
    return snapshots


def fake_transport(snapshots, calls):
    def transport(payload, label, *, dry_run=False, **kwargs):
        if dry_run:
            return {"status": "dry_run", "payloadHash": sha256_json(payload)}
        calls.append(deepcopy(payload))
        ids = [q["questionId"] for q in payload["questions"]]
        for question in payload["questions"]:
            identity = question["questionId"]
            current = snapshots["current"][identity]
            current["updateTimeExact"] = "2026-10-17T02:00:00.000000002Z"
            for field in payload["writeFieldsByDocument"][identity]:
                current["fields"][field] = delivery.uploader.build_doc_data_base(question)[field]
            current["fields"].update(updatedById=delivery.uploader.UPDATED_BY_ID, updatedAt="2026-10-17T02:00:00Z")
        return {"schemaVersion": delivery.uploader.ATOMIC_MAINTENANCE_RESULT_SCHEMA, "status": "committed", "documentIds": ids,
                "writeFieldsByDocument": payload["writeFieldsByDocument"], "expectedUpdateTimes": payload["expectedUpdateTimes"],
                "expectedLiveFingerprint": payload["expectedLiveFingerprint"], "changedDocumentIds": ids, "unchangedDocumentIds": [],
                "writeResults": [{"questionId": i, "updateTime": snapshots["current"][i]["updateTimeExact"]} for i in ids], "publicationConfirmed": False}
    return transport


def test_vertical_publish_reconcile_and_no_second_write(rig, monkeypatch):
    calls = []
    # Scope preparation uses actual atomic mask validator, but not SDK writes.
    snapshots = prepare_flow(rig, monkeypatch)
    transport = fake_transport(snapshots, calls)
    original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    result = delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    assert result["publicationConfirmed"] is True
    assert len(calls) == 1
    assert delivery.publish(rig["root"], rig["output"], connection=rig["db"]) == result
    assert len(calls) == 1


def test_publication_unknown_commit_never_blindly_retries_or_completes(rig, monkeypatch):
    prepare_flow(rig, monkeypatch)
    original = delivery.uploader.upload_atomic_maintenance_questions
    attempts = []
    def transport(payload, label, **kwargs):
        if kwargs.get("dry_run"): return original(payload, label, **kwargs)
        attempts.append(True)
        raise delivery.uploader.AtomicMaintenanceCommitUnconfirmed({"status": "prepared"})
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", transport)
    with pytest.raises(ValueError, match="unconfirmed"): delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    with pytest.raises(ValueError, match="no exact commit"): delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    assert len(attempts) == 1
    assert not (rig["output"] / "publication/result.json").exists()


def test_publication_preflight_conflict_prevents_durable_attempt_and_write(rig, monkeypatch):
    snapshots = prepare_flow(rig, monkeypatch)
    snapshots["current"][rig["ids"][0]]["updateTimeExact"] = "2026-10-17T01:00:00.000000003Z"
    original = delivery.uploader.upload_atomic_maintenance_questions
    writes = []
    def transport(payload, label, **kw):
        if kw.get("dry_run"): return original(payload, label, **kw)
        writes.append(True)
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", transport)
    with pytest.raises(ValueError, match="fresh production"): delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    assert writes == []
    assert not (rig["output"] / "publication/attempt.json").exists()


def test_publication_intent_crash_resume_uses_one_cas_attempt(rig, monkeypatch):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls)
    original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    def crash(phase):
        if phase == "intent_saved": raise RuntimeError("crash before attempt")
    with pytest.raises(RuntimeError): delivery.publish(rig["root"], rig["output"], connection=rig["db"], failure_hook=crash)
    assert len(calls) == 0
    assert delivery.publish(rig["root"], rig["output"], connection=rig["db"])["publicationConfirmed"]
    assert len(calls) == 1


def test_readback_failure_is_reconcilable_without_second_write(rig, monkeypatch):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls)
    original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    original_snapshot = delivery._snapshot
    count = [0]
    def flaky(plan):
        count[0] += 1
        if count[0] == 2: raise OSError("network readback unavailable")
        return original_snapshot(plan)
    monkeypatch.setattr(delivery, "_snapshot", flaky)
    with pytest.raises(OSError): delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    assert len(calls) == 1
    assert (rig["output"] / "publication/commit.json").exists()
    assert not (rig["output"] / "publication/result.json").exists()
    assert delivery.reconcile(rig["root"], rig["output"], connection=rig["db"])["publicationConfirmed"]
    assert len(calls) == 1


def test_completion_replays_task_binding_fresh_commit_and_outside_mask(rig, monkeypatch):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls)
    original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    artifact = {"schemaVersion": delivery.PUBLICATION_SCHEMA, "taskId": "task", "execution": str(rig["output"]),
                "deliveryPlan": delivery._ref(rig["output"] / "plan.json"), "publicationResult": delivery._ref(rig["output"] / "publication/result.json")}
    kwargs = {"task_id": "task", "source_question_ids": {"original"}, "task_requirement": rig["plan"]["taskRequirement"], "connection": rig["db"]}
    assert delivery.verify_completion(rig["root"], artifact, **kwargs)["liveReadbackMatched"]
    kwargs["task_requirement"] = {"requirementHash": "changed"}
    with pytest.raises(ValueError, match="binding"): delivery.verify_completion(rig["root"], artifact, **kwargs)
    kwargs["task_requirement"] = rig["plan"]["taskRequirement"]
    snapshots["current"][rig["ids"][0]]["fields"]["questionText"] = "concurrent edit"
    with pytest.raises(ValueError, match="outside-mask"): delivery.verify_completion(rig["root"], artifact, **kwargs)


def test_same_operation_native_cache_rejects_mid_operation_revocation(monkeypatch, tmp_path):
    from tests.test_native_independent_approval import native_audit
    scope = {"nativeCreationCall": "exact call\n", "planHash": "fixed"}
    record = native_audit(tmp_path, scope, encrypted=False)
    child = Path(record["reviewerFinal"]["sessionPath"])
    child.write_bytes(child.read_bytes() + (json.dumps({"type": "turn_context", "payload": {"turn_id": "review-turn", "model": "fixture", "effort": "high"}}) + '\n').encode())
    monkeypatch.setattr(delivery.native, "NATIVE_SESSION_ROOT", tmp_path)
    @delivery.stages.operation
    def operation():
        delivery._native(record, scope, {"reviewerProfile": None})
        event = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": json.dumps({"scope": scope, "decision": "rejected"})}]}}
        child.write_bytes(child.read_bytes() + (json.dumps(event) + '\n').encode())
        delivery._native(record, scope, {"reviewerProfile": None})
    with pytest.raises(ValueError): operation()


def native_command_fixture(root, output, plan, *, yielded=False):
    import hashlib
    path = root / "command.jsonl"
    def e(payload): return {"type": "response_item", "payload": payload}
    meta = {"turn_id": "owner-turn"}
    events = [{"type": "session_meta", "payload": {"id": "parent", "source": "vscode"}},
              e({"type": "message", "role": "user", "content": [{"type": "input_text", "text": delivery.native.AUTHORIZATION_TEXT}], "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}}),
              e({"type": "custom_tool_call", "name": "exec", "call_id": "execute", "input": delivery.stages.native_operation_call(["check", "--repo-root", str(root), "--execution", str(output)], module_name=delivery.MODULE), "internal_chat_message_metadata_passthrough": meta}),
              e({"type": "custom_tool_call_output", "call_id": "execute", "output": "Script running with cell ID 42" if yielded else "Script completed\n" + delivery._check_command_marker(output, plan), "internal_chat_message_metadata_passthrough": meta})]
    if yielded:
        events.extend([e({"type": "function_call", "name": "wait", "call_id": "wait42", "arguments": json.dumps({"cell_id": "42", "yield_time_ms": 1000}), "internal_chat_message_metadata_passthrough": meta}),
                       e({"type": "function_call_output", "call_id": "wait42", "output": "Script completed\n" + delivery._check_command_marker(output, plan), "internal_chat_message_metadata_passthrough": meta})])
    def write():
        lines = [(json.dumps(event) + '\n').encode() for event in events]; path.write_bytes(b''.join(lines))
        def ref(n): return {"sessionPath": str(path), "eventLine": n, "eventSha256IncludingFinalLf": hashlib.sha256(lines[n-1]).hexdigest()}
        return {"schemaVersion": "native-verified-delivery-check-command/v1", "authorization": ref(2), "invocation": ref(3), "result": ref(4),
                "continuations": [{"invocation": ref(5), "result": ref(6)}] if yielded else []}
    return events, write


@pytest.mark.parametrize("yielded", [False, True])
def test_native_checker_command_binds_real_call_result_and_optional_wait(monkeypatch, tmp_path, yielded):
    output = tmp_path / "execution"; delivery._write(output / "checks/receipt.json", {"actual": "checker receipt"})
    plan = {"planHash": "fixed"}
    monkeypatch.setattr(delivery.native, "NATIVE_SESSION_ROOT", tmp_path)
    events, write = native_command_fixture(tmp_path, output, plan, yielded=yielded)
    record = write()
    assert delivery._verify_check_origin(tmp_path, output, plan, record)["receipt"] == delivery._ref(output / "checks/receipt.json")
    if yielded:
        events[4]["payload"]["arguments"] = json.dumps({"cell_id": "other"})
    else:
        events[2]["payload"]["input"] = 'text("fake checked result");\n'
    with pytest.raises(ValueError): delivery._verify_check_origin(tmp_path, output, plan, write())


def test_forged_checker_receipt_cannot_supply_native_origin(monkeypatch, tmp_path):
    output = tmp_path / "execution"; delivery._write(output / "checks/receipt.json", {"actual": "not an executed checker"})
    with pytest.raises(FileNotFoundError): delivery._verify_check_command(tmp_path, output, {"planHash": "fixed"})
    with pytest.raises(ValueError, match="genuine"):
        delivery._verify_check_origin(tmp_path, output, {"planHash": "fixed"}, {"status": "passed", "returnCode": 0})


@pytest.mark.parametrize("mutation", ["task", "graph", "native"])
def test_readback_mid_io_change_refuses_success_receipt(rig, monkeypatch, mutation):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls)
    original_transport = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original_transport(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    real_snapshot = delivery._snapshot
    count = [0]; revoked = [False]
    def read(plan):
        result = real_snapshot(plan); count[0] += 1
        if count[0] == 2:
            if mutation == "task": monkeypatch.setattr(delivery, "task_requirement_binding", lambda *a, **k: {"changed": True})
            elif mutation == "graph": (rig["output"] / "evaluation-candidate.json").write_text('{}')
            else: revoked[0] = True
        return result
    if mutation == "native":
        def assert_guard(*a, **k):
            if revoked[0]: raise ValueError("native revoked during readback")
        monkeypatch.setattr(delivery.stages, "assert_native_guard", assert_guard)
    monkeypatch.setattr(delivery, "_snapshot", read)
    with pytest.raises(ValueError): delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    assert len(calls) == 1
    assert (rig["output"] / "publication/commit.json").exists()
    assert not (rig["output"] / "publication/result.json").exists()


@pytest.mark.parametrize("field", ["schemaVersion", "deliveryPlanHash", "documentIds", "intent"])
def test_completion_result_cannot_be_rebound_to_other_plan_intent_or_schema(rig, monkeypatch, field):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls)
    original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
    delivery.publish(rig["root"], rig["output"], connection=rig["db"])
    path = rig["output"] / "publication/result.json"; result = json.loads(path.read_bytes()); result[field] = ["other"] if field == "documentIds" else "other"
    path.write_bytes(delivery.context_api._bytes(result))
    artifact = {"schemaVersion": delivery.PUBLICATION_SCHEMA, "taskId": "task", "execution": str(rig["output"]), "deliveryPlan": delivery._ref(rig["output"] / "plan.json"), "publicationResult": delivery._ref(path)}
    with pytest.raises(ValueError, match="binding"):
        delivery.verify_completion(rig["root"], artifact, task_id="task", source_question_ids={"original"}, task_requirement=rig["plan"]["taskRequirement"], connection=rig["db"])


def prepare_actual_completion_ledger(rig, monkeypatch):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank.feedback_task_requirements import task_requirement_binding
    db = daily._private_db(rig["root"] / "private/ledger.sqlite3")
    with db:
        db.execute("INSERT INTO improvement_tasks(task_id,title,need,target,created_at,updated_at) VALUES('task','Title','Need','original','before','before')")
    daily.reconcile(db, {"submissions": [], "receipts": [], "cases": [], "caseReports": {}, "aiQuestions": [dict(sourcePath='memos/fixture', questionId='original', createdAt='2026-10-01T00:00:00Z', textHash='a' * 64, visibility='public')]}, source='fixture')
    cid = db.execute("SELECT candidate_id FROM ai_question_candidates").fetchone()[0]
    daily.promote_ai_candidate(db, candidate_id=cid, title='Title', need='Need', target='original', existing_task_id='task')
    requirement = task_requirement_binding(db, 'task')
    rig["plan"]["taskRequirement"] = requirement
    rig["plan"].pop("planHash")
    rig["plan"] = delivery._seal(rig["plan"])
    # The fixture environment closure reads the original plan dict. Keep that
    # same dict identity while updating the new seal and requirement.
    original_plan = delivery._environment(None, None, None)[0]
    original_plan.update(rig["plan"])
    monkeypatch.setattr(delivery, "_environment", lambda *a, **k: (deepcopy(rig["plan"]), deepcopy(rig["context"]), {"projected": {"choiceTextList": [str(i) for i in range(5)]}}, deepcopy(rig["effective"]), deepcopy(rig["evidence"])))
    (rig["output"] / "plan.json").write_bytes(delivery.context_api._bytes(rig["plan"]))
    monkeypatch.setattr(delivery, "task_requirement_binding", task_requirement_binding)
    rig["db"] = db
    return daily, db


def test_real_completion_dispatch_transaction_closes_once_with_live_proof(rig, monkeypatch):
    import contextlib
    daily, db = prepare_actual_completion_ledger(rig, monkeypatch)
    try:
        snapshots = prepare_flow(rig, monkeypatch)
        calls = []; transport = fake_transport(snapshots, calls); original = delivery.uploader.upload_atomic_maintenance_questions
        monkeypatch.setattr(delivery.uploader, "upload_atomic_maintenance_questions", lambda p, label, **kw: original(p, label, **kw) if kw.get("dry_run") else transport(p, label, **kw))
        delivery.publish(rig["root"], rig["output"], connection=db)
        artifact = {"schemaVersion": delivery.PUBLICATION_SCHEMA, "taskId": "task", "execution": str(rig["output"]), "deliveryPlan": delivery._ref(rig["output"] / "plan.json"), "publicationResult": delivery._ref(rig["output"] / "publication/result.json")}
        path = rig["output"] / "completion.json"; delivery._write(path, artifact)
        @contextlib.contextmanager
        def readonly():
            connection = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()[2]); connection.row_factory = sqlite3.Row
            try: yield connection
            finally: connection.close()
        monkeypatch.setattr(delivery.checker, "readonly_connection", readonly)
        kwargs = dict(task_id='task', expected_status='open', reason=delivery.COMPLETION_REASON, artifact_ref=path, artifact_sha256=delivery._ref(path)['sha256'], repo_root=rig['root'])
        first = daily.record_improvement_publication(db, **kwargs)
        assert daily.record_improvement_publication(db, **kwargs) == first
        for override in ({"expected_status": "blocked"}, {"reason": "different unapproved reason"}):
            with pytest.raises(ValueError, match="approval"):
                daily.record_improvement_publication(db, **{**kwargs, **override})
        assert db.execute("SELECT status FROM improvement_tasks WHERE task_id='task'").fetchone()[0] == 'completed'
        assert db.execute("SELECT COUNT(*) FROM improvement_result_history WHERE status='completed'").fetchone()[0] == 1
        ledger = Path(db.execute("PRAGMA database_list").fetchone()[2])
        monkeypatch.setattr(daily, "DEFAULT_DB", ledger)
        result = delivery.record_completion(rig["root"], rig["output"], expected_status="open", reason=delivery.COMPLETION_REASON)
        assert result["resultId"] == first and result["status"] == "completed"
        assert ledger.with_suffix(".backup.sqlite3").exists()
        with pytest.raises(ValueError, match="argv"):
            delivery.record_completion(rig["root"], rig["output"], expected_status="blocked", reason="unapproved reason")
    finally:
        db.close()


@pytest.mark.parametrize("failure_at", [1, 2])
def test_completion_transaction_terminal_failure_rolls_back_status_and_history(tmp_path, monkeypatch, failure_at):
    from tools.question_bank import feedback_daily as daily
    from tools.question_bank.feedback_task_requirements import task_requirement_binding
    db = daily._private_db(tmp_path / 'private/ledger.sqlite3')
    try:
        with db:
            db.execute("INSERT INTO improvement_tasks(task_id,title,need,target,created_at,updated_at) VALUES('task','Title','Need','original','before','before')")
        daily.reconcile(db, {"submissions": [], "receipts": [], "cases": [], "caseReports": {}, "aiQuestions": [dict(sourcePath='memos/fixture', questionId='original', createdAt='2026-10-01T00:00:00Z', textHash='a' * 64, visibility='public')]}, source='fixture')
        cid = db.execute("SELECT candidate_id FROM ai_question_candidates").fetchone()[0]
        daily.promote_ai_candidate(db, candidate_id=cid, title='Title', need='Need', target='original', existing_task_id='task')
        path = tmp_path / 'completion.json'; path.write_text(json.dumps({'schemaVersion': delivery.PUBLICATION_SCHEMA}))
        checks = [0]
        def terminal(connection):
            assert connection.in_transaction
            checks[0] += 1
            if checks[0] == failure_at: raise ValueError('native graph revoked inside transaction')
        monkeypatch.setattr(feedback_publication_result, 'verify_improvement_publication', lambda *a, **k: delivery._CompletionVerification({'runId': 'test'}, terminal, {'expectedStatus': 'open', 'reason': 'verified delivery'}))
        with pytest.raises(ValueError, match='revoked'):
            daily.record_improvement_publication(db, task_id='task', expected_status='open', reason='verified delivery', artifact_ref=path, artifact_sha256=delivery._ref(path)['sha256'], repo_root=tmp_path)
        assert db.execute("SELECT status FROM improvement_tasks WHERE task_id='task'").fetchone()[0] == 'open'
        assert db.execute("SELECT COUNT(*) FROM improvement_result_history WHERE status='completed'").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("mutation", ["wrong_call", "wrong_turn", "row_order", "missing_marker", "old_plan", "old_hash", "count", "error_envelope", "nested_success"])
def test_native_check_command_rejects_fake_success_or_binding_drift(monkeypatch, tmp_path, mutation):
    output = tmp_path / 'execution'; delivery._write(output / 'checks/receipt.json', {'actual': 'receipt'})
    plan = {'planHash': 'fixed'}
    events, write = native_command_fixture(tmp_path, output, plan)
    monkeypatch.setattr(delivery.native, 'NATIVE_SESSION_ROOT', tmp_path)
    result = events[3]['payload']
    if mutation == 'wrong_call': result['call_id'] = 'foreign-call'
    elif mutation == 'wrong_turn': result['internal_chat_message_metadata_passthrough'] = {'turn_id': 'other'}
    elif mutation == 'row_order': events[2], events[3] = events[3], events[2]
    elif mutation == 'missing_marker': result['output'] = 'Script completed\nno receipt'
    elif mutation == 'old_plan': result['output'] = result['output'].replace('"deliveryPlanHash":"fixed"', '"deliveryPlanHash":"old"')
    elif mutation == 'old_hash': result['output'] = result['output'].replace(delivery._ref(output / 'checks/receipt.json')['sha256'], '0' * 64)
    elif mutation == 'count': result['output'] = result['output'].replace('"checkedCommandCount":5', '"checkedCommandCount":0')
    elif mutation == 'error_envelope': result['output'] = result['output'].replace('Script completed', 'Script failed')
    elif mutation == 'nested_success': result['output'] = 'Script interrupted\n' + result['output']
    with pytest.raises(ValueError): delivery._verify_check_origin(tmp_path, output, plan, write())


def test_first_revision_record_detects_post_write_artifact_mutation(rig):
    def tamper(phase):
        if phase == 'revision_saved':
            path = delivery._revision_path(rig['root'], rig['plan'])
            value = json.loads(path.read_bytes()); value['effectiveHash'] = 'changed'
            path.write_bytes(delivery.context_api._bytes(value))
    with pytest.raises(ValueError): save_revision(rig, tamper)


def test_evaluation_input_uses_current_supplementary_and_reference_values(sample):
    _, ids, base, candidate, evidence, _ = sample
    effective, _ = delivery._revision(base, candidate, ids, evidence)
    for row in effective['questions']:
        if row['questionId'] in ids:
            row['suggestedQuestions'] = []
            row['suggestedQuestionDetails'] = []
            row['explanationReferences'] = [{'source': 'fresh official'}]
    question = delivery._evaluation_question({'projected': {'suggestedQuestionDetailsByChoice': [['stale']] * 5}}, effective,
                                            {'officialOrder': [{'questionId': i} for i in ids]})
    assert question['projected']['suggestedQuestionDetailsByChoice'] == [[], [], [], [], []]
    assert question['projected']['explanationReferencesByChoice'] == [[{'source': 'fresh official'}]] * 5


@pytest.mark.parametrize('moment', ['before_write', 'during_readback'])
def test_new_task_status_decision_cannot_be_silently_published_or_completed(rig, monkeypatch, moment):
    snapshots = prepare_flow(rig, monkeypatch)
    calls = []; transport = fake_transport(snapshots, calls); original = delivery.uploader.upload_atomic_maintenance_questions
    monkeypatch.setattr(delivery.uploader, 'upload_atomic_maintenance_questions', lambda p, label, **kw: original(p, label, **kw) if kw.get('dry_run') else transport(p, label, **kw))
    if moment == 'before_write':
        with rig['db']: rig['db'].execute("UPDATE improvement_tasks SET status='blocked'")
    else:
        read = delivery._snapshot; count = [0]
        def readback(plan):
            result = read(plan); count[0] += 1
            if count[0] == 2:
                with rig['db']: rig['db'].execute("UPDATE improvement_tasks SET status='blocked'")
            return result
        monkeypatch.setattr(delivery, '_snapshot', readback)
    with pytest.raises(ValueError, match='status changed'): delivery.publish(rig['root'], rig['output'], connection=rig['db'])
    assert len(calls) == (0 if moment == 'before_write' else 1)
    assert not (rig['output'] / 'publication/result.json').exists()


def test_check_cli_marker_binds_returned_verified_receipt_not_later_file_bytes(tmp_path):
    output = tmp_path / 'execution'; result = {'deliveryPlanHash': 'verified', 'count': 202}
    delivery._write(output / 'checks/receipt.json', result)
    actual_marker = delivery._check_command_marker(output, {'planHash': 'verified'}, completed_receipt=result)
    (output / 'checks/receipt.json').write_text('{"forged":true}')
    assert actual_marker != delivery._check_command_marker(output, {'planHash': 'verified'})
    assert actual_marker == delivery._check_command_marker(output, {'planHash': 'verified'}, completed_receipt=result)
