"""Audited verified25 delivery with immutable authoring revisions and atomic publication.

The original saved canonical/receipt remains authoritative and unchanged. A
separate, explicitly approved revision supplies current authoring fields and,
when required, narrowly scoped explanations and supplementary questions. Dedicated checkpoints never impersonate ordinary workVersions. Every
native approval and every current policy is replayed before production writes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import subprocess
import sys

from scripts.check import verified_feedback_contract as checker
from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as gas
from scripts.upload import upload_questions_to_firestore as uploader
from tools.question_bank import feedback_verified_review_context as context_api
from tools.question_bank import feedback_verified_stage_execution as stages
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_review_console import independent_approval as native
from tools.question_review_console.evaluation import EvaluationStore
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.question_patch_proposal import _canonical_file_locks

MODULE = "tools.question_bank.feedback_verified_delivery"
PLAN_SCHEMA = "feedback-verified-delivery-plan/v1"
NODE_SCHEMA = "feedback-verified-delivery-node/v1"
SCOPE_SCHEMA = "feedback-verified-delivery-scope/v1"
RECEIPT_SCHEMA = "feedback-verified-delivery-receipt/v1"
PUBLICATION_SCHEMA = "feedback-verified-improvement-publication/v1"
REVISION_SCHEMA = "gas-shunin-verified-authoring-revision/v1"
AUTHORING_FIELDS = frozenset({"isCalculationQuestion", "questionIntent", "questionLearningPatternId"})
MAINTENANCE_FIELDS = frozenset({"explanationText", "suggestedQuestions", "suggestedQuestionDetails", "explanationReferences"})
ALIASES = {"suggestedQuestionDetailsByChoice": "suggestedQuestionDetails"}
KINDS = ("revision", "stage", "evaluation", "publication")
COMPLETION_REASON = "独立監査・正式revision・限定公開・全対象live readback一致を確認。"


class _CompletionVerification(dict):
    """In-process proof carrying a terminal guard; never constructible from JSON."""
    def __init__(self, value, terminal, completion_contract):
        super().__init__(value)
        self.__terminal = terminal
        self.__completion_contract = deepcopy(completion_contract)

    def assert_completion_arguments(self, expected_status, reason):
        if self.__completion_contract != {"expectedStatus": expected_status, "reason": reason}:
            raise ValueError("completion CAS/reason differs from independent publication approval")

    def assert_current(self, transaction_connection):
        self.__terminal(transaction_connection)


def _ref(path):
    return context_api._ref(path)


def _json(root, ref):
    path, raw = context_api._bound(root, ref)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("bound JSON object required")
    return path, value


def _write(path, value):
    """Exclusive, durable, byte-identical replay; no mutable success flags."""
    path = context_api._physical(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = context_api._bytes(value)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("immutable delivery artifact conflict")
    else:
        context_api.saver._exclusive(path, raw)
    path.chmod(0o600)
    return _ref(path)


def _seal(value, key="planHash"):
    return {**value, key: sha256_json(value)}


def _verify_seal(value, key="planHash"):
    if not isinstance(value, dict) or value.get(key) != sha256_json({k: v for k, v in value.items() if k != key}):
        raise ValueError("delivery hash seal differs")


def _dependencies():
    modules = (sys.modules[__name__], stages, uploader, checker, gas, native)
    files = {Path(module.__file__).resolve() for module in modules}
    from tools.question_bank import feedback_publication_result, feedback_daily
    from tools.question_review_console import evaluation
    files.update({Path(__file__).resolve().parents[2] / "config/question_learning_patterns.json",
                  Path(feedback_publication_result.__file__).resolve(), Path(feedback_daily.__file__).resolve(), Path(evaluation.__file__).resolve(),
                  Path(evaluation.__file__).with_name("evaluation_result.schema.json").resolve()})
    return {str(p): context_api._hash(p.read_bytes()) for p in sorted(files)}


def _current(plan):
    for path, digest in plan["inputs"].items():
        if context_api._hash(context_api._physical(path).read_bytes()) != digest:
            raise ValueError("delivery fixed input or implementation changed")


def _private(root, output):
    return context_api._private(context_api._physical(root), output)


def _rows(bundle):
    rows, ids = checker._bundle(bundle)
    return {identity: row for identity, row in zip(ids, rows, strict=True)}, ids


def _evidence(root, evidence, ids):
    if (set(evidence) != {"schemaVersion", "referenceDate", "sources", "evidenceByDocument"}
            or evidence["schemaVersion"] != "feedback-verified-delivery-evidence/v1"
            or evidence["referenceDate"] != datetime.now(timezone.utc).date().isoformat()
            or not isinstance(evidence["sources"], dict) or not evidence["sources"]
            or set(evidence["evidenceByDocument"]) != set(ids)):
        raise ValueError("fresh complete per-document primary evidence required")
    inputs = {}
    for name, ref in evidence["sources"].items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("named primary evidence required")
        path, raw = context_api._bound(root, ref)
        inputs[str(path)] = context_api._hash(raw)
    for identity, values in evidence["evidenceByDocument"].items():
        if (not isinstance(values, dict) or set(values) != {"examLaw", "currentLaw", "category"}
                or any(not isinstance(keys, list) or not keys or any(key not in evidence["sources"] for key in keys) for keys in values.values())):
            raise ValueError("exam/current-law/category evidence must cover every sibling")
    return inputs


def _reason(value, keys):
    if (not isinstance(value, dict) or set(value) != {"present", "value", "reason", "evidenceKeys"}
            or type(value["present"]) is not bool
            or not isinstance(value["reason"], str) or not value["reason"].strip()
            or not isinstance(value["evidenceKeys"], list) or not value["evidenceKeys"]
            or len(set(value["evidenceKeys"])) != len(value["evidenceKeys"])
            or any(key not in keys for key in value["evidenceKeys"])
            or (not value["present"] and value["value"] is not None)):
        raise ValueError("field presence, reason and fixed evidence are required")


def _learning_patterns():
    path = Path(__file__).resolve().parents[2] / "config/question_learning_patterns.json"
    return {p["id"] for p in json.loads(path.read_bytes())["patterns"]}


def _revision(base, candidate, ids, evidence):
    if (set(candidate) != {"schemaVersion", "revisionKind", "decisionsByDocument"}
            or candidate["schemaVersion"] != "feedback-verified-authoring-candidate/v1"
            or candidate["revisionKind"] not in {"authoring_only", "maintenance"}
            or set(candidate["decisionsByDocument"]) != set(ids)):
        raise ValueError("complete explicit-ID authoring revision required")
    effective = deepcopy(base)
    by_id, all_ids = _rows(effective)
    before, _ = _rows(base)
    deltas = []
    for identity in ids:
        decisions = candidate["decisionsByDocument"][identity]
        allowed = AUTHORING_FIELDS | (MAINTENANCE_FIELDS if candidate["revisionKind"] == "maintenance" else set())
        if set(decisions) != allowed:
            raise ValueError("revision has non-owned or missing authoring fields")
        for field, decision in decisions.items():
            _reason(decision, evidence["sources"])
            if decision["present"] is not True:
                raise ValueError("revision-owned fields must be present; deletion is unsupported")
            value = decision["value"]
            if (field == "isCalculationQuestion" and type(value) is not bool
                    or field == "questionIntent" and value not in {"select_correct", "select_incorrect"}
                    or field == "questionLearningPatternId" and value not in _learning_patterns()):
                raise ValueError("invalid authoring field value")
            previous = before[identity]
            if field not in previous or previous[field] != value:
                deltas.append({"questionId": identity, "field": field, "beforePresent": field in previous,
                               "before": previous.get(field), "after": deepcopy(value)})
            by_id[identity][field] = deepcopy(value)
    # No guessed masks: prove the actual uploader's projection is unchanged for
    # the entire nonempty year, including every non-target row and optional field.
    if not deltas:
        raise ValueError("revision must make a nonempty approved field change")
    for identity in all_ids:
        old_dto, new_dto = uploader.build_doc_data_base(before[identity]), uploader.build_doc_data_base(by_id[identity])
        allowed_public = MAINTENANCE_FIELDS if identity in ids and candidate["revisionKind"] == "maintenance" else set()
        if any((f in old_dto) != (f in new_dto) or old_dto.get(f) != new_dto.get(f)
               for f in set(old_dto) | set(new_dto) if f not in allowed_public):
            raise ValueError("revision changed a non-owned public DTO field")
        if identity not in ids and before[identity] != by_id[identity]:
            raise ValueError("non-target authoring row changed")
        if identity in ids and candidate["revisionKind"] == "maintenance":
            details = by_id[identity]["suggestedQuestionDetails"]
            if (not isinstance(details, list) or len(details) > 3
                    or any(not isinstance(d, dict) or set(d) != {"question", "answer"}
                           or any(not isinstance(v, str) or not v.strip() for v in d.values()) for d in details)
                    or by_id[identity]["suggestedQuestions"] != [d["question"] for d in details]):
                raise ValueError("revised supplementary question mirror/shape differs")
    return effective, deltas


@stages.operation
def bootstrap(root, context_dir, output, candidate_path, evidence_path, *, connection):
    root = context_api._physical(root); output = _private(root, output)
    context, review, refs = stages._load_context(root, context_dir, connection)
    _, base = _json(root, context["identity"]["canonical"])
    all_rows, all_ids = _rows(base); ids = context["identity"]["siblingIds"]
    if len(ids) != 5 or set(review["documentsById"]) != set(ids):
        raise ValueError("complete five-statement verified original required")
    candidate_ref, candidate = context_api._json_snapshot(candidate_path)
    evidence_ref, evidence = context_api._json_snapshot(evidence_path)
    evidence_inputs = _evidence(root, evidence, ids)
    effective, deltas = _revision(base, candidate, ids, evidence)
    inputs = {**context["inputs"], **_dependencies(), **evidence_inputs}
    inputs.update({ref["ref"]: ref["sha256"] for ref in [*refs.values(), candidate_ref, evidence_ref]})
    plan = _seal({"schemaVersion": PLAN_SCHEMA, "repoRoot": str(root), "contextDirectory": str(context_dir),
                  "contextHash": context["contextHash"], "identity": context["identity"],
                  "taskId": context["taskRequirement"]["requirement"]["taskId"], "taskRequirement": context["taskRequirement"],
                  "contextFiles": refs, "candidate": candidate_ref, "evidence": evidence_ref,
                  "allDocumentIds": all_ids, "allDocumentCount": len(all_ids),
                  "publicProjectionHash": sha256_json({i: uploader.build_doc_data_base(all_rows[i]) for i in all_ids}),
                  "effectiveHash": sha256_json(effective), "revisionDeltas": deltas,
                  "revisionKind": candidate["revisionKind"],
                  "effectivePublicProjectionHash": sha256_json({r["questionId"]: uploader.build_doc_data_base(r) for r in effective["questions"]}),
                  "requiredStages": context["preflight"]["requiredStageIds"], "policies": context["policies"],
                  "inputs": inputs})
    _current(plan); _write(output / "plan.json", plan); _current(plan)
    return plan


def _environment(root, output, connection):
    root = context_api._physical(root); output = _private(root, output)
    plan = json.loads((output / "plan.json").read_bytes()); _verify_seal(plan)
    if plan.get("schemaVersion") != PLAN_SCHEMA or plan.get("repoRoot") != str(root):
        raise ValueError("dedicated delivery plan required")
    _current(plan)
    cache = stages._OPERATION_CONTEXT.get()
    cache_key = ("delivery-environment", str(output), plan["planHash"], id(connection))
    if cache is not None and cache_key in cache:
        environment, guard, old_native_files = cache[cache_key]
        if task_requirement_binding(connection, plan["taskId"]) != plan["taskRequirement"]:
            raise ValueError("task requirement changed")
        _evidence(root, environment[4], plan["identity"]["siblingIds"])
        for ref in old_native_files:
            context_api._bound(root, ref)
        stages.assert_native_guard(guard, terminal=True)
        return deepcopy(environment)
    # Pin the historical native chain before genuine context verification. A
    # single operation may memoize expensive load_saved, but a later revocation,
    # changed approval file, or semantic native append must still stop it.
    stored_context = _json(root, plan["contextFiles"]["context.json"])[1]
    old_receipt = _json(root, stored_context["save"]["receipt"])[1]
    old_native_approvals = [old_receipt["approvalEvidence"]]
    old_native_files = [stored_context["save"]["receipt"]]
    old_plan_path, old_plan = _json(root, stored_context["save"]["plan"])
    if old_plan.get("lawReviewRequired"):
        for stage_id in context_api.saver.LAW_STAGES:
            path = old_plan_path.parent / ("law-" + stage_id + "-approval.json")
            old_native_files.append(_ref(path))
            old_native_approvals.append(_json(root, old_native_files[-1])[1])
    guard = stages.capture_native_guard(old_native_approvals)
    context, review, refs = stages._load_context(root, plan["contextDirectory"], connection)
    if context["contextHash"] != plan["contextHash"] or refs != plan["contextFiles"] or context["policies"] != plan["policies"]:
        raise ValueError("genuine context/current policy differs")
    if task_requirement_binding(connection, plan["taskId"]) != plan["taskRequirement"]:
        raise ValueError("task requirement changed")
    _, base = _json(root, context["identity"]["canonical"])
    by_id, all_ids = _rows(base)
    if (all_ids != plan["allDocumentIds"] or len(all_ids) != plan["allDocumentCount"]
            or sha256_json({i: uploader.build_doc_data_base(by_id[i]) for i in all_ids}) != plan["publicProjectionHash"]
            or plan["requiredStages"] != context["preflight"]["requiredStageIds"]):
        raise ValueError("complete source count/IDs/projection/applicable stages differ")
    _, candidate = _json(root, plan["candidate"]); _, evidence = _json(root, plan["evidence"])
    _evidence(root, evidence, plan["identity"]["siblingIds"])
    effective, deltas = _revision(base, candidate, plan["identity"]["siblingIds"], evidence)
    if sha256_json(effective) != plan["effectiveHash"] or deltas != plan["revisionDeltas"]:
        raise ValueError("effective authoring replay differs")
    environment = (plan, context, review, effective, evidence)
    for ref in old_native_files:
        context_api._bound(root, ref)
    stages.assert_native_guard(guard, terminal=True)
    if cache is not None:
        cache[cache_key] = (deepcopy(environment), guard, old_native_files)
    return environment


def _node_dir(output, kind, stage_id=None):
    if kind not in KINDS or (kind == "stage") != bool(stage_id):
        raise ValueError("explicit delivery kind/stage required")
    if stage_id is not None and (not isinstance(stage_id, str) or not stage_id.isidentifier()):
        raise ValueError("invalid stage ID")
    return Path(output) / "reviews" / ("stage-" + stage_id if kind == "stage" else kind)


def _scope(root, output, node):
    directory = _node_dir(output, node["kind"], node["stageId"])
    argv = ["scope", "--repo-root", str(root), "--execution", str(output), "--kind", node["kind"]]
    if node["stageId"]:
        argv += ["--stage-id", node["stageId"]]
    return {"schemaVersion": SCOPE_SCHEMA, "action": "audit_verified_delivery_" + node["kind"],
            "planHash": node["planHash"], "deliveryPlanHash": node["deliveryPlanHash"],
            "deliveryPlan": _ref(Path(output) / "plan.json"),
            "node": _ref(directory / "plan.json"), "taskId": node["taskId"],
            "identity": node["identity"], "kind": node["kind"], "stageId": node["stageId"],
            "formalPatchSaved": False, "publicationApproved": False, "publicationRequested": node["kind"] == "publication",
            "nativeCreationCall": stages.native_operation_call(argv, module_name=MODULE)}


def _native(approval, scope_value, node):
    guard = stages.capture_native_guard([approval])
    cache = stages._OPERATION_CONTEXT.get()
    cache_key = ("delivery-native", sha256_json(approval), sha256_json(scope_value), sha256_json(node.get("reviewerProfile")))
    if cache is not None and cache_key in cache:
        binding, initial_guard = cache[cache_key]
        stages.assert_native_guard(initial_guard, terminal=True)
        return deepcopy(binding)
    binding = native.verify_independent_approval(approval, scope_value, production=True)
    _, _, lines = native._event(approval["reviewerFinal"], production=True)
    profiles = []
    latest = None
    for number, raw in enumerate(lines, 1):
        event = json.loads(raw); payload = event.get("payload", {})
        if event.get("type") == "turn_context" and payload.get("turn_id") == binding["reviewerTurnId"]:
            profiles.append({"model": payload.get("model"), "reasoningEffort": payload.get("effort")})
        if event.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
            try:
                verdict = json.loads(native._text(payload))
            except (ValueError, TypeError):
                continue
            if isinstance(verdict, dict) and verdict.get("scope") == scope_value:
                latest = number
    if latest != approval["reviewerFinal"]["eventLine"]:
        raise ValueError("native delivery approval superseded or revoked")
    expected = node.get("reviewerProfile")
    if not profiles or any(profile != profiles[0] for profile in profiles) or expected and any(profile != expected for profile in profiles):
        raise ValueError("native reviewer profile differs from current policy")
    binding = {**binding, "reviewerProfile": profiles[0]}
    stages.assert_native_guard(guard, terminal=True)
    if cache is not None:
        cache[cache_key] = (deepcopy(binding), guard)
    return binding


def _field_value(row, field):
    canonical = ALIASES.get(field, field)
    return canonical in row, deepcopy(row.get(canonical))


def _stage_candidate(candidate, stage_id, plan, effective, evidence):
    if (set(candidate) != {"schemaVersion", "stageId", "decisionsByDocument"}
            or candidate["schemaVersion"] != "feedback-verified-stage-decisions/v1" or candidate["stageId"] != stage_id):
        raise ValueError("dedicated current stage decisions required")
    ids = plan["identity"]["siblingIds"]
    if set(candidate["decisionsByDocument"]) != set(ids):
        raise ValueError("stage must explicitly decide every sibling")
    rows, _ = _rows(effective)
    fields = plan["policies"]["maintenance"][stage_id]["issueFields"]
    for identity, decisions in candidate["decisionsByDocument"].items():
        if set(decisions) != set(fields):
            raise ValueError("stage ownership/field coverage differs")
        for field, decision in decisions.items():
            _reason(decision, evidence["sources"])
            if (decision["present"], decision["value"]) != _field_value(rows[identity], field):
                raise ValueError("remaining stage field differences require an audited formal revision")
            if field in AUTHORING_FIELDS and decision["present"] is not True:
                raise ValueError("required authoring field has not been formally decided")
    return deepcopy(candidate)


def _revision_path(root, plan):
    base = Path(plan["identity"]["canonical"]["ref"])
    return base.parent / "revisions" / (plan["planHash"] + ".json")


def _verify_revision(root, output, plan, connection):
    node, receipt = _verify_node(root, output, "revision", connection=connection)
    revision_path = _revision_path(root, plan)
    expected = {"schemaVersion": REVISION_SCHEMA, "deliveryPlanHash": plan["planHash"],
                "base": plan["identity"]["canonical"], "taskRequirement": plan["taskRequirement"],
                "revisionKind": plan["revisionKind"], "deltas": plan["revisionDeltas"], "effectiveHash": plan["effectiveHash"],
                "approvalBindings": receipt["bindings"]}
    if json.loads(revision_path.read_bytes()) != expected or receipt.get("revision") != _ref(revision_path):
        raise ValueError("saved authoring revision differs")
    return receipt


def _type_execution(root, directory, plan, effective, connection):
    receipt = stages.inspect(root, directory, connection=connection)
    _, type_plan = context_api._json_snapshot(Path(directory) / "stage-plan.json")
    _, output = context_api._json_snapshot(Path(directory) / "stage-output.json")
    if (receipt["phase"] != "classification" or receipt["contextHash"] != plan["contextHash"]
            or {p["phase"] for p in type_plan["priorExecutions"]} != {"aggregate_a", "aggregate_b"}
            or len({p["receipt"]["binding"]["reviewerSessionId"] for p in type_plan["priorExecutions"]}) != 2):
        raise ValueError("genuine independent A/B/classification chain required")
    rows, _ = _rows(effective)
    decisions = output["output"]["decisionsByDocument"]
    ids = plan["identity"]["siblingIds"]
    if set(decisions) != set(ids) or any(decisions[i] != {f: rows[i].get(f) for f in stages.FIELDS} for i in ids):
        raise ValueError("genuine classification and effective authoring differ")
    allowed = [d for d in plan["revisionDeltas"] if d["field"] in stages.FIELDS]
    if receipt["remainingFieldDiffs"] != allowed:
        # Both are ID keyed in meaning; never compare or bind by array position.
        sort = lambda values: sorted(values, key=lambda x: (x["questionId"], x["field"]))
        if sort(receipt["remainingFieldDiffs"]) != sort(allowed):
            raise ValueError("classification residual is not exactly the saved revision")
    return {"directory": str(directory), "receipt": _ref(Path(directory) / "execution-receipt.json"),
            "binding": receipt["binding"], "aggregateBindings": [p["receipt"]["binding"] for p in type_plan["priorExecutions"]]}


def _evaluation_question(review, effective, context):
    rows, _ = _rows(effective)
    selected = [rows[s["questionId"]] for s in context["officialOrder"]]
    projected = deepcopy(review["projected"])
    projected["correctChoiceText"] = [row["correctChoiceText"] for row in selected]
    projected["explanationText"] = [row["explanationText"] for row in selected]
    projected["classificationByChoice"] = [{f: row[f] for f in AUTHORING_FIELDS} for row in selected]
    for destination, field in {"suggestedQuestionsByChoice": "suggestedQuestions", "suggestedQuestionDetailsByChoice": "suggestedQuestionDetails",
                               "explanationReferencesByChoice": "explanationReferences", "lawReferencesByChoice": "lawReferences",
                               "lawRevisionFactsByChoice": "lawRevisionFacts"}.items():
        projected[destination] = [deepcopy(row.get(field)) for row in selected]
    return {"projected": projected, "documentsById": {row["questionId"]: deepcopy(row) for row in selected},
            "officialOrder": deepcopy(context["officialOrder"])}


def _validate_schema(value, schema):
    """Strict subset used by the existing evaluation schema, without a new SDK.

    Unknown schema vocabulary fails closed instead of silently ignoring a new
    constraint. The content/score/truth gate remains EvaluationStore's validator.
    """
    vocabulary = {"$schema", "type", "additionalProperties", "required", "properties", "items", "minItems", "maxItems", "enum", "description", "minimum", "maximum", "minLength"}
    if set(schema) - vocabulary:
        raise ValueError("unsupported evaluation schema constraint")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "integer": int, "string": str, "boolean": bool}
    if kind not in types or type(value) is not types[kind]:
        raise ValueError("evaluation schema type differs")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("evaluation schema enum differs")
    if kind == "object":
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= set(value) or schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError("evaluation schema properties differ")
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key])
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise ValueError("evaluation schema item count differs")
        for item in value:
            _validate_schema(item, schema["items"])
    elif kind == "string" and len(value) < schema.get("minLength", 0):
        raise ValueError("evaluation schema string is empty")
    elif kind == "integer" and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
        raise ValueError("evaluation schema number outside range")


def _evaluation(candidate, review, effective, context):
    schema = json.loads(Path(sys.modules[EvaluationStore.__module__].__file__).with_name("evaluation_result.schema.json").read_bytes())
    _validate_schema(candidate, schema)
    result = EvaluationStore._validate_result(_evaluation_question(review, effective, context), candidate)
    if result["status"] != "passed" or result["reworkItems"]:
        raise ValueError("independent evaluation requires verified all-choice pass with no rework")
    return result


def _review_count(kind, stage_id):
    return 3 if kind == "stage" and stage_id == "law_audit" else 1


@stages.operation
def prepare(root, output, kind, candidate_path=None, *, connection, stage_id=None, type_execution=None):
    root = context_api._physical(root); output = _private(root, output)
    graph, graph_guard = _capture_delivery(root, output)
    plan, context, review, effective, evidence = _environment(root, output, connection)
    directory = _node_dir(output, kind, stage_id)
    parents = {}; result = None; profile = None; type_chain = None
    if kind == "revision":
        candidate_ref = plan["candidate"]; candidate = _json(root, candidate_ref)[1]
    else:
        parents["revision"] = _ref(Path(output) / "reviews/revision/receipt.json")
        _verify_revision(root, output, plan, connection)
        if kind == "stage":
            if stage_id not in plan["requiredStages"] or candidate_path is None:
                raise ValueError("current applicable stage and candidate required")
            previous_ids = plan["requiredStages"][:plan["requiredStages"].index(stage_id)]
            for previous in previous_ids:
                _verify_node(root, output, "stage", connection=connection, stage_id=previous)
                parents[previous] = _ref(_node_dir(output, "stage", previous) / "receipt.json")
            candidate_ref, candidate = context_api._json_snapshot(candidate_path)
            result = _stage_candidate(candidate, stage_id, plan, effective, evidence)
            profile = plan["policies"]["maintenance"][stage_id]["agentPolicy"].get("independent_review")
            if stage_id == "question_type":
                if type_execution is None:
                    raise ValueError("01 requires genuine native aggregate and classification execution")
                type_chain = _type_execution(root, type_execution, plan, effective, connection)
        else:
            completed = _verify_stages(root, output, plan, connection)
            parents.update({stage: _ref(_node_dir(output, "stage", stage) / "receipt.json") for stage in completed})
            if kind == "evaluation":
                if candidate_path is None:
                    raise ValueError("evaluation worker result required")
                candidate_ref, candidate = context_api._json_snapshot(candidate_path)
                result = {"evaluationInput": _evaluation_question(review, effective, context), "evaluation": _evaluation(candidate, review, effective, context)}
            else:
                _verify_node(root, output, "evaluation", connection=connection)
                _verify_checks(root, output, plan, connection)
                parents["evaluation"] = _ref(_node_dir(output, "evaluation") / "receipt.json")
                parents["checks"] = _ref(Path(output) / "checks/receipt.json")
                candidate_ref, candidate = _prepare_publication(root, output, plan, effective, connection)
                result = candidate
    node = _seal({"schemaVersion": NODE_SCHEMA, "deliveryPlanHash": plan["planHash"], "taskId": plan["taskId"],
                  "identity": plan["identity"], "kind": kind, "stageId": stage_id, "candidate": candidate_ref,
                  "parents": parents, "result": result, "typeExecution": type_chain,
                  "reviewCount": _review_count(kind, stage_id), "reviewerProfile": profile})
    _check_graph(graph, root, plan, guard=graph_guard, connection=connection)
    _json(root, candidate_ref)
    _write(directory / "plan.json", node)
    _check_graph(graph, root, plan, guard=graph_guard, connection=connection)
    _json(root, candidate_ref)
    return node


def _read_node(root, output, kind, stage_id):
    directory = _node_dir(output, kind, stage_id)
    node = json.loads((directory / "plan.json").read_bytes()); _verify_seal(node)
    if (node.get("schemaVersion") != NODE_SCHEMA or node.get("kind") != kind or node.get("stageId") != stage_id):
        raise ValueError("dedicated immutable review node required")
    _json(root, node["candidate"])
    for ref in node["parents"].values():
        _json(root, ref)
    return node


def _node_bindings(root, output, node, approvals):
    if len(approvals) != node["reviewCount"]:
        raise ValueError("required independent native audit count differs")
    bindings = [_native(approval, _scope(root, output, node), node) for approval in approvals]
    if len({b["reviewerSessionId"] for b in bindings}) != len(bindings):
        raise ValueError("independent audit sessions must be distinct")
    return bindings


def _verify_node(root, output, kind, *, connection, stage_id=None):
    node = _read_node(root, output, kind, stage_id)
    directory = _node_dir(output, kind, stage_id)
    receipt = json.loads((directory / "receipt.json").read_bytes())
    approvals = json.loads((directory / "approvals.json").read_bytes())["approvals"]
    expected_keys = {"schemaVersion", "nodePlanHash", "deliveryPlanHash", "kind", "stageId", "bindings", "candidate", "parents"} | ({"revision"} if kind == "revision" else set())
    if (set(receipt) != expected_keys or receipt.get("schemaVersion") != RECEIPT_SCHEMA or receipt.get("nodePlanHash") != node["planHash"]
            or receipt.get("deliveryPlanHash") != node["deliveryPlanHash"]
            or receipt.get("kind") != kind or receipt.get("stageId") != stage_id
            or receipt.get("candidate") != node["candidate"] or receipt.get("parents") != node["parents"]
            or receipt.get("bindings") != _node_bindings(root, output, node, approvals)):
        raise ValueError("current native review receipt differs")
    plan, context, review, effective, evidence = _environment(root, output, connection)
    cache = stages._OPERATION_CONTEXT.get()
    validation_key = ("delivery-node", str(directory), sha256_json(node), sha256_json(receipt), sha256_json(approvals))
    if cache is None or validation_key not in cache:
        _validate_node(root, output, node, plan, context, review, effective, evidence, connection)
        if cache is not None:
            cache[validation_key] = True
    if kind == "evaluation" and any(b["reviewerSessionId"] in _reviewer_sessions(root, output, plan, connection) for b in receipt["bindings"]):
        raise ValueError("evaluation must be independent of every maintenance reviewer")
    return node, receipt


def _validate_node(root, output, node, plan, context, review, effective, evidence, connection):
    expected_profile = plan["policies"]["maintenance"].get(node["stageId"], {}).get("agentPolicy", {}).get("independent_review") if node["kind"] == "stage" else None
    if (node["reviewCount"] != _review_count(node["kind"], node["stageId"])
            or node["reviewerProfile"] != expected_profile):
        raise ValueError("required current audit count/profile was changed")
    if node["deliveryPlanHash"] != plan["planHash"] or node["identity"] != plan["identity"] or node["taskId"] != plan["taskId"]:
        raise ValueError("review belongs to another fixed delivery")
    _, candidate = _json(root, node["candidate"])
    if node["kind"] == "revision":
        if node["candidate"] != plan["candidate"]:
            raise ValueError("revision candidate differs")
    else:
        _verify_revision(root, output, plan, connection)
        if node["parents"].get("revision") != _ref(_node_dir(output, "revision") / "receipt.json"):
            raise ValueError("revision parent changed")
        if node["kind"] == "stage":
            previous_ids = plan["requiredStages"][:plan["requiredStages"].index(node["stageId"])] if node["stageId"] in plan["requiredStages"] else []
            if set(node["parents"]) != {"revision", *previous_ids}:
                raise ValueError("stage order/prior checkpoint coverage differs")
            for previous in previous_ids:
                _verify_node(root, output, "stage", connection=connection, stage_id=previous)
                if node["parents"][previous] != _ref(_node_dir(output, "stage", previous) / "receipt.json"):
                    raise ValueError("prior current stage checkpoint changed")
            if node["stageId"] not in plan["requiredStages"] or node["result"] != _stage_candidate(candidate, node["stageId"], plan, effective, evidence):
                raise ValueError("stage candidate result changed")
            if node["stageId"] == "question_type":
                chain = node["typeExecution"]
                if chain != _type_execution(root, chain["directory"], plan, effective, connection):
                    raise ValueError("01 native chain changed")
        else:
            _verify_stages(root, output, plan, connection)
            required_parents = {"revision", *plan["requiredStages"]}
            if node["kind"] == "publication":
                required_parents |= {"evaluation", "checks"}
            if set(node["parents"]) != required_parents:
                raise ValueError("all required current checkpoint parents must be fixed")
            for stage_id in plan["requiredStages"]:
                if node["parents"][stage_id] != _ref(_node_dir(output, "stage", stage_id) / "receipt.json"):
                    raise ValueError("current stage parent changed")
            if node["kind"] == "evaluation":
                if node["result"] != {"evaluationInput": _evaluation_question(review, effective, context), "evaluation": _evaluation(candidate, review, effective, context)}:
                    raise ValueError("evaluation result changed")
            else:
                _verify_node(root, output, "evaluation", connection=connection)
                _verify_checks(root, output, plan, connection)
                _publication_candidate(root, output, plan, candidate)


@stages.operation
def scope(root, output, kind, *, connection, stage_id=None):
    plan, context, review, effective, evidence = _environment(root, output, connection)
    node = _read_node(root, output, kind, stage_id)
    # Creation is a fixed candidate, not a success or publication gate. Replaying
    # every prior native journal here would make the creation marker spill into
    # a later wait result. record/publish/completion replay the entire chain.
    if node["deliveryPlanHash"] != plan["planHash"] or node["identity"] != plan["identity"]:
        raise ValueError("scope candidate belongs to another delivery")
    _current(plan)
    return _scope(root, output, node)


@stages.operation
def record(root, output, kind, approvals, *, connection, stage_id=None, failure_hook=None):
    approvals = deepcopy(approvals)
    graph, graph_guard = _capture_delivery(root, output)
    guard = stages.capture_native_guard(approvals)
    plan, context, review, effective, evidence = _environment(root, output, connection)
    node = _read_node(root, output, kind, stage_id)
    _validate_node(root, output, node, plan, context, review, effective, evidence, connection)
    bindings = _node_bindings(root, output, node, approvals)
    if kind == "evaluation":
        previous = _reviewer_sessions(root, output, plan, connection)
        if any(b["reviewerSessionId"] in previous for b in bindings):
            raise ValueError("evaluation requires a new independent session")
    directory = _node_dir(output, kind, stage_id)
    receipt = {"schemaVersion": RECEIPT_SCHEMA, "nodePlanHash": node["planHash"],
               "deliveryPlanHash": plan["planHash"], "kind": kind, "stageId": stage_id,
               "bindings": bindings, "candidate": node["candidate"], "parents": node["parents"]}
    stages.assert_native_guard(guard, terminal=True)
    _check_graph(graph, root, plan, guard=graph_guard, connection=connection)
    written_refs = [_write(directory / "approvals.json", {"approvals": approvals})]
    if kind == "revision":
        revision = {"schemaVersion": REVISION_SCHEMA, "deliveryPlanHash": plan["planHash"],
                    "base": plan["identity"]["canonical"], "taskRequirement": plan["taskRequirement"],
                    "revisionKind": plan["revisionKind"], "deltas": plan["revisionDeltas"], "effectiveHash": plan["effectiveHash"], "approvalBindings": bindings}
        destination = _revision_path(root, plan)
        with _canonical_file_locks(root, [destination.relative_to(root)]):
            _check_graph(graph, root, plan, guard=graph_guard, connection=connection); stages.assert_native_guard(guard, terminal=True)
            _write(directory / "save-intent.json", {"node": _ref(directory / "plan.json"), "approvals": _ref(directory / "approvals.json"), "revision": revision})
            if failure_hook:
                failure_hook("intent_saved")
            _check_graph(graph, root, plan, guard=graph_guard, connection=connection); stages.assert_native_guard(guard, terminal=True)
            receipt["revision"] = _write(destination, revision)
            _check_graph(graph, root, plan, guard=graph_guard, connection=connection); stages.assert_native_guard(guard, terminal=True)
            if failure_hook:
                failure_hook("revision_saved")
    _check_graph(graph, root, plan, guard=graph_guard, connection=connection); stages.assert_native_guard(guard, terminal=True)
    written_refs.append(_write(directory / "receipt.json", receipt))
    if failure_hook:
        failure_hook("receipt_saved")
    _verify_node(root, output, kind, connection=connection, stage_id=stage_id)
    if kind == "revision":
        _verify_revision(root, output, plan, connection)
        written_refs.append(receipt["revision"])
    _check_graph(graph, root, plan, guard=graph_guard, connection=connection); stages.assert_native_guard(guard, terminal=True)
    for ref in written_refs:
        _json(root, ref)
    return receipt


def _verify_stages(root, output, plan, connection):
    for stage_id in plan["requiredStages"]:
        node, _ = _verify_node(root, output, "stage", connection=connection, stage_id=stage_id)
        if node["deliveryPlanHash"] != plan["planHash"]:
            raise ValueError("stage belongs to another delivery")
    return list(plan["requiredStages"])


def _reviewer_sessions(root, output, plan, connection):
    _, revision = _verify_node(root, output, "revision", connection=connection)
    sessions = {binding["reviewerSessionId"] for binding in revision["bindings"]}
    for stage_id in plan["requiredStages"]:
        node, receipt = _verify_node(root, output, "stage", connection=connection, stage_id=stage_id)
        sessions.update(b["reviewerSessionId"] for b in receipt["bindings"])
        if node["typeExecution"]:
            sessions.add(node["typeExecution"]["binding"]["reviewerSessionId"])
            sessions.update(b["reviewerSessionId"] for b in node["typeExecution"]["aggregateBindings"])
    return sessions


def _run(root, argv):
    """No caller-supplied command, shell, success flag, or receipt import."""
    completed = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    result = {"argv": argv, "returnCode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    if completed.returncode != 0:
        raise ValueError("required delivery checker failed: " + Path(argv[1]).name)
    return result


@stages.operation
def check(root, output, *, connection):
    graph, guard = _capture_delivery(root, output)
    plan, _, _, effective, _ = _environment(root, output, connection)
    _verify_revision(root, output, plan, connection)
    _verify_stages(root, output, plan, connection)
    _verify_node(root, output, "evaluation", connection=connection)
    directory = Path(output) / "checks"
    # Generate from the genuinely saved base through the existing gas schemas;
    # authoring-only revision is not mislabelled as an old 10/15 patch.
    base = _json(root, plan["identity"]["canonical"])[1]
    merged = deepcopy(effective); merged["schemaVersion"] = gas.MERGED_SCHEMA_VERSION
    converted = {"list_group_id": effective["list_group_id"], "questions": deepcopy(effective["questions"]), "total_count": len(effective["questions"])}
    generated = directory / "generated/questions_json" / str(effective["list_group_id"])
    merged_ref = _write(generated / "30_merged_2" / (str(effective["list_group_id"]) + "_verified_publication_merged.json"), merged)
    converted_ref = _write(generated / "40_convert" / (str(effective["list_group_id"]) + "_firestore_verified_delivery.json"), converted)
    public_rows = [{"questionId": row["questionId"], **uploader.build_doc_data_base(row)} for row in effective["questions"]]
    dto_issues = checker._dto_issues(public_rows, Path(converted_ref["ref"])) + checker._alignment(public_rows)
    if dto_issues:
        raise ValueError("effective revision failed existing final DTO/alignment checks: " + "; ".join(dto_issues[:3]))
    # The old checker contract consumes the unchanged base's existing 30/40. Do
    # not replace those files with a revision or falsely label the base receipt.
    base_group = Path(plan["identity"]["canonical"]["ref"]).parent.parent
    expected_base_merged = {**deepcopy(base), "schemaVersion": gas.MERGED_SCHEMA_VERSION}
    base_merged = base_group / "30_merged_2" / (str(base["list_group_id"]) + "_verified_publication_merged.json")
    base_converted_paths = list((base_group / "40_convert").glob(str(base["list_group_id"]) + "_firestore_*.json"))
    if (not base_merged.is_file() or json.loads(base_merged.read_bytes()) != expected_base_merged
            or len(base_converted_paths) != 1
            or json.loads(base_converted_paths[0].read_bytes()) != {"list_group_id": base["list_group_id"], "questions": base["questions"], "total_count": len(base["questions"])}):
        raise ValueError("existing source-derived base 30/40 must be generated before revision checks")
    if [uploader.build_doc_data_base(r) for r in effective["questions"]] != [uploader.build_doc_data_base(r) for r in converted["questions"]]:
        raise ValueError("effective revision public projection differs from existing generator")
    save_plan = _json(root, plan["contextFiles"]["context.json"])[1]["save"]["plan"]["ref"]
    commands = []
    scripts = {"alignment": "check_choice_text_alignment.py", "required": "check_required_fields.py", "coverage": "check_correct_choice_patch_coverage.py"}
    for kind, name in scripts.items():
        command = [sys.executable, str(Path(root) / "scripts/check" / name), "--verified-save-plan", save_plan, "--repo-root", str(root)]
        run = _run(root, command)
        result = json.loads(run["stdout"])
        if (result.get("checkKind") != kind or result.get("checkedCount") != plan["allDocumentCount"]
                or result.get("issues") != [] or result.get("inputFilesVerified") is not True):
            raise ValueError("checker did not verify the complete nonempty saved year")
        commands.append(_write(directory / (kind + ".json"), run))
    # Existing quality gate runs against the saved year's actual generated files;
    # it cannot be replaced by only the three saved-source checks above.
    qualification = plan["identity"]["qualificationId"]; group = plan["identity"]["listGroupId"]
    quality_argv = [sys.executable, str(Path(root) / "tools/question_bank/question_bank.py"), "quality-gate", "--qualification", qualification,
                    "--list-group-id", group, "--base-dir", str(generated.parent), "--mode", "firestore"]
    commands.append(_write(directory / "quality-gate.json", _run(root, quality_argv)))
    dry_argv = [sys.executable, str(Path(root) / "scripts/upload/upload_questions_to_firestore.py"), str(converted_ref["ref"]), "--dry-run"]
    commands.append(_write(directory / "full-upload-dry-run.json", _run(root, dry_argv)))
    receipt = {"schemaVersion": "feedback-verified-delivery-checks/v1", "deliveryPlanHash": plan["planHash"],
               "merged": merged_ref, "converted": converted_ref, "commands": commands, "documentCount": plan["allDocumentCount"],
               "allDocumentIdsHash": sha256_json(plan["allDocumentIds"]), "publicProjectionHash": plan["effectivePublicProjectionHash"],
               "FirestoreWritten": False}
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _write(directory / "receipt.json", receipt); _verify_checks(root, output, plan, connection, require_command=False)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    return receipt


def _verify_checks(root, output, plan, connection, *, require_command=True):
    path = Path(output) / "checks/receipt.json"; receipt = json.loads(path.read_bytes())
    if (receipt.get("schemaVersion") != "feedback-verified-delivery-checks/v1" or receipt.get("deliveryPlanHash") != plan["planHash"]
            or receipt.get("documentCount") != plan["allDocumentCount"] or receipt.get("allDocumentIdsHash") != sha256_json(plan["allDocumentIds"])
            or receipt.get("publicProjectionHash") != plan["effectivePublicProjectionHash"] or len(receipt.get("commands", [])) != 5):
        raise ValueError("complete genuine checker/artifact receipt required")
    _, base = _json(root, plan["identity"]["canonical"])
    _, merged = _json(root, receipt["merged"]); _, converted = _json(root, receipt["converted"])
    _, candidate = _json(root, plan["candidate"]); _, evidence = _json(root, plan["evidence"])
    effective, _ = _revision(base, candidate, plan["identity"]["siblingIds"], evidence)
    expected = deepcopy(effective); expected["schemaVersion"] = gas.MERGED_SCHEMA_VERSION
    if merged != expected or converted != {"list_group_id": effective["list_group_id"], "questions": effective["questions"], "total_count": len(effective["questions"])}:
        raise ValueError("complete generated artifacts differ")
    save_plan = _json(root, plan["contextFiles"]["context.json"])[1]["save"]["plan"]["ref"]
    names = ("check_choice_text_alignment.py", "check_required_fields.py", "check_correct_choice_patch_coverage.py")
    expected_commands = [[sys.executable, str(Path(root) / "scripts/check" / name), "--verified-save-plan", save_plan, "--repo-root", str(root)] for name in names]
    expected_commands += [[sys.executable, str(Path(root) / "tools/question_bank/question_bank.py"), "quality-gate", "--qualification", plan["identity"]["qualificationId"],
                           "--list-group-id", plan["identity"]["listGroupId"], "--base-dir", str(Path(receipt["converted"]["ref"]).parents[2]), "--mode", "firestore"],
                          [sys.executable, str(Path(root) / "scripts/upload/upload_questions_to_firestore.py"), receipt["converted"]["ref"], "--dry-run"]]
    for index, ref in enumerate(receipt["commands"]):
        _, run = _json(root, ref)
        if run.get("returnCode") != 0 or run.get("argv") != expected_commands[index]:
            raise ValueError("required checker failed or exact command differs")
        if index < 3:
            result = json.loads(run["stdout"])
            if (result.get("checkKind") != ("alignment", "required", "coverage")[index] or result.get("checkedCount") != plan["allDocumentCount"]
                    or result.get("issues") != [] or result.get("inputFilesVerified") is not True or not result.get("inputFiles")
                    or result.get("orderedIdsSha256") != checker._object_hash(plan["allDocumentIds"])):
                raise ValueError("required checker result/count/identity differs")
            for file, digest in result["inputFiles"].items():
                if context_api._hash(context_api._physical(file).read_bytes()) != digest:
                    raise ValueError("checker executable or checked input changed")
        elif index == 3 and "[OK] question quality gate passed" not in run["stdout"]:
            raise ValueError("quality gate did not report success")
    if require_command:
        _verify_check_command(root, output, plan)
    return receipt


def _check_command_marker(output, plan, *, completed_receipt=None):
    receipt_ref = _ref(Path(output) / "checks/receipt.json") if completed_receipt is None else {
        "ref": str(Path(output) / "checks/receipt.json"), "sha256": context_api._hash(context_api._bytes(completed_receipt))}
    return "NATIVE_DELIVERY_CHECK_RECEIPT=" + json.dumps({"deliveryPlanHash": plan["planHash"], "receipt": receipt_ref, "checkedCommandCount": 5}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _command_output_state(text):
    # Runtime completion is the envelope's first line, never a substring in
    # script output. An error, interruption or terminated cell is not success.
    lines = text.splitlines()
    if not lines:
        raise ValueError("native checker runtime envelope missing")
    if lines[0] == "Script completed":
        return True, None
    match = re.fullmatch(r"Script running with cell ID ([A-Za-z0-9_-]+)", lines[0])
    if match:
        return False, match.group(1)
    raise ValueError("native checker runtime did not complete normally")


def _verify_check_origin(root, output, plan, record):
    if (not isinstance(record, dict) or set(record) != {"schemaVersion", "authorization", "invocation", "result", "continuations"}
            or record["schemaVersion"] != "native-verified-delivery-check-command/v1" or not isinstance(record["continuations"], list)):
        raise ValueError("genuine checker command receipt required")
    guard = stages.capture_native_guard([record])
    auth, author, _ = native._event(record["authorization"], production=True)
    payload = native._response(auth, "message")
    if (payload.get("role") != "user" or native._text(payload).strip() != native.AUTHORIZATION_TEXT
            or payload.get("internal_chat_message_metadata_passthrough", {}).get("content_item_kinds") != ["user.text"]
            or author.get("parent_thread_id") or isinstance(author.get("source"), dict) and author["source"].get("subagent")):
        raise ValueError("direct parent authorization for checker command required")
    command, command_meta, _ = native._event(record["invocation"], production=True)
    invocation = native._response(command, "custom_tool_call")
    argv = ["check", "--repo-root", str(root), "--execution", str(output)]
    expected_call = stages.native_operation_call(argv, module_name=MODULE)
    turn = invocation.get("internal_chat_message_metadata_passthrough", {}).get("turn_id")
    if (command_meta["id"] != author["id"] or record["invocation"]["sessionPath"] != record["authorization"]["sessionPath"]
            or record["authorization"]["eventLine"] >= record["invocation"]["eventLine"]
            or invocation.get("name") != "exec" or invocation.get("namespace") not in {None, "functions"}
            or invocation.get("input") != expected_call or not invocation.get("call_id") or not turn):
        raise ValueError("exact native checker CLI invocation required")
    initial, result_meta, _ = native._event(record["result"], production=True)
    result = native._response(initial, "custom_tool_call_output")
    if (result_meta["id"] != author["id"] or record["result"]["sessionPath"] != record["invocation"]["sessionPath"]
            or record["result"]["eventLine"] <= record["invocation"]["eventLine"]
            or result.get("call_id") != invocation["call_id"]
            or result.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") != turn):
        raise ValueError("native checker tool result binding differs")
    text = native._output_text(result.get("output", ""))
    outputs = [text]; previous = record["result"]["eventLine"]
    complete, cell = _command_output_state(text)
    for continuation in record["continuations"]:
        if not isinstance(continuation, dict) or set(continuation) != {"invocation", "result"} or not cell or complete:
            raise ValueError("exact active checker wait continuation required")
        call_event, call_meta, _ = native._event(continuation["invocation"], production=True)
        call = native._response(call_event, "function_call")
        args = json.loads(call.get("arguments", "{}"))
        response_event, response_meta, _ = native._event(continuation["result"], production=True)
        response = native._response(response_event, "function_call_output")
        if (call_meta["id"] != author["id"] or response_meta["id"] != author["id"]
                or continuation["invocation"]["sessionPath"] != record["invocation"]["sessionPath"]
                or continuation["result"]["sessionPath"] != record["invocation"]["sessionPath"]
                or not previous < continuation["invocation"]["eventLine"] < continuation["result"]["eventLine"]
                or call.get("name") not in {"wait", "functions.wait"} or call.get("namespace") not in {None, "functions"}
                or set(args) - {"cell_id", "yield_time_ms", "max_tokens", "terminate"} or args.get("cell_id") != cell or args.get("terminate") not in {None, False}
                or response.get("call_id") != call.get("call_id")
                or call.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") != turn
                or response.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") != turn):
            raise ValueError("native checker wait call/cell/result differs")
        text = native._output_text(response.get("output", "")); outputs.append(text)
        complete, more = _command_output_state(text)
        if more and more != cell:
            raise ValueError("native checker continuation cell changed")
        previous = continuation["result"]["eventLine"]
    markers = [line for text in outputs for line in text.splitlines() if line.startswith("NATIVE_DELIVERY_CHECK_RECEIPT=")]
    if not complete or markers != [_check_command_marker(output, plan)]:
        raise ValueError("genuine successful checker CLI receipt/hash output required")
    stages.assert_native_guard(guard, terminal=True)
    return {"authorSessionId": author["id"], "turnId": turn, "nativeEvidenceHash": sha256_json(record),
            "receipt": _ref(Path(output) / "checks/receipt.json")}


def _verify_check_command(root, output, plan):
    path = Path(output) / "checks/native-command.json"
    value = json.loads(path.read_bytes())
    if set(value) != {"schemaVersion", "evidence", "binding"} or value["schemaVersion"] != "feedback-verified-delivery-check-command-receipt/v1":
        raise ValueError("native checker execution evidence required")
    if value["binding"] != _verify_check_origin(root, output, plan, value["evidence"]):
        raise ValueError("native checker command receipt changed")
    return value


@stages.operation
def record_check_command(root, output, record, *, connection):
    plan, _, _, _, _ = _environment(root, output, connection)
    binding = _verify_check_origin(root, output, plan, record)
    _verify_checks(root, output, plan, connection, require_command=False)
    value = {"schemaVersion": "feedback-verified-delivery-check-command-receipt/v1", "evidence": deepcopy(record), "binding": binding}
    _write(Path(output) / "checks/native-command.json", value)
    _verify_check_command(root, output, plan)
    return value


def _git(root, *, revision=None):
    def run(*argv):
        return subprocess.check_output(["git", *argv], cwd=root, text=True).strip()
    head = run("rev-parse", "HEAD")
    if run("branch", "--show-current") != "main" or run("rev-parse", "origin/main") != head or run("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("verified delivery requires clean committed and pushed main")
    for path in _dependencies():
        source = Path(path)
        if source.is_relative_to(root):
            relative = str(source.relative_to(root))
            run("ls-files", "--error-unmatch", relative)
            if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root) != source.read_bytes():
                raise ValueError("executed delivery implementation is not the committed HEAD blob")
    if revision is not None:
        run("ls-files", "--error-unmatch", str(Path(revision).relative_to(root)))
        relative = str(Path(revision).relative_to(root))
        if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root) != Path(revision).read_bytes():
            raise ValueError("formal revision is not the committed HEAD blob")
    return {"branch": "main", "head": head, "originMain": head}


def _snapshot(plan):
    fields = sorted(set(uploader.DOC_COMPARE_KEYS) | {"createdAt", "createdById", "updatedAt", "updatedById"})
    return context_api.saver._snapshots(plan["identity"]["siblingIds"], fields)


def _payload(plan, effective, snapshots):
    rows, _ = _rows(effective); ids = plan["identity"]["siblingIds"]
    scopes = _json(Path(plan["repoRoot"]), plan["contextFiles"]["context.json"])[1]["fieldScopes"]
    old_plan = _json(Path(plan["repoRoot"]), _json(Path(plan["repoRoot"]), plan["contextFiles"]["context.json"])[1]["save"]["plan"])[1]
    masks = {}
    allowed_masks = getattr(uploader, "ATOMIC_MAINTENANCE_WRITE_FIELD_SETS", uploader.PRESERVED_SUGGESTION_MASKS)
    for identity in ids:
        baseline = old_plan["baseline"][identity]["fields"]
        projected = uploader.build_doc_data_base(rows[identity])
        changed = {f for f in set(baseline) | set(projected) if (f in baseline) != (f in projected) or baseline.get(f) != projected.get(f)}
        permitted = {"explanationText", "suggestedQuestions", "suggestedQuestionDetails", "lawRevisionFacts"}
        if not changed or not changed <= permitted:
            raise ValueError("public revision expanded outside permitted explanation/supplementary scope")
        matching = [list(mask) for mask in allowed_masks if set(mask) == changed]
        if len(matching) != 1:
            raise ValueError("existing atomic uploader has no exact revised field mask")
        masks[identity] = matching[0]
    documents = [deepcopy(rows[identity]) for identity in ids]
    payload = {"schemaVersion": uploader.ATOMIC_MAINTENANCE_SCHEMA, "originalQuestionId": plan["identity"]["originalQuestionId"],
               "questions": documents, "writeFieldsByDocument": masks,
               "expectedUpdateTimes": {identity: snapshots[identity]["updateTimeExact"] for identity in ids},
               "expectedLiveFingerprint": uploader.firestore_live_fingerprint(ids, {i: snapshots[i]["fields"] for i in ids})}
    uploader.resolve_atomic_maintenance_contract(payload, "verified delivery")
    # Every outside-mask field must agree with the saved baseline, including
    # absence. This stops an unrelated live edit from being blessed as baseline.
    old_plan = _json(Path(plan["repoRoot"]), _json(Path(plan["repoRoot"]), plan["contextFiles"]["context.json"])[1]["save"]["plan"])[1]
    for identity in ids:
        baseline = old_plan["baseline"][identity]["fields"]
        actual = snapshots[identity]["fields"]
        if any((f in actual) != (f in baseline) or actual.get(f) != baseline.get(f) for f in uploader.DOC_COMPARE_KEYS if f not in masks[identity]):
            raise ValueError("outside-mask production baseline changed; investigate and re-audit")
        if not any(f not in actual or actual[f] != uploader.build_doc_data_base(rows[identity])[f] for f in masks[identity]):
            raise ValueError("partially/already published scope requires reconciliation")
    return payload


def _completion_contract(connection, plan):
    from tools.question_bank.feedback_daily import IMPROVEMENT_WORK_STATES
    row = connection.execute("SELECT status FROM improvement_tasks WHERE task_id=?", (plan["taskId"],)).fetchone()
    if row is None or row["status"] not in IMPROVEMENT_WORK_STATES:
        raise ValueError("current unfinished improvement status required for completion CAS")
    return {"expectedStatus": row["status"], "reason": COMPLETION_REASON}


def _check_task_status(connection, output, plan, candidate, *, allow_completed=False):
    expected = candidate["completionContract"]
    row = connection.execute("SELECT status FROM improvement_tasks WHERE task_id=?", (plan["taskId"],)).fetchone()
    if row is not None and row["status"] == expected["expectedStatus"]:
        return
    if allow_completed and row is not None and row["status"] == "completed":
        latest = connection.execute("SELECT * FROM improvement_result_history WHERE task_id=? ORDER BY id DESC LIMIT 1", (plan["taskId"],)).fetchone()
        artifact = Path(output) / "completion.json"
        if (latest is not None and latest["status"] == "completed" and latest["reason"] == expected["reason"]
                and latest["artifact_ref"] == str(artifact) and artifact.is_file()
                and latest["artifact_sha256"] == context_api._hash(context_api._physical(artifact).read_bytes())):
            return
    raise ValueError("improvement status changed after independent publication approval")


def _prepare_publication(root, output, plan, effective, connection):
    revision = _revision_path(root, plan)
    git = _git(root, revision=revision)
    baseline = _snapshot(plan)
    payload = _payload(plan, effective, baseline)
    dry_run = uploader.upload_atomic_maintenance_questions(payload, "verified delivery", dry_run=True)
    directory = Path(output) / "publication"
    baseline_ref = _write(directory / "baseline.json", baseline)
    payload_ref = _write(directory / "payload.json", payload)
    dry_ref = _write(directory / "dry-run.json", dry_run)
    candidate = {"schemaVersion": "feedback-verified-delivery-publication-candidate/v1", "deliveryPlanHash": plan["planHash"],
                 "baseline": baseline_ref, "payload": payload_ref, "dryRun": dry_ref, "git": git,
                 "documentIds": plan["identity"]["siblingIds"], "taskRequirement": plan["taskRequirement"],
                 "revision": _ref(revision), "completionContract": _completion_contract(connection, plan)}
    return _write(directory / "candidate.json", candidate), candidate


def _publication_candidate(root, output, plan, candidate):
    expected_keys = {"schemaVersion", "deliveryPlanHash", "baseline", "payload", "dryRun", "git", "documentIds", "taskRequirement", "revision", "completionContract"}
    completion = candidate.get("completionContract", {})
    from tools.question_bank.feedback_daily import IMPROVEMENT_WORK_STATES
    if (set(candidate) != expected_keys or set(completion) != {"expectedStatus", "reason"}
            or completion.get("expectedStatus") not in IMPROVEMENT_WORK_STATES or completion.get("reason") != COMPLETION_REASON
            or candidate.get("schemaVersion") != "feedback-verified-delivery-publication-candidate/v1" or candidate.get("deliveryPlanHash") != plan["planHash"]
            or candidate.get("documentIds") != plan["identity"]["siblingIds"] or candidate.get("taskRequirement") != plan["taskRequirement"]
            or candidate.get("revision") != _ref(_revision_path(root, plan))):
        raise ValueError("fixed limited publication candidate differs")
    for key in ("baseline", "payload", "dryRun"):
        _json(root, candidate[key])
    payload = _json(root, candidate["payload"])[1]
    uploader.resolve_atomic_maintenance_contract(payload, "verified delivery")
    base = _json(root, plan["identity"]["canonical"])[1]
    authoring = _json(root, plan["candidate"])[1]
    evidence = _json(root, plan["evidence"])[1]
    effective, _ = _revision(base, authoring, plan["identity"]["siblingIds"], evidence)
    if payload != _payload(plan, effective, _json(root, candidate["baseline"])[1]):
        raise ValueError("atomic payload differs from complete formally audited revision")
    if _json(root, candidate["dryRun"])[1] != uploader.upload_atomic_maintenance_questions(payload, "verified delivery", dry_run=True):
        raise ValueError("atomic dry-run differs")
    return payload


def _dependency_graph(root, output, plan):
    """Pin every consumed artifact and native session, including legacy chains."""
    directory = Path(output)
    paths = {directory / "plan.json"}
    approvals = []
    for relative in ("reviews", "checks", "publication"):
        paths.update(path for path in (directory / relative).rglob("*.json") if path.is_file())
    for path in (directory / "reviews").rglob("approvals.json"):
        approvals.extend(json.loads(path.read_bytes())["approvals"])
    for path in (directory / "reviews").glob("*/plan.json"):
        node = json.loads(path.read_bytes())
        for ref in [node["candidate"], *node["parents"].values()]:
            bound, _ = _json(root, ref)
            paths.add(bound)
        if node.get("typeExecution"):
            execution = Path(node["typeExecution"]["directory"])
            type_plan = json.loads((execution / "stage-plan.json").read_bytes())
            phase_directories = [execution, *(Path(p["directory"]) for p in type_plan["priorExecutions"])]
            for phase in phase_directories:
                paths.update(phase / name for name in stages.FINAL_FILES)
                phase_plan = json.loads((phase / "stage-plan.json").read_bytes())
                for source, digest in phase_plan["inputs"].items():
                    physical = context_api._physical(source)
                    if context_api._hash(physical.read_bytes()) != digest:
                        raise ValueError("01 native phase dependency changed")
                    paths.add(physical)
                approvals.append(json.loads((phase / "native-approval.json").read_bytes()))
    context = _json(root, plan["contextFiles"]["context.json"])[1]
    old_plan_path, old_plan = _json(root, context["save"]["plan"])
    old_receipt_path, old_receipt = _json(root, context["save"]["receipt"])
    paths.update({old_plan_path, old_receipt_path})
    approvals.append(old_receipt["approvalEvidence"])
    if old_plan.get("lawReviewRequired"):
        for stage_id in context_api.saver.LAW_STAGES:
            path = old_plan_path.parent / ("law-" + stage_id + "-approval.json")
            paths.add(path)
            approvals.append(json.loads(context_api._physical(path).read_bytes()))
    graph = {str(path): context_api._hash(context_api._physical(path).read_bytes()) for path in sorted(paths)}
    return graph, stages.capture_native_guard(approvals)


def _graph_snapshot(output):
    # Kept as a small utility for isolated mutation tests; production uses the
    # complete dependency_graph above rather than only these local directories.
    directory = Path(output)
    paths = [directory / "plan.json"]
    for relative in ("reviews", "checks"):
        paths.extend(sorted((directory / relative).rglob("*.json")))
    return {str(path): context_api._hash(context_api._physical(path).read_bytes()) for path in paths if path.is_file()}


def _check_graph(snapshot, root, plan, *, guard=None, connection=None):
    _current(plan)
    for path, digest in snapshot.items():
        if context_api._hash(context_api._physical(path).read_bytes()) != digest:
            raise ValueError("delivery stage/check/evaluation graph changed during operation")
    if connection is not None and task_requirement_binding(connection, plan["taskId"]) != plan["taskRequirement"]:
        raise ValueError("task requirement changed during delivery I/O")
    if guard is not None:
        stages.assert_native_guard(guard, terminal=True)


def _capture_delivery(root, output):
    root = context_api._physical(root); output = _private(root, output)
    plan = json.loads((Path(output) / "plan.json").read_bytes())
    _verify_seal(plan)
    graph, guard = _dependency_graph(root, output, plan)
    return graph, guard


def _intent(root, output, plan, node, candidate):
    directory = Path(output) / "publication"
    expected = {"schemaVersion": "feedback-verified-delivery-publication-intent/v1", "deliveryPlanHash": plan["planHash"],
                "node": _ref(_node_dir(output, "publication") / "plan.json"), "approval": _ref(_node_dir(output, "publication") / "receipt.json"),
                "candidate": node["candidate"], "payload": candidate["payload"], "git": candidate["git"]}
    value = json.loads((directory / "intent.json").read_bytes())
    attempt = json.loads((directory / "attempt.json").read_bytes())
    if value != expected or attempt != {"schemaVersion": "feedback-verified-delivery-write-attempt/v1", "intent": _ref(directory / "intent.json")}:
        raise ValueError("durable publication intent/attempt binding differs")
    return value


def _publication_current(root, output, plan, effective, connection):
    _verify_revision(root, output, plan, connection); _verify_stages(root, output, plan, connection)
    eval_node, evaluation = _verify_node(root, output, "evaluation", connection=connection)
    _verify_checks(root, output, plan, connection)
    node, receipt = _verify_node(root, output, "publication", connection=connection)
    candidate = _json(root, node["candidate"])[1]
    payload = _publication_candidate(root, output, plan, candidate)
    return node, receipt, candidate, payload


@stages.operation
def publish(root, output, *, connection, failure_hook=None):
    graph, guard = _capture_delivery(root, output)
    plan, context, review, effective, evidence = _environment(root, output, connection)
    node, approval, candidate, payload = _publication_current(root, output, plan, effective, connection)
    _validate_node(root, output, node, plan, context, review, effective, evidence, connection)
    directory = Path(output) / "publication"
    if (directory / "intent.json").exists() and (directory / "attempt.json").exists():
        # Never blindly replay an unknown write. A durable intent without an
        # attempt is safely resumable under the same fresh CAS and approval.
        return reconcile(root, output, connection=connection)
    if _git(root, revision=_revision_path(root, plan)) != candidate["git"]:
        raise ValueError("publication git input differs")
    _check_task_status(connection, output, plan, candidate)
    baseline = _snapshot(plan)
    _check_task_status(connection, output, plan, candidate)
    if baseline != _json(root, candidate["baseline"])[1]:
        raise ValueError("fresh production fingerprint/updateTime drift; re-audit required")
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=False)
    intent = {"schemaVersion": "feedback-verified-delivery-publication-intent/v1", "deliveryPlanHash": plan["planHash"],
              "node": _ref(_node_dir(output, "publication") / "plan.json"), "approval": _ref(_node_dir(output, "publication") / "receipt.json"),
              "candidate": node["candidate"], "payload": candidate["payload"], "git": candidate["git"]}
    _write(directory / "intent.json", intent)
    if failure_hook:
        failure_hook("intent_saved")
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=False)
    _write(directory / "attempt.json", {"schemaVersion": "feedback-verified-delivery-write-attempt/v1", "intent": _ref(directory / "intent.json")})
    try:
        result = uploader.upload_atomic_maintenance_questions(payload, "audited verified delivery")
    except uploader.AtomicMaintenanceCommitUnconfirmed as error:
        _write(directory / "commit-unconfirmed.json", {"schemaVersion": "feedback-verified-delivery-commit-unconfirmed/v1", "intent": _ref(directory / "intent.json"), "transportResult": error.prepared_result})
        raise ValueError("publication unconfirmed; exact commit versions unavailable; reconcile before retry") from error
    except Exception:
        _write(directory / "write-failed.json", {"schemaVersion": "feedback-verified-delivery-write-failed/v1", "intent": _ref(directory / "intent.json"), "mayHaveCommitted": True})
        raise
    if result.get("status") != "committed" or result.get("publicationConfirmed") is not False:
        _write(directory / "write-observation.json", result)
        raise ValueError("transport did not return an exact committed write set")
    _write(directory / "commit.json", result)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=False)
    if failure_hook:
        failure_hook("commit_saved")
    return reconcile(root, output, connection=connection)


def _readback_match(plan, candidate, payload, commit, current):
    ids = plan["identity"]["siblingIds"]
    if (commit.get("schemaVersion") != uploader.ATOMIC_MAINTENANCE_RESULT_SCHEMA or commit.get("status") != "committed"
            or commit.get("documentIds") != ids or commit.get("changedDocumentIds") != ids or commit.get("unchangedDocumentIds") != []
            or commit.get("writeFieldsByDocument") != payload["writeFieldsByDocument"]
            or commit.get("expectedUpdateTimes") != payload["expectedUpdateTimes"] or commit.get("expectedLiveFingerprint") != payload["expectedLiveFingerprint"]
            or len(commit.get("writeResults", [])) != len(ids)):
        raise ValueError("exact atomic commit evidence required")
    versions = {entry["questionId"]: entry["updateTime"] for entry in commit["writeResults"]}
    if set(versions) != set(ids) or len(versions) != len(commit["writeResults"]):
        raise ValueError("commit version coverage differs")
    uploader.resolve_expected_update_times({"expectedUpdateTimes": versions}, payload["questions"], uploader.DETAILS_ONLY_WRITE_FIELDS)
    root = Path(plan["repoRoot"])
    baseline = _json(root, candidate["baseline"])[1]
    docs = {q["questionId"]: uploader.build_doc_data_base(q) for q in payload["questions"]}
    if set(current) != set(ids):
        raise ValueError("readback sibling membership differs")
    for identity in ids:
        before = baseline[identity]["fields"]; after = current[identity]["fields"]
        mask = payload["writeFieldsByDocument"][identity]
        if current[identity]["updateTimeExact"] != versions[identity]:
            raise ValueError("live document updateTime differs from exact commit")
        if any(field not in after or after[field] != docs[identity][field] for field in mask):
            raise ValueError("published field readback differs")
        if any((field in after) != (field in before) or after.get(field) != before.get(field) for field in set(before) | set(after) if field not in {*mask, "updatedAt", "updatedById"}):
            raise ValueError("outside-mask field presence/value changed")
        if "updatedAt" not in after or after.get("updatedById") != uploader.UPDATED_BY_ID:
            raise ValueError("write audit fields missing or writer differs")
    return versions


@stages.operation
def reconcile(root, output, *, connection):
    graph, guard = _capture_delivery(root, output)
    plan, _, _, effective, _ = _environment(root, output, connection)
    node, approval, candidate, payload = _publication_current(root, output, plan, effective, connection)
    directory = Path(output) / "publication"
    _intent(root, output, plan, node, candidate)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    current = _snapshot(plan)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    if not (directory / "commit.json").exists():
        _write(directory / ("unconfirmed-readback-" + sha256_json(current) + ".json"), current)
        raise ValueError("publication unconfirmed: reconcile observed live values, but no exact commit response; no blind retry")
    commit = json.loads((directory / "commit.json").read_bytes())
    versions = _readback_match(plan, candidate, payload, commit, current)
    current_ref = _write(directory / ("readback-" + sha256_json(current) + ".json"), current)
    result = {"schemaVersion": "feedback-verified-delivery-publication-result/v1", "deliveryPlanHash": plan["planHash"],
              "intent": _ref(directory / "intent.json"), "commit": _ref(directory / "commit.json"),
              "readback": current_ref, "documentIds": plan["identity"]["siblingIds"], "commitUpdateTimes": versions,
              "publicationConfirmed": True, "liveReadbackMatched": True, "outsideMaskUnchanged": True}
    # Readback readAt is not persisted by the existing snapshot reader. Identical
    # replay therefore preserves this exact success artifact.
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    _write(directory / "result.json", result)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    if json.loads((directory / "result.json").read_bytes()) != result:
        raise ValueError("publication result changed before return")
    return result


@stages.operation
def verify_completion(root, artifact, *, task_id, source_question_ids, task_requirement, live_reader=None, connection=None):
    if (not isinstance(artifact, dict) or set(artifact) != {"schemaVersion", "taskId", "execution", "deliveryPlan", "publicationResult"}
            or artifact["schemaVersion"] != PUBLICATION_SCHEMA or artifact["taskId"] != task_id):
        raise ValueError("dedicated verified delivery completion artifact required")
    if connection is None:
        with checker.readonly_connection() as db:
            return verify_completion(root, artifact, task_id=task_id, source_question_ids=source_question_ids,
                                     task_requirement=task_requirement, live_reader=live_reader, connection=db)
    root = context_api._physical(root); output = _private(root, artifact["execution"])
    graph, guard = _capture_delivery(root, output)
    plan, context, review, effective, evidence = _environment(root, output, connection)
    if (artifact["deliveryPlan"] != _ref(Path(output) / "plan.json") or task_requirement != plan["taskRequirement"] or plan["taskId"] != task_id
            or not set(source_question_ids) <= (set(plan["identity"]["siblingIds"]) | {plan["identity"]["originalQuestionId"]})):
        raise ValueError("completion task/source/requirement binding differs")
    node, receipt, candidate, payload = _publication_current(root, output, plan, effective, connection)
    _validate_node(root, output, node, plan, context, review, effective, evidence, connection)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    result_ref = _ref(Path(output) / "publication/result.json")
    if artifact["publicationResult"] != result_ref:
        raise ValueError("publication result binding differs")
    result = _json(root, result_ref)[1]
    _intent(root, output, plan, node, candidate)
    directory = Path(output) / "publication"
    expected_keys = {"schemaVersion", "deliveryPlanHash", "intent", "commit", "readback", "documentIds", "commitUpdateTimes", "publicationConfirmed", "liveReadbackMatched", "outsideMaskUnchanged"}
    if (set(result) != expected_keys or result["schemaVersion"] != "feedback-verified-delivery-publication-result/v1"
            or result["deliveryPlanHash"] != plan["planHash"] or result["documentIds"] != plan["identity"]["siblingIds"]
            or result["intent"] != _ref(directory / "intent.json") or result["commit"] != _ref(directory / "commit.json")):
        raise ValueError("publication result schema/plan/intent/document binding differs")
    readback_path, recorded = _json(root, result["readback"])
    if readback_path != directory / ("readback-" + sha256_json(recorded) + ".json"):
        raise ValueError("canonical readback artifact binding differs")
    _, commit = _json(root, result["commit"])
    versions = _readback_match(plan, candidate, payload, commit, recorded)
    # Completion must use the production snapshot/membership reader with exact
    # commit versions, not the generic value-only read_documents API.
    current = _snapshot(plan) if live_reader is None else live_reader(plan["identity"]["siblingIds"], fields=sorted(set(uploader.DOC_COMPARE_KEYS) | {"createdAt", "createdById", "updatedAt", "updatedById"}))
    _readback_match(plan, candidate, payload, commit, current)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    if (result.get("publicationConfirmed") is not True or result.get("liveReadbackMatched") is not True
            or result.get("outsideMaskUnchanged") is not True or result.get("commitUpdateTimes") != versions):
        raise ValueError("complete matching publication readback required")
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _check_task_status(connection, output, plan, candidate, allow_completed=True)
    def terminal(transaction_connection):
        _check_graph(graph, root, plan, guard=guard, connection=transaction_connection)
        _check_task_status(transaction_connection, output, plan, candidate, allow_completed=True)
    return _CompletionVerification({"runId": plan["planHash"], "documentIds": plan["identity"]["siblingIds"], "liveReadbackMatched": True}, terminal, candidate["completionContract"])


@stages.operation
def completion_artifact(root, output, *, connection):
    graph, guard = _capture_delivery(root, output)
    plan, _, _, _, _ = _environment(root, output, connection)
    artifact = {"schemaVersion": PUBLICATION_SCHEMA, "taskId": plan["taskId"], "execution": str(output),
                "deliveryPlan": _ref(Path(output) / "plan.json"), "publicationResult": _ref(Path(output) / "publication/result.json")}
    sources = {r["question_id"] for r in connection.execute("SELECT question_id FROM ai_question_candidates WHERE improvement_task_id=?", (plan["taskId"],))}
    verify_completion(root, artifact, task_id=plan["taskId"], source_question_ids=sources,
                      task_requirement=plan["taskRequirement"], connection=connection)
    if task_requirement_binding(connection, plan["taskId"]) != plan["taskRequirement"]:
        raise ValueError("task requirement changed before completion artifact")
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    _write(Path(output) / "completion.json", artifact)
    _check_graph(graph, root, plan, guard=guard, connection=connection)
    if task_requirement_binding(connection, plan["taskId"]) != plan["taskRequirement"]:
        raise ValueError("task requirement changed during completion artifact write")
    return artifact


@stages.operation
def record_completion(root, output, *, expected_status, reason):
    """Use the existing CAS/history transaction in the same native owner envelope."""
    root = context_api._physical(root); output = _private(root, output)
    from tools.question_bank import feedback_daily as daily
    path = Path(output) / "completion.json"
    artifact = json.loads(path.read_bytes())
    if artifact.get("schemaVersion") != PUBLICATION_SCHEMA:
        raise ValueError("dedicated completion artifact required")
    node = _read_node(root, output, "publication", None)
    candidate = _json(root, node["candidate"])[1]
    if candidate.get("completionContract") != {"expectedStatus": expected_status, "reason": reason}:
        raise ValueError("completion argv differs from independently approved CAS/reason scope")
    db_path = Path(daily.DEFAULT_DB).expanduser().resolve()
    connection = daily._private_db(db_path)
    try:
        result_id = daily.record_improvement_publication(connection, task_id=artifact["taskId"],
            expected_status=expected_status, reason=reason, artifact_ref=path, artifact_sha256=context_api._hash(path.read_bytes()), repo_root=Path(root))
        daily._backup_db(connection, db_path)
        row = connection.execute("SELECT status FROM improvement_tasks WHERE task_id=?", (artifact["taskId"],)).fetchone()
        history = connection.execute("SELECT * FROM improvement_result_history WHERE id=?", (result_id,)).fetchone()
        if row is None or row["status"] != "completed" or history is None or history["status"] != "completed":
            raise ValueError("completion ledger readback differs")
        result = {"schemaVersion": "feedback-verified-delivery-ledger-completion/v1", "taskId": artifact["taskId"],
                  "resultId": result_id, "status": "completed", "artifact": _ref(path), "history": dict(history)}
        _write(Path(output) / "completion-ledger-readback.json", result)
        return result
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "prepare", "scope", "record", "check", "record-check-command", "publish", "reconcile", "completion", "record-completion"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--stage-id")
    parser.add_argument("--type-execution", type=Path)
    parser.add_argument("--approval", type=Path, action="append", default=[])
    parser.add_argument("--command-evidence", type=Path)
    parser.add_argument("--expected-status")
    parser.add_argument("--reason")
    actual = list(sys.argv[1:] if argv is None else argv); args = parser.parse_args(actual)
    with checker.readonly_connection() as connection, stages.native_operation(actual, module_name=MODULE):
        if args.command == "bootstrap":
            if not args.context or not args.candidate or not args.evidence:
                parser.error("bootstrap requires --context --candidate --evidence")
            result = bootstrap(args.repo_root, args.context, args.execution, args.candidate, args.evidence, connection=connection)
        elif args.command == "prepare":
            result = prepare(args.repo_root, args.execution, args.kind, args.candidate, stage_id=args.stage_id,
                             type_execution=args.type_execution, connection=connection)
        elif args.command == "scope":
            result = scope(args.repo_root, args.execution, args.kind, stage_id=args.stage_id, connection=connection)
        elif args.command == "record":
            if not args.approval:
                parser.error("record requires native --approval")
            approvals = [json.loads(path.read_bytes()) for path in args.approval]
            result = record(args.repo_root, args.execution, args.kind, approvals, stage_id=args.stage_id, connection=connection)
        elif args.command == "record-completion":
            if not args.expected_status or not args.reason:
                parser.error("record-completion requires --expected-status --reason")
            result = record_completion(args.repo_root, args.execution, expected_status=args.expected_status, reason=args.reason)
        elif args.command == "record-check-command":
            if args.command_evidence is None:
                parser.error("record-check-command requires --command-evidence")
            result = record_check_command(args.repo_root, args.execution, json.loads(args.command_evidence.read_bytes()), connection=connection)
        else:
            callback = {"check": check, "publish": publish, "reconcile": reconcile, "completion": completion_artifact}[args.command]
            result = callback(args.repo_root, args.execution, connection=connection)
    if args.command == "scope":
        print("NATIVE_CANDIDATE_SCOPE=" + json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    elif args.command == "check":
        print(_check_command_marker(args.execution, {"planHash": result["deliveryPlanHash"]}, completed_receipt=result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
