"""Authentic read-only stage execution for verified25; never a fake work version.

Initially only text-only 01 is supported: two native aggregate reviews, then a
new native classification review. Each approval replays real delegation and
completed final events. Results are private execution evidence, not patch saves,
normal workVersions, evaluation passes, or publication authorization.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import json
from pathlib import Path
import shlex
import sqlite3
import sys

from scripts.common import aggregate_answer_decomposition as aggregate
from tools.question_bank import feedback_verified_review_context as context_api
from tools.question_review_console import independent_approval as native
from tools.question_review_console.projection import sha256_json

SCHEMA = "feedback-verified-stage-plan/v1"
INPUT_SCHEMA = "feedback-verified-stage-input/v1"
OUTPUT_SCHEMA = "feedback-verified-stage-output/v1"
RECEIPT_SCHEMA = "feedback-verified-stage-execution-receipt/v1"
SCOPE_SCHEMA = "feedback-verified-stage-native-scope/v1"
BASE_FILES = ("stage-plan.json", "stage-input.json", "stage-output.json")
FINAL_FILES = (*BASE_FILES, "native-approval.json", "execution-receipt.json")
PHASES = ("aggregate_a", "aggregate_b", "classification")
FIELDS = ("questionType", "isCalculationQuestion")
# Public DTO body images/local paths, legacy body storage URLs, and the
# public/legacy choice images read by the normal schema/reader/converter.
IMAGE_FIELDS = ("questionImageUrls", "questionImagePaths", "questionImageStorageUrls",
                "originalQuestionChoiceImageUrls", "correctChoiceImageUrls", "correctChoiceImagePaths")
_OPERATION_CONTEXT = ContextVar("verified_stage_operation_context", default=None)


def _operation(function):
    """A fresh private cache per public operation; nested prior replays share it."""
    @wraps(function)
    def run(*args, **kwargs):
        if _OPERATION_CONTEXT.get() is not None:
            return function(*args, **kwargs)
        token = _OPERATION_CONTEXT.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _OPERATION_CONTEXT.reset(token)
    return run


def _snapshot(path):
    return context_api._json_snapshot(path)


def _bound_json(root, ref):
    path, raw = context_api._bound(root, ref)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return path, value


def _check_files(output, *, allowed, required):
    output = context_api._physical(output)
    if not output.is_dir():
        raise ValueError("stage output directory required")
    names = {p.name for p in output.iterdir()}
    if not set(required) <= names or names - set(allowed):
        raise ValueError("stage output scope conflict or incomplete")


def _current(plan):
    context_api._check_inputs(plan["reviewContextSnapshot"])
    for path, digest in plan["inputs"].items():
        if context_api._hash(context_api._physical(path).read_bytes()) != digest:
            raise ValueError("fixed stage input or policy changed")


def _load_context(root, output, connection):
    output = context_api._private(root, output)
    cache = _OPERATION_CONTEXT.get()
    key = (str(root), str(output))
    if cache is not None and key in cache:
        context, review, refs = deepcopy(cache[key])
        context_api._check_output_set(output, complete=True)
        for ref in refs.values():
            context_api._bound(root, ref)
        context_api._check_inputs(context)
        context_api._check_output_set(output, complete=True)
        return context, review, refs
    verified = context_api.inspect(root, output, connection=connection)
    refs, values = {}, {}
    for name in context_api.FILES:
        refs[name], values[name] = _snapshot(output / name)
    context, review, receipt = (values[name] for name in context_api.FILES)
    if (receipt != verified or context["contextHash"] != receipt["contextHash"]
            or context["reviewInputHash"] != sha256_json(review)
            or context["contextHash"] != sha256_json({k: v for k, v in context.items() if k != "contextHash"})
            or any(receipt["artifacts"][name] != refs[name] for name in context_api.FILES[:2])):
        raise ValueError("verified review context changed during snapshot")
    if (context["identity"]["kind"] != "verified25_publication"
            or context["officialOrder"] is None
            or context["preflight"]["applicabilityUnresolvedStageIds"]):
        raise ValueError("resolved verified25 identity and official order required")
    context_api._check_inputs(context)
    if cache is not None:
        cache[key] = deepcopy((context, review, refs))
    return context, review, refs


def _validate_output(phase, candidate, stage_input):
    if not isinstance(candidate, dict):
        raise ValueError("dedicated stage output required")
    body = stage_input["questionBodyText"]
    if phase != "classification":
        value = aggregate.normalize_review(candidate, body, stage_input["candidateSet"])
        if (value != candidate or value["sourceHash"] != aggregate.source_text_hash(body)
                or value["classification"] != "non_target" or value["decision"] != "approve"):
            raise ValueError("aggregate target/hold requires unsupported decomposition adapter")
        return deepcopy(value)
    if set(candidate) != {"decisionsByDocument", "reasonsByDocument"}:
        raise ValueError("classification output requires decisions and independent reasons")
    decisions, reasons = candidate["decisionsByDocument"], candidate["reasonsByDocument"]
    ids = {s["questionId"] for s in stage_input["statements"]}
    if not isinstance(decisions, dict) or not isinstance(reasons, dict) or set(decisions) != ids or set(reasons) != ids:
        raise ValueError("all sibling IDs exactly once required")
    for identity in ids:
        fields, explanation = decisions[identity], reasons[identity]
        if (not isinstance(fields, dict) or set(fields) != set(FIELDS)
                or fields["questionType"] not in {"true_false", "flash_card", "group_choice"}
                or type(fields["isCalculationQuestion"]) is not bool
                or not isinstance(explanation, dict) or set(explanation) != set(FIELDS)
                or any(not isinstance(v, str) or not v.strip() for v in explanation.values())):
            raise ValueError("both independently reasoned stage fields required for each sibling")
    # These public documents are one official question, not five unrelated types.
    if len({sha256_json(v) for v in decisions.values()}) != 1:
        raise ValueError("one original question requires consistent stage output")
    return deepcopy(candidate)


def _derive(root, context_dir, phase, candidate, priors, connection, execution_profile=None):
    if phase not in PHASES:
        raise ValueError("only text-only question_type execution phases supported")
    context, review, refs = _load_context(root, context_dir, connection)
    if "question_type" not in context["preflight"]["requiredStageIds"]:
        raise ValueError("question_type is not an applicable current stage")
    policy = context["policies"]["maintenance"]["question_type"]
    targets = {f for target in policy.get("updateTargets", []) for f in target.get("fields", [])}
    independent_profile = policy.get("agentPolicy", {}).get("independent_review")
    if (targets != set(FIELDS) or not isinstance(independent_profile, dict)
            or set(independent_profile) != {"model", "reasoningEffort"}
            or any(not isinstance(v, str) or not v.strip() for v in independent_profile.values())
            or not policy.get("policyFingerprint") or not policy.get("policyVersion")):
        raise ValueError("current 01 field/model policy unsupported; hold")
    # The catalog's independent_review policy owns the two aggregate reviews.
    # Ordinary classification uses the caller's run configuration; its default
    # here is the same profile, not a new mandatory policy for that phase.
    model = deepcopy(execution_profile or independent_profile)
    if (not isinstance(model, dict) or set(model) != {"model", "reasoningEffort"}
            or any(not isinstance(v, str) or not v.strip() for v in model.values())
            or phase != "classification" and model != independent_profile):
        raise ValueError("aggregate reviewer must use policy; classification needs an explicit valid run profile")
    statements = []
    for binding in review["choiceBindings"]:
        row = review["documentsById"][binding["questionId"]]
        if any(row.get(field) for field in IMAGE_FIELDS):
            raise ValueError("image-bearing stage execution requires unsupported image receipt adapter")
        statements.append({"questionId": binding["questionId"], "officialLabel": binding["officialStatementLabel"],
                           "officialOrderIndex": binding["officialOrderIndex"],
                           "choiceText": row["originalQuestionChoiceText"]})
    body = review["projected"]["questionBodyText"]
    stage_input = {"schemaVersion": INPUT_SCHEMA, "identity": deepcopy(context["identity"]),
                   "stageId": "question_type", "questionBodyText": body, "statements": statements,
                   "candidateSet": aggregate.generate_statement_candidates(body, [s["choiceText"] for s in statements]),
                   "policy": deepcopy(policy), "instructions": [
                       "01正本と固定資格方針を読み、現在値・正答数から逆算せず回答操作を判断する。",
                       "全記述がchoiceTextListに既存ならnon_target。target/holdは未対応として停止する。",
                       "分類phaseはquestionTypeとisCalculationQuestionを内容から別々に判断し、全5IDを確認する。",
                       "fieldの内容正しさは独立レビューが判断し、機械検査は型と完全性だけを確認する。"]}
    output = _validate_output(phase, candidate, stage_input)
    prior_records, prior_inputs, seen = [], {}, set()
    if phase == "classification":
        if len(priors) != 2:
            raise ValueError("classification requires both prior native aggregate reviews")
        for prior, expected_phase in zip(priors, PHASES[:2], strict=True):
            receipt = inspect(root, prior, connection=connection)
            prior_plan = _snapshot(Path(prior) / BASE_FILES[0])[1]
            if (prior_plan["phase"] != expected_phase
                    or prior_plan["contextHash"] != context["contextHash"]
                    or receipt["binding"]["reviewerSessionId"] in seen):
                raise ValueError("two distinct current aggregate native sessions required")
            seen.add(receipt["binding"]["reviewerSessionId"])
            prior_records.append({"phase": expected_phase, "directory": str(prior), "receipt": deepcopy(receipt)})
            for name in FINAL_FILES:
                ref = context_api._ref(Path(prior) / name)
                prior_inputs[ref["ref"]] = ref["sha256"]
        first, second = (_snapshot(Path(p) / "stage-output.json")[1]["output"] for p in priors)
        consensus = aggregate.reconcile_reviews(body, [first, second], stage_input["candidateSet"])
        if consensus["classification"] != "non_target" or consensus["decision"] != "approve":
            raise ValueError("aggregate review disagreement/target/hold; no classification")
        stage_input["aggregateConsensus"] = consensus
    elif priors:
        raise ValueError("aggregate reviews must not receive other reviewer results")
    inputs = {**context["inputs"], **prior_inputs}
    inputs.update({ref["ref"]: ref["sha256"] for ref in refs.values()})
    for module in (sys.modules[__name__], aggregate, native):
        ref = context_api._ref(Path(module.__file__))
        inputs[ref["ref"]] = ref["sha256"]
    plan = {"schemaVersion": SCHEMA, "stageId": "question_type", "phase": phase,
            "repoRoot": str(root), "contextDirectory": str(context_dir), "contextHash": context["contextHash"],
            "identity": deepcopy(context["identity"]), "reviewContextSnapshot": context,
            "policy": deepcopy(policy), "requiredReviewerProfile": deepcopy(model),
            "reviewerProfileSource": "execution_configuration" if phase == "classification" else "current_independent_review_policy",
            "stageInputHash": sha256_json(stage_input), "stageOutputHash": sha256_json(output),
            "priorExecutions": prior_records, "inputs": inputs, "inputSetHash": sha256_json(inputs),
            "publicationReady": False, "checkpointRecorded": False, "evaluationPerformed": False,
            "supportedStageIds": ["question_type"],
            "unsupportedStageIds": [s for s in context["preflight"]["requiredStageIds"] if s != "question_type"]}
    plan["planHash"] = sha256_json(plan)
    _current(plan)
    return plan, stage_input, {"schemaVersion": OUTPUT_SCHEMA, "phase": phase, "output": output}


def _contents(plan, stage_input, stage_output):
    return dict(zip(BASE_FILES, (context_api._bytes(v) for v in (plan, stage_input, stage_output)), strict=True))


def _finish(output, contents, plan, *, allowed, required):
    _check_files(output, allowed=allowed, required=required)
    contents = dict(contents)
    # Scope/read_plan may start from a partially recorded execution. Pin its
    # present approval/receipt bytes too, rather than re-reading different proof
    # values during terminal native replay.
    for name in FINAL_FILES[len(BASE_FILES):]:
        path = output / name
        if name not in contents and path.exists():
            contents[name] = context_api._physical(path).read_bytes()
    for name, raw in contents.items():
        if context_api._physical(output / name).read_bytes() != raw:
            raise ValueError("immutable stage output changed")
    _current(plan)
    chain = _terminal_native_chain(output, contents, plan)
    guard = _capture_native_guard([item[0] for item in chain]) if chain else {}
    for approval, scope_value, owner_plan, expected_binding in chain:
        if _native_binding(approval, scope_value, owner_plan) != expected_binding:
            raise ValueError("native chain binding differs at completion")
    # A prior may be revoked while outputs are read or written. Validate the
    # complete chain, not only the classification review, before any return.
    for name, raw in contents.items():
        if context_api._physical(output / name).read_bytes() != raw:
            raise ValueError("immutable stage output changed during native replay")
    _current(plan)
    _assert_native_guard(guard)
    for prior in plan["priorExecutions"]:
        _check_files(context_api._physical(prior["directory"]), allowed=FINAL_FILES, required=FINAL_FILES)
    _check_files(output, allowed=allowed, required=required)


def _write(output, contents, plan, *, failure_hook=None, final=False):
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    allowed = FINAL_FILES if final else BASE_FILES
    _check_files(output, allowed=allowed, required=())
    for name, raw in contents.items():
        _current(plan)
        _check_files(output, allowed=allowed, required=())
        path = output / name
        if path.exists():
            if context_api._physical(path).read_bytes() != raw:
                raise ValueError("immutable stage output conflict")
        else:
            try:
                context_api._exclusive(path, raw)
            except FileExistsError:
                if context_api._physical(path).read_bytes() != raw:
                    raise ValueError("competing stage output conflict")
        if failure_hook:
            failure_hook(name)
    _finish(output, contents, plan, allowed=allowed, required=FINAL_FILES if final else BASE_FILES)


@_operation
def prepare(root, context_dir, output, *, connection, phase, candidate=None, priors=(),
            execution_profile=None, failure_hook=None):
    root = context_api._physical(root)
    context_dir = context_api._private(root, context_dir)
    output = context_api._private(root, output)
    priors = tuple(context_api._private(root, p) for p in priors)
    for prior in priors:
        _pin_execution_guards(root, prior)
    if phase != "classification" and candidate is None:
        _, review, _ = _load_context(root, context_dir, connection)
        candidate = {"schemaVersion": aggregate.REVIEW_SCHEMA_VERSION,
                     "sourceHash": aggregate.source_text_hash(review["projected"]["questionBodyText"]),
                     "classification": "non_target", "candidateId": None, "decision": "approve", "issueCodes": []}
    candidate = deepcopy(candidate)
    plan, stage_input, stage_output = _derive(root, context_dir, phase, candidate, priors, connection,
                                             deepcopy(execution_profile))
    contents = _contents(plan, stage_input, stage_output)
    if output.exists() and (output / "execution-receipt.json").exists():
        receipt = inspect(root, output, connection=connection)
        if receipt["planHash"] != plan["planHash"]:
            raise ValueError("existing stage execution input conflict")
        return receipt
    _write(output, contents, plan, failure_hook=failure_hook)
    return {"planHash": plan["planHash"], "phase": phase, "nativeApprovalRequired": True,
            "stageCompleted": False, "publicationReady": False}


def _read_plan(root, output, connection):
    _check_files(output, allowed=FINAL_FILES, required=BASE_FILES)
    refs, values = {}, {}
    for name in BASE_FILES:
        refs[name], values[name] = _snapshot(output / name)
    plan, stage_input, stage_output = (values[name] for name in BASE_FILES)
    if (plan.get("schemaVersion") != SCHEMA or plan.get("planHash") != sha256_json({k: v for k, v in plan.items() if k != "planHash"})
            or stage_output.get("schemaVersion") != OUTPUT_SCHEMA or stage_output.get("phase") != plan.get("phase")):
        raise ValueError("dedicated sealed stage plan required")
    _pin_execution_guards(root, output)
    for prior in plan["priorExecutions"]:
        _pin_execution_guards(root, prior["directory"])
    derived = _derive(root, context_api._private(root, plan["contextDirectory"]), plan["phase"],
                      stage_output["output"], tuple(Path(p["directory"]) for p in plan["priorExecutions"]), connection,
                      plan["requiredReviewerProfile"])
    if derived != (plan, stage_input, stage_output):
        raise ValueError("stage input/output/current policy differs")
    contents = _contents(*derived)
    _finish(output, contents, plan, allowed=FINAL_FILES, required=BASE_FILES)
    return plan, stage_input, stage_output, refs, contents


def _scope(root, output, plan, refs):
    command = " ".join(shlex.quote(s) for s in (sys.executable, "-m", "tools.question_bank.feedback_verified_stage_execution",
                     "scope", "--repo-root", str(root), "--execution", str(output)))
    call = '// @exec: {"yield_time_ms": 60000,"max_output_tokens":16000}\nlet r=await tools.exec_command(' + json.dumps({"cmd": command, "workdir": str(root), "max_output_tokens": 16000,
            "yield_time_ms": 30000}, ensure_ascii=False) + ");let out=r.output;while(r.session_id){r=await tools.write_stdin({session_id:r.session_id,chars:\"\",yield_time_ms:1000,max_output_tokens:16000});out+=r.output;}if(r.exit_code!==0)throw new Error(\"stage scope creation failed\");text(out);\n"
    return {"schemaVersion": SCOPE_SCHEMA, "action": "execute_current_01_readonly_phase",
            "stageId": plan["stageId"], "phase": plan["phase"], "planHash": plan["planHash"],
            "contextHash": plan["contextHash"], "identity": plan["identity"], "policy": plan["policy"],
            "stageInputHash": plan["stageInputHash"], "stageOutputHash": plan["stageOutputHash"],
            "artifacts": refs, "requiredReviewerProfile": plan["requiredReviewerProfile"],
            "reviewerProfileSource": plan["reviewerProfileSource"],
            "priorExecutions": [{"phase": p["phase"], "planHash": p["receipt"]["planHash"],
                                 "binding": p["receipt"]["binding"]} for p in plan["priorExecutions"]],
            "formalPatchSaved": False, "publicationApproved": False, "nativeCreationCall": call}


@_operation
def scope(root, output, *, connection):
    root = context_api._physical(root); output = context_api._private(root, output)
    plan, _, _, refs, _ = _read_plan(root, output, connection)
    return _scope(root, output, plan, refs)


def _native_journal_bytes(path, *, bootstrap=False):
    """Never expand a bootstrap baseline by retrying into newly appended input."""
    context_api._physical(path)
    observed_length = 0
    for _ in range(3):
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("native journal replaced during verification")
        if len(raw) < max(observed_length, before.st_size) or after.st_size < max(before.st_size, len(raw)):
            raise ValueError("native journal truncated during verification")
        # Before the first prefix is hashed there is no evidence that a changed
        # file was only appended to (rather than rewritten and appended to).
        # Hold this short bootstrap window even for usage-only activity. Once
        # pinned, normal appends are proved against immutable prefix bytes.
        if bootstrap:
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(before) != signature(after) or len(raw) != before.st_size:
                raise ValueError("native bootstrap changed during verification; fresh operation required")
            return before, after, raw
        observed_length = max(observed_length, after.st_size)
        if len(raw) == after.st_size:
            return before, after, raw
    raise ValueError("native session changed during verification: append did not settle")


def _capture_native_guard(approvals):
    """Pin consumed journal prefixes for this operation, never journal length.

    Native journals are append-only runtime streams. Their observed prefix and
    session identity are authority; a file's later size/mtime is not. Keep the
    earliest prefix in the private operation context so a new instruction or
    reviewer event between two replays cannot become an unnoticed new baseline.
    Nothing from this guard is persisted in plans or receipts.
    """
    paths = set()
    for approval in approvals:
        if not isinstance(approval, dict):
            raise ValueError("native approval object required")
        for ref in approval.values():
            if isinstance(ref, dict) and "sessionPath" in ref:
                path = context_api._physical(ref["sessionPath"])
                if not path.is_relative_to(native.NATIVE_SESSION_ROOT):
                    raise ValueError("genuine native session path required")
                paths.add(path)
        if not paths:
            raise ValueError("native session evidence required")
    result = {}
    for path in sorted(paths):
        cache = _OPERATION_CONTEXT.get()
        key = ("native-journal-prefix", str(path))
        if cache is not None and key in cache:
            result[path] = cache[key]
            _assert_native_guard({path: result[path]})
            continue
        before, after, raw = _native_journal_bytes(path, bootstrap=True)
        prefix = raw[:before.st_size]
        if not prefix or not prefix.endswith(b"\n"):
            raise ValueError("complete LF-terminated native journal required")
        try:
            meta = json.loads(prefix[:prefix.index(b"\n")])
            # Referenced events are replayed by the genuine native verifier.
            # Check the current tail too so an incomplete JSON append cannot be
            # hidden merely because it happened before this guard was captured.
            tail = json.loads(prefix[prefix.rfind(b"\n", 0, len(prefix) - 1) + 1:-1])
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("complete native journal JSON required") from exc
        if (not isinstance(meta, dict) or meta.get("type") != "session_meta"
                or not isinstance(meta.get("payload"), dict) or not meta["payload"].get("id")
                or not isinstance(tail, dict)):
            raise ValueError("native journal session identity required")
        result[path] = {"device": before.st_dev, "inode": before.st_ino, "length": before.st_size,
                        "observedLength": max(len(raw), after.st_size),
                        "prefixSha256": context_api._hash(prefix), "sessionId": meta["payload"]["id"]}
        _validate_native_suffix(raw[before.st_size:], result[path]["sessionId"])
        _assert_native_guard({path: result[path]})
        if cache is not None:
            cache[key] = result[path]
    return result


def _pin_execution_guards(root, output):
    """Start the operation's authority window before expensive context replay."""
    output = context_api._private(root, output)
    approval_path = output / "native-approval.json"
    if approval_path.exists():
        _capture_native_guard([_snapshot(approval_path)[1]])


def _observational_runtime_event(event, session_id):
    """Recognize the runtime's usage-only envelope, not conversational input.

    Only same-session identities plus integer counters can be ignored by an
    authority replay. Messages, tool requests/results, delegation, turn context,
    completions, and world state can change instructions, scope, input, model or
    approval and must force a fresh operation. Unknown metadata schemas are held
    too; adding a type name to a loose allowlist would not establish authority.
    """
    if (not isinstance(event, dict) or event.get("type") != "token_usage_record"
            or set(event) - {"type", "timestamp", "ordinal", "payload"}):
        return False
    if "ordinal" in event and (type(event["ordinal"]) is not int or event["ordinal"] < 0):
        return False
    if "timestamp" in event:
        try:
            datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return False
    payload = event.get("payload")
    identities = {"thread_id", "turn_id", "session_id", "root_turn_id", "response_id"}
    usages = {"usage", "turn_token_usage", "thread_token_usage"}
    counters = {"input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens", "total_tokens"}
    if (not isinstance(payload, dict) or set(payload) != identities | usages
            or any(not isinstance(payload[k], str) or not payload[k] for k in identities)
            or payload["thread_id"] != session_id):
        return False
    return all(isinstance(payload[k], dict) and set(payload[k]) == counters
               and all(type(v) is int and v >= 0 for v in payload[k].values()) for k in usages)


def _validate_native_suffix(suffix, session_id):
    if suffix and not suffix.endswith(b"\n"):
        raise ValueError("complete LF-terminated native append required")
    for line in suffix.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("complete native append JSON required") from exc
        if not _observational_runtime_event(event, session_id):
            raise ValueError("native session changed during verification: new decision input")


def _assert_native_guard(guard):
    for path, snapshot in guard.items():
        before, after, raw = _native_journal_bytes(path)
        identity = (snapshot["device"], snapshot["inode"])
        if ((after.st_dev, after.st_ino) != identity
                or min(before.st_size, len(raw)) < snapshot["observedLength"]
                or context_api._hash(raw[:snapshot["length"]]) != snapshot["prefixSha256"]):
            raise ValueError("native session changed during verification")
        _validate_native_suffix(raw[snapshot["length"]:], snapshot["sessionId"])
        snapshot["observedLength"] = max(snapshot["observedLength"], after.st_size)


def _native_binding(approval, scope_value, plan):
    guard = _capture_native_guard([approval])
    cache = _OPERATION_CONTEXT.get()
    key = ("native-binding", sha256_json(approval), sha256_json(scope_value),
           plan["planHash"], sha256_json(plan["requiredReviewerProfile"]))
    if cache is not None and key in cache:
        # The first replay proved native origin, exact scope, completed turn,
        # profile and latest verdict. An unchanged consumed prefix plus usage-
        # only suffix cannot introduce a new verdict or instruction. Re-prove
        # that invariant at every reuse; semantic append requires a fresh run.
        _assert_native_guard(guard)
        return deepcopy(cache[key])
    binding = native.verify_independent_approval(approval, scope_value, production=True)
    _, _, lines = native._event(approval["reviewerFinal"], production=True)
    profiles = []
    latest = None
    for number, raw in enumerate(lines, 1):
        event = json.loads(raw); payload = event.get("payload", {})
        if (event.get("type") == "turn_context" and payload.get("turn_id") == binding["reviewerTurnId"]):
            profiles.append({"model": payload.get("model"), "reasoningEffort": payload.get("effort")})
        if (event.get("type") == "response_item" and payload.get("type") == "message"
                and payload.get("role") == "assistant" and payload.get("phase") == "final_answer"):
            try:
                verdict = json.loads(native._text(payload))
            except (ValueError, TypeError):
                continue
            if isinstance(verdict, dict) and isinstance(verdict.get("scope"), dict) and verdict["scope"].get("planHash") == plan["planHash"]:
                latest = number
    if not profiles or any(profile != plan["requiredReviewerProfile"] for profile in profiles):
        raise ValueError("native reviewer actual model/effort differs from current stage policy")
    if latest != approval["reviewerFinal"]["eventLine"]:
        raise ValueError("native phase approval replaced or revoked")
    previous = {p["receipt"]["binding"]["reviewerSessionId"] for p in plan["priorExecutions"]}
    if binding["reviewerSessionId"] in previous:
        raise ValueError("classification requires a new independent native reviewer session")
    _assert_native_guard(guard)
    if cache is not None:
        cache[key] = deepcopy(binding)
    return binding


def _terminal_native_chain(output, contents, plan):
    """Replay pinned priors without another expensive load_saved recursion.

    The normal derivation already verified each prior against this same genuine
    context. Its full artifacts are in plan.inputs, so terminal replay must use
    those identical raw bytes and reproduce the exact scope/binding again.
    """
    root = Path(plan["repoRoot"])
    chain = []
    for prior in plan["priorExecutions"]:
        directory = context_api._private(root, prior["directory"])
        _check_files(directory, allowed=FINAL_FILES, required=FINAL_FILES)
        values, refs = {}, {}
        for name in FINAL_FILES:
            path = directory / name
            ref = {"ref": str(path), "sha256": plan["inputs"][str(path)]}
            _, raw = context_api._bound(root, ref)
            values[name] = json.loads(raw)
            if name in BASE_FILES:
                refs[name] = ref
        prior_plan = values["stage-plan.json"]
        if (prior_plan["phase"] != prior["phase"] or prior_plan["contextHash"] != plan["contextHash"]
                or prior_plan["priorExecutions"] or values["execution-receipt.json"] != prior["receipt"]):
            raise ValueError("pinned prior native chain differs")
        chain.append((values["native-approval.json"], _scope(root, directory, prior_plan, refs),
                      prior_plan, prior["receipt"]["binding"]))
    approval_path = output / "native-approval.json"
    if approval_path.exists():
        approval_raw = contents.get("native-approval.json")
        if approval_raw is None:
            approval_raw = context_api._physical(approval_path).read_bytes()
        approval = json.loads(approval_raw)
        refs = {name: {"ref": str(output / name), "sha256": context_api._hash(contents[name])} for name in BASE_FILES}
        scope_value = _scope(root, output, plan, refs)
        binding = _native_binding(approval, scope_value, plan)
        # A partial record has an approval but no receipt yet; replay is still
        # required and no success receipt is invented for that partial state.
        receipt_path = output / "execution-receipt.json"
        if receipt_path.exists():
            raw = contents.get("execution-receipt.json")
            if raw is None:
                raw = context_api._physical(receipt_path).read_bytes()
            if json.loads(raw)["binding"] != binding:
                raise ValueError("own native execution binding differs")
        chain.append((approval, scope_value, plan, binding))
    return chain


def _receipt(root, output, plan, stage_output, approval, refs):
    binding = _native_binding(approval, _scope(root, output, plan, refs), plan)
    completed = False
    differences = []
    if plan["phase"] == "classification":
        canonical_ref = plan["identity"]["canonical"]
        _, canonical = _bound_json(root, canonical_ref)
        rows = {row["questionId"]: row for row in canonical["questions"]}
        for identity, values in stage_output["output"]["decisionsByDocument"].items():
            for field, value in values.items():
                if field not in rows[identity] or rows[identity][field] != value:
                    differences.append({"questionId": identity, "field": field,
                                        "beforePresent": field in rows[identity], "before": rows[identity].get(field), "after": value})
        completed = not differences
    return {"schemaVersion": RECEIPT_SCHEMA, "planHash": plan["planHash"], "contextHash": plan["contextHash"],
            "identity": plan["identity"], "stageId": "question_type", "phase": plan["phase"],
            "stageInputHash": plan["stageInputHash"], "stageOutputHash": plan["stageOutputHash"],
            "policyVersion": plan["policy"]["policyVersion"], "policyFingerprint": plan["policy"]["policyFingerprint"],
            "binding": binding, "contentAuditValidated": True, "stageCompleted": completed,
            "remainingFieldDiffs": differences,
            "holdReasons": (["stage_output_requires_formal_save"] if differences else []) +
                           (["classification_not_executed"] if plan["phase"] != "classification" else []),
            "unsupportedStageIds": plan["unsupportedStageIds"], "checkpointRecorded": False,
            "evaluationPerformed": False, "publicationReady": False, "formalPatchSaved": False, "FirestoreWritten": False}


@_operation
def record(root, output, approval, *, connection, failure_hook=None):
    root = context_api._physical(root); output = context_api._private(root, output)
    approval = deepcopy(approval)
    _capture_native_guard([approval])
    plan, _, stage_output, refs, contents = _read_plan(root, output, connection)
    receipt = _receipt(root, output, plan, stage_output, approval, refs)
    contents.update({"native-approval.json": context_api._bytes(approval), "execution-receipt.json": context_api._bytes(receipt)})
    _write(output, contents, plan, failure_hook=failure_hook, final=True)
    # Replaying all native evidence after writes is mandatory before success.
    repeated = _receipt(root, output, plan, stage_output, approval, refs)
    if repeated != receipt:
        raise ValueError("native evidence changed during stage recording")
    _finish(output, contents, plan, allowed=FINAL_FILES, required=FINAL_FILES)
    return receipt


@_operation
def inspect(root, output, *, connection):
    root = context_api._physical(root); output = context_api._private(root, output)
    _check_files(output, allowed=FINAL_FILES, required=FINAL_FILES)
    plan, _, stage_output, refs, contents = _read_plan(root, output, connection)
    _, approval = _snapshot(output / "native-approval.json")
    receipt = _receipt(root, output, plan, stage_output, approval, refs)
    contents.update({"native-approval.json": context_api._bytes(approval), "execution-receipt.json": context_api._bytes(receipt)})
    _finish(output, contents, plan, allowed=FINAL_FILES, required=FINAL_FILES)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "scope", "record", "inspect"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--prior", type=Path, action="append", default=[])
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    args = parser.parse_args(argv)
    if args.command == "prepare" and (args.context is None or args.phase is None):
        parser.error("prepare requires --context and --phase")
    if args.command == "record" and args.approval is None:
        parser.error("record requires --approval")
    if bool(args.model) != bool(args.reasoning_effort):
        parser.error("execution model and reasoning effort must be selected together")
    connection = sqlite3.connect(context_api.saver.LEDGER.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if args.command == "prepare":
            candidate = _snapshot(args.candidate)[1] if args.candidate else None
            value = prepare(args.repo_root, args.context, args.execution, connection=connection, phase=args.phase,
                            candidate=candidate, priors=args.prior,
                            execution_profile={"model": args.model, "reasoningEffort": args.reasoning_effort} if args.model else None)
        elif args.command == "scope":
            print(native.scope_marker(scope(args.repo_root, args.execution, connection=connection), creation=True))
            return
        elif args.command == "record":
            value = record(args.repo_root, args.execution, _snapshot(args.approval)[1], connection=connection)
        else:
            value = inspect(args.repo_root, args.execution, connection=connection)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
