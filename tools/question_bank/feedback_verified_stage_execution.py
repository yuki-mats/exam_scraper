"""Authentic read-only stage execution for verified25; never a fake work version.

Initially only text-only 01 is supported: two native aggregate reviews, then a
new native classification review. Each approval replays real delegation and
completed final events. Results are private execution evidence, not patch saves,
normal workVersions, evaluation passes, or publication authorization.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import json
import re
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
_CLI_OPERATION = ContextVar("verified_stage_cli_operation", default=None)


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


# Shared by explicitly allowlisted entry points; library calls gain no CLI owner.
operation = _operation

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
    _assert_native_guard(guard, terminal=True)
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
    call = native_operation_call(["scope", "--repo-root", str(root), "--execution", str(output)])
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


def native_operation_call(argv, *, module_name="tools.question_bank.feedback_verified_stage_execution"):
    """Canonical native owner envelope: one exact CLI, no shell redirection/code.

    This is an origin binding, not an approval. main binds its real argv; a
    caller must execute this exact text to receive owner-only liveness support.
    Library callers and reviewer journals retain the usage-only boundary.
    """
    if module_name not in {"tools.question_bank.feedback_verified_stage_execution",
                           "tools.question_bank.feedback_verified_delivery",
                           "tools.question_bank.feedback_verified_save_record",
                           "tools.question_bank.feedback_source_tf_delivery",
                           "tools.question_bank.feedback_source_tf_report_review"}:
        raise ValueError("owner-operation module is outside the explicit allowlist")
    if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError("exact nonempty CLI argv required")
    if module_name == "tools.question_bank.feedback_source_tf_report_review":
        options = {}
        position = 0
        while position < len(argv) and argv[position] in {"--repo-root", "--db"}:
            key = argv[position]
            if key in options or position + 1 >= len(argv) or argv[position + 1].startswith("--"):
                raise ValueError("exact private report review global options required")
            options[key] = argv[position + 1]
            position += 2
        if set(options) != {"--repo-root", "--db"} or position >= len(argv) or argv[position] != "execute":
            raise ValueError("private report review owner supports only explicit execute")
        tail = argv[position + 1:]
        selected = {}
        if len(tail) != 4:
            raise ValueError("exact private report review request/output required")
        for key, value in zip(tail[::2], tail[1::2]):
            if key not in {"--request", "--output"} or key in selected or value.startswith("--"):
                raise ValueError("unsupported private report review option")
            selected[key] = value
        if set(selected) != {"--request", "--output"}:
            raise ValueError("exact private report review scope required")
    if module_name == "tools.question_bank.feedback_source_tf_delivery":
        options = {}
        position = 0
        while position < len(argv) and argv[position] in {"--repo-root", "--db"}:
            key = argv[position]
            if key in options or position + 1 >= len(argv) or argv[position + 1].startswith("--"):
                raise ValueError("exact source TF owner global options required")
            options[key] = argv[position + 1]
            position += 2
        if "--repo-root" not in options or position >= len(argv):
            raise ValueError("source TF owner requires explicit repo and command")
        commands = {
            "scope": {"--plan", "--action"},
            "inspect": {"--plan"}, "check": {"--plan"}, "evaluate": {"--plan"},
            "reconcile": {"--plan"}, "completion": {"--plan"},
            "record-completion": {"--plan", "--completion", "--completion-sha256"},
            "save": {"--plan", "--approval"}, "publish": {"--plan", "--approval"},
        }
        command_name = argv[position]
        tail = argv[position + 1:]
        if command_name not in commands or len(tail) % 2:
            raise ValueError("unsupported source TF owner command")
        selected = {}
        for key, value in zip(tail[::2], tail[1::2]):
            if key not in commands[command_name] or key in selected or value.startswith("--"):
                raise ValueError("unsupported source TF owner option")
            selected[key] = value
        if command_name in {'completion', 'record-completion'} and set(options) != {'--repo-root', '--db'}:
            raise ValueError('source TF completion owner requires explicit authority database')
        if command_name == 'record-completion' and not re.fullmatch(r'[0-9a-f]{64}', selected.get('--completion-sha256', '')):
            raise ValueError('source TF completion owner requires exact artifact SHA256')
        if set(selected) != commands[command_name] or selected.get("--action", "save") not in {"save", "publish"}:
            raise ValueError("exact source TF owner command scope required")
    if module_name == "tools.question_bank.feedback_verified_save_record":
        # This module has two subcommands with global options before them.
        # Do not turn the module addition into an arbitrary CLI whitelist.
        options = {}
        position = 0
        while position < len(argv) and argv[position] in {"--repo-root", "--db"}:
            key = argv[position]
            if key in options or position + 1 >= len(argv):
                raise ValueError("exact formal-save record CLI argv required")
            options[key] = argv[position + 1]
            if options[key].startswith("--"):
                raise ValueError("exact formal-save record CLI option value required")
            position += 2
        if "--repo-root" not in options or position >= len(argv) or argv[position] not in {"record", "inspect"}:
            raise ValueError("formal-save owner supports only record/inspect")
        command_name = argv[position]
        tail = argv[position + 1:]
        permitted = {"--plan"} if command_name == "record" else {"--task-id", "--task-kind"}
        required = {"--plan"} if command_name == "record" else {"--task-id"}
        selected = {}
        if len(tail) % 2:
            raise ValueError("exact formal-save record CLI argv required")
        for key, value in zip(tail[::2], tail[1::2]):
            if key not in permitted or key in selected or value.startswith("--"):
                raise ValueError("unsupported formal-save owner CLI option")
            selected[key] = value
        if not required <= set(selected) or selected.get("--task-kind", "improvement") not in {"improvement", "maintenance"}:
            raise ValueError("exact formal-save record/inspect scope required")
    command = " ".join(shlex.quote(str(s)) for s in (sys.executable, "-m", module_name, *argv))
    error = "stage scope creation failed" if argv[0] == "scope" else "stage operation failed"
    # Preserve the new CLI's structured legacy hold observation on failure;
    # it must not disappear behind a generic wrapper exception.
    failure = ('{text(out);throw new Error("formal-save record operation failed");}'
               if module_name == "tools.question_bank.feedback_verified_save_record"
               else 'throw new Error(' + json.dumps(error) + ')')
    return ('// @exec: {"yield_time_ms": 60000,"max_output_tokens":16000}\n'
            'let r=await tools.exec_command(' + json.dumps({"cmd": command, "workdir": str(Path(_cli_root(argv))),
                "max_output_tokens": 16000, "yield_time_ms": 30000}, ensure_ascii=False)
            + ');let out=r.output;while(r.session_id){r=await tools.write_stdin({session_id:r.session_id,chars:"",yield_time_ms:1000,max_output_tokens:16000});out+=r.output;}if(r.exit_code!==0)'
            + failure + ';text(out);\n')


@contextmanager
def native_operation(argv, *, module_name="tools.question_bank.feedback_verified_stage_execution"):
    """Bind a CLI's actual argv and a fresh non-persisted authority window.

    Only explicit production entry points may opt into this protocol.
    Their caller must supply actual parsed invocation arguments, not a guessed
    earlier command. All native origin and latest-verdict checks remain real.
    """
    if _OPERATION_CONTEXT.get() is not None or _CLI_OPERATION.get() is not None:
        raise ValueError("native owner operation must have one outermost scope")
    request_token = _CLI_OPERATION.set(native_operation_call(list(argv), module_name=module_name))
    cache_token = _OPERATION_CONTEXT.set({})
    try:
        yield
    finally:
        _OPERATION_CONTEXT.reset(cache_token)
        _CLI_OPERATION.reset(request_token)


def capture_native_guard(approvals):
    return _capture_native_guard(approvals)


def assert_native_guard(guard, *, terminal=False):
    return _assert_native_guard(guard, terminal=terminal)


def _cli_root(argv):
    try:
        return argv[argv.index("--repo-root") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("native operation requires an explicit repository root") from exc


def _signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


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
            if _signature(before) != _signature(after) or len(raw) != before.st_size:
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
    paths, author_paths, reviewer_paths = set(), set(), set()
    for approval in approvals:
        if not isinstance(approval, dict):
            raise ValueError("native approval object required")
        for key, ref in approval.items():
            if isinstance(ref, dict) and "sessionPath" in ref:
                path = context_api._physical(ref["sessionPath"])
                if not path.is_relative_to(native.NATIVE_SESSION_ROOT):
                    raise ValueError("genuine native session path required")
                paths.add(path)
                if key == "authorization": author_paths.add(path)
                if key.startswith("reviewer"): reviewer_paths.add(path)
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
        request = _CLI_OPERATION.get()
        result[path]["owner"] = (_operation_owner(prefix, result[path]["sessionId"], request)
                                  if request and path in author_paths and path not in reviewer_paths else None)
        result[path]["signature"] = _signature(after)
        _validate_native_suffix(raw[before.st_size:], result[path]["sessionId"], result[path]["owner"])
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


def _event_payload(event, owner):
    if (not isinstance(event, dict) or set(event) - {"type", "timestamp", "ordinal", "payload", "metadata"}
            or not isinstance(event.get("payload"), dict)):
        return None
    if "ordinal" in event and (type(event["ordinal"]) is not int or event["ordinal"] < 0):
        return None
    if "timestamp" in event:
        try: datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError): return None
    if "metadata" in event and not _transport_metadata(event, owner): return None
    return event["payload"]


def _transport_metadata(event, owner):
    """Accept only observed owner-output transport schemas, never user input.

    user_input_order is the native message ordering counter, despite its name.
    It must not go backwards from the launch prefix. Retained-source identity
    belongs to this assistant message, this turn and its completed revision.
    """
    meta, payload = event.get("metadata"), event.get("payload", {})
    if (event.get("type") != "response_item" or not isinstance(meta, dict)
            or meta.get("client_authored") is not False): return False
    tag = payload.get("type")
    if tag in {"custom_tool_call_output", "function_call_output"}:
        return (set(meta) == {"client_authored", "fallback_token_limit_override"}
                and type(meta["fallback_token_limit_override"]) is int
                and meta["fallback_token_limit_override"] == 12000)
    expected = {"client_authored", "user_input_order"}
    if tag == "message": expected.add("retained_source")
    if tag not in {"message", "function_call", "custom_tool_call"} or set(meta) != expected:
        return False
    order = meta["user_input_order"]
    if type(order) is not int or order < max(0, owner["lastInputOrder"]): return False
    if tag == "message":
        source = meta["retained_source"]
        if (not isinstance(source, dict) or set(source) != {"id", "revision", "complete"}
                or source["complete"] is not True or not isinstance(source["id"], dict)
                or set(source["id"]) != {"message_id", "turn_id", "role"}
                or source["id"] != {"message_id": payload.get("id"), "turn_id": owner["turnId"], "role": "assistant"}
                or not isinstance(source["id"]["message_id"], str) or not source["id"]["message_id"]
                or not isinstance(source["revision"], str)
                or re.fullmatch(r"retained_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", source["revision"]) is None):
            return False
    owner["lastInputOrder"] = order
    return True


def _metadata(payload, owner):
    value = payload.get("internal_chat_message_metadata_passthrough")
    return (isinstance(value, dict) and set(value) <= {"turn_id", "create_time", "content_item_kinds"}
            and value.get("turn_id") == owner["turnId"]
            and ("create_time" not in value or type(value["create_time"]) in (int, float))
            and ("content_item_kinds" not in value or value["content_item_kinds"] == ["unknown"]))


def _operation_owner(prefix, session_id, request):
    """Find the actual launch in the initial immutable prefix, never an append."""
    events = [json.loads(line) for line in prefix.splitlines()]
    turn_id, found, last_order = None, None, -1
    for number, event in enumerate(events):
        payload = event.get("payload", {})
        if event.get("type") == "turn_context":
            next_turn = payload.get("turn_id")
            if next_turn != turn_id: last_order = -1
            turn_id = next_turn
        meta = event.get("metadata", {})
        if (isinstance(meta, dict) and type(meta.get("user_input_order")) is int
                and meta["user_input_order"] >= 0
                and payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") == turn_id):
            last_order = max(last_order, meta["user_input_order"])
        if (event.get("type") == "response_item" and payload.get("type") == "custom_tool_call"
                and payload.get("name") == "exec" and payload.get("input") == request):
            metadata = payload.get("internal_chat_message_metadata_passthrough", {})
            if (not turn_id or metadata.get("turn_id") != turn_id or not payload.get("call_id")
                    or set(payload) - {"type", "id", "status", "name", "input", "call_id",
                                      "internal_chat_message_metadata_passthrough"}):
                raise ValueError("native owner operation launch identity required")
            found = (number, {"sessionId": session_id, "turnId": turn_id,
                             "launchCallId": payload["call_id"], "pendingCallId": payload["call_id"],
                             "cellId": None, "usedCallIds": {payload["call_id"]}, "lastInputOrder": last_order})
            if "metadata" in event and not _transport_metadata(event, found[1]):
                raise ValueError("native owner launch transport identity required")
    if found is None:
        raise ValueError("canonical native owner operation launch required")
    number, owner = found
    # Input arriving after launch but before Python starts must not be absorbed.
    for event in events[number + 1:]:
        if not (_observational_runtime_event(event, session_id) or _owner_event(event, owner)):
            raise ValueError("native session changed after owner launch: new decision input")
    return owner


def _token_count(payload):
    """Exact observed numeric runtime schema, without conversational text."""
    counters = {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens"}
    info, rates = payload.get("info"), payload.get("rate_limits")
    if (set(payload) != {"type", "info", "rate_limits"} or not isinstance(info, dict)
            or set(info) != {"total_token_usage", "last_token_usage", "model_context_window"}
            or type(info["model_context_window"]) is not int or info["model_context_window"] <= 0
            or not isinstance(rates, dict)
            or set(rates) != {"limit_id", "limit_name", "primary", "secondary", "credits", "individual_limit",
                              "spend_control_reached", "plan_type", "rate_limit_reached_type"}):
        return False
    for key in ("total_token_usage", "last_token_usage"):
        if (not isinstance(info[key], dict) or set(info[key]) != counters
                or any(type(v) is not int or v < 0 for v in info[key].values())): return False
    for key in ("primary", "secondary"):
        value = rates[key]
        if value is not None and (not isinstance(value, dict)
                or set(value) != {"used_percent", "window_minutes", "resets_at"}
                or type(value["used_percent"]) not in (int, float) or not 0 <= value["used_percent"] <= 100
                or any(type(value[k]) is not int or value[k] < 0 for k in ("window_minutes", "resets_at"))):
            return False
    credits = rates["credits"]
    return (rates["limit_id"] == "codex" and rates["limit_name"] is None
            and rates["individual_limit"] is None and rates["rate_limit_reached_type"] is None
            and (rates["spend_control_reached"] is None or type(rates["spend_control_reached"]) is bool)
            and rates["plan_type"] in {"pro", "plus", "free", "team", "enterprise", "business"}
            and isinstance(credits, dict) and set(credits) == {"has_credits", "unlimited", "balance"}
            and type(credits["has_credits"]) is bool and type(credits["unlimited"]) is bool
            and (credits["balance"] is None or isinstance(credits["balance"], str)
                 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", credits["balance"]) is not None))


def _owner_output_identity(payload, owner):
    common = {"type", "id", "internal_chat_message_metadata_passthrough"}
    return (not (set(payload) - common - {"call_id", "output"})
            and payload.get("type") in {"custom_tool_call_output", "function_call_output"}
            and payload.get("call_id") == owner["pendingCallId"] and owner["pendingCallId"] is not None
            and (payload["type"] == "custom_tool_call_output") == (payload["call_id"] == owner["launchCallId"]))


def _creation_command_spec(request):
    prefix = 'let r=await tools.exec_command('
    pragma = '// @exec: {"yield_time_ms": 60000,"max_output_tokens":16000}\n'
    body = request.removeprefix(pragma)
    if not body.startswith(prefix) or body.count('tools.exec_command(') != 1:
        raise ValueError("canonical creation exec_command required")
    spec, end = json.JSONDecoder().raw_decode(body[len(prefix):])
    if (not isinstance(spec, dict) or set(spec) != {"cmd", "workdir", "max_output_tokens", "yield_time_ms"}
            or not body[len(prefix) + end:].startswith(');let out=r.output;')
            or any(not isinstance(spec[k], str) or not spec[k] for k in ("cmd", "workdir"))
            or not Path(spec["workdir"]).is_absolute()
            or any(type(spec[k]) is not int or spec[k] <= 0 for k in ("max_output_tokens", "yield_time_ms"))):
        raise ValueError("exact canonical creation command spec required")
    return spec


def _creation_command_completed(event, owner, spec):
    payload = _event_payload(event, owner)
    if (payload is None or event["type"] != "event_msg"
            or set(payload) != {"type", "thread_id", "turn_id", "item", "started_at_ms", "completed_at_ms"}
            or payload["type"] != "item_completed" or payload["thread_id"] != owner["sessionId"]
            or payload["turn_id"] != owner["turnId"]
            or any(type(payload[k]) is not int or payload[k] < 0 for k in ("started_at_ms", "completed_at_ms"))
            or payload["completed_at_ms"] < payload["started_at_ms"]): return None
    item = payload["item"]
    if (not isinstance(item, dict) or set(item) != {"type", "id", "aggregated_output", "command", "cwd", "duration",
            "exit_code", "parsed_cmd", "process_id", "source", "status"}
            or item["type"] != "CommandExecution" or item["status"] != "completed"
            or item["source"] != "unified_exec_startup" or type(item["exit_code"]) is not int or item["exit_code"] != 0
            or item["command"] != ["/bin/zsh", "-lc", spec["cmd"]]
            or item["cwd"] not in (spec["workdir"], Path(spec["workdir"]).as_uri())
            or item["parsed_cmd"] != [{"type": "unknown", "cmd": spec["cmd"]}]
            or not isinstance(item["id"], str) or not item["id"]
            or not isinstance(item["process_id"], str) or re.fullmatch(r"[0-9]+", item["process_id"]) is None
            or not isinstance(item["aggregated_output"], str)):
        return None
    duration = item["duration"]
    if (not isinstance(duration, dict) or set(duration) != {"secs", "nanos"}
            or any(type(duration[k]) is not int for k in duration)
            or duration["secs"] < 0 or not 0 <= duration["nanos"] < 1_000_000_000): return None
    return item


def verify_yielded_scope_creation(lines, session_id, request, terminal_line, marker, *, delegation_line=None):
    """Replay one completed exec/wait chain; never authorize a running output.

    Reuse the owner protocol's exact event and metadata schemas. The terminal
    completion is deliberately *not* accepted by _owner_event / native guards:
    it is decision data, valid only for this immutable scope-creation proof.
    """
    if (not isinstance(lines, list) or type(terminal_line) is not int or not 1 <= terminal_line <= len(lines)
            or not isinstance(request, str) or not request or not isinstance(marker, str) or not marker):
        raise ValueError("bounded native scope creation required")
    prefix = lines[:terminal_line]
    events = [json.loads(line) for line in prefix]
    launches = [n for n, event in enumerate(events) if event.get("type") == "response_item"
        and event.get("payload", {}).get("type") == "custom_tool_call"
        and event["payload"].get("name") == "exec" and event["payload"].get("input") == request]
    if len(launches) != 1:
        raise ValueError("unique canonical native creation launch required")
    launch = launches[0]; protocol = prefix[launch:]
    if (len(protocol) > 4096 or sum(map(len, protocol)) > 8 * 1024 * 1024
            or any(not isinstance(line, bytes) or not line.endswith(b"\n") for line in protocol)):
        raise ValueError("bounded complete native creation protocol required")
    owner = _operation_owner(b"".join(prefix[:launch + 1]), session_id, request)
    launch_payload = _event_payload(events[launch], owner)
    if (launch_payload is None or not _metadata(launch_payload, owner)
            or not isinstance(owner["launchCallId"], str) or not owner["launchCallId"]
            or launch_payload.get("status", "completed") != "completed"):
        raise ValueError("exact native creation launch metadata required")
    prior_call_ids = {event["payload"]["call_id"] for event in events[:launch]
        if event.get("type") == "response_item" and event.get("payload", {}).get("type") in {"custom_tool_call", "function_call"}
        and isinstance(event["payload"].get("call_id"), str)}
    if owner["launchCallId"] in prior_call_ids:
        raise ValueError("native creation call identity reused")
    owner["usedCallIds"].update(prior_call_ids)
    spec = _creation_command_spec(request)
    waits = []
    completion = None
    for number, event in enumerate(events[launch + 1:-1], launch + 2):
        payload = event.get("payload", {})
        command = _creation_command_completed(event, owner, spec)
        if command is not None:
            if completion is not None or not waits or owner["pendingCallId"] != waits[-1]:
                raise ValueError("unique pending native creation command completion required")
            completion = (number, command)
            continue
        if (payload.get("turn_id", owner["turnId"]) != owner["turnId"]
                or native.CREATED in json.dumps(event, ensure_ascii=False)
                or payload.get("type") == "function_call" and payload.get("status", "completed") != "completed"):
            raise ValueError("native creation turn/marker differs")
        if completion is not None and event.get("type") == "response_item" and payload.get("type") in {
                "custom_tool_call", "function_call", "custom_tool_call_output", "function_call_output"}:
            raise ValueError("new tool after native creation command completion")
        if not (_observational_runtime_event(event, session_id) or _owner_event(event, owner)):
            raise ValueError("native creation has unsupported continuation")
        if event.get("type") == "response_item" and payload.get("type") == "function_call":
            waits.append(payload["call_id"])
            if len(waits) > 64: raise ValueError("native creation wait bound exceeded")
    terminal = events[-1]; payload = _event_payload(terminal, owner)
    if (not waits or owner["cellId"] is None or terminal.get("type") != "response_item"
            or payload is None or not _metadata(payload, owner) or payload.get("type") != "function_call_output"
            or not _owner_output_identity(payload, owner)):
        raise ValueError("pending same-cell native creation completion required")
    output = payload.get("output")
    if not (isinstance(output, str) or isinstance(output, list) and len(output) == 2 and _text_blocks(output, "input_text")):
        raise ValueError("exact native creation output transport required")
    text = native._output_text(output)
    completed = re.fullmatch(r"Script completed\nWall time [0-9]+(?:\.[0-9]+)? seconds\nOutput:\n(.*)",
        text, flags=re.DOTALL)
    markers = [line for line in completed[1].splitlines() if line.startswith(native.CREATED)] if completed else []
    if not completed or not text.endswith("\n") or text.count(native.CREATED) != 1 or markers != [marker]:
        raise ValueError("successful exact native scope creation completion required")
    stdout = output[1]["text"] if isinstance(output, list) else completed[1]
    if (isinstance(output, list) and re.fullmatch(
            r"Script completed\nWall time [0-9]+(?:\.[0-9]+)? seconds\nOutput:\n", output[0]["text"]) is None
            or completion is None or completion[1]["aggregated_output"] != stdout):
        raise ValueError("canonical command and native terminal stdout differ")
    trailing = []
    if delegation_line is not None:
        if type(delegation_line) is not int or not terminal_line < delegation_line <= len(lines):
            raise ValueError("native creation delegation boundary differs")
        trailing = lines[terminal_line:delegation_line - 1]
        if (len(trailing) + len(protocol) > 4096 or sum(map(len, trailing)) + sum(map(len, protocol)) > 8 * 1024 * 1024
                or any(not line.endswith(b"\n") for line in trailing)):
            raise ValueError("bounded pre-delegation native events required")
        for line in trailing:
            event = json.loads(line); payload = event.get("payload", {})
            if (event.get("type") == "response_item" and payload.get("type") in {
                    "custom_tool_call", "function_call", "custom_tool_call_output", "function_call_output"}
                    or native.CREATED in json.dumps(event, ensure_ascii=False)
                    or payload.get("turn_id", owner["turnId"]) != owner["turnId"]
                    or not (_observational_runtime_event(event, session_id) or _owner_event(event, owner))):
                raise ValueError("new input/tool after native creation terminal")
    return {"schemaVersion": "native-yielded-scope-creation/v1", "sessionId": session_id,
        "turnId": owner["turnId"], "cellId": owner["cellId"], "launchCallId": owner["launchCallId"],
        "waitCallIds": waits, "launchEventLine": launch + 1, "terminalEventLine": terminal_line,
        "launchEventSha256IncludingFinalLf": context_api._hash(prefix[launch]),
        "terminalEventSha256IncludingFinalLf": context_api._hash(prefix[-1]),
        "protocolSha256IncludingFinalLf": context_api._hash(b"".join(protocol)),
        "terminalOutputSha256": sha256_json(output), "terminalTextSha256": context_api._hash(text.encode()),
        "canonicalExecCommandSha256": sha256_json(spec),
        "commandCompletionEventLine": completion[0],
        "commandCompletionEventSha256IncludingFinalLf": context_api._hash(prefix[completion[0] - 1]),
        "commandStdoutSha256": context_api._hash(stdout.encode()),
        "preDelegationSha256IncludingFinalLf": context_api._hash(b"".join(trailing))}


def _owner_event(event, owner):
    """Finite owner-only wait protocol; no other tool/output is observational."""
    payload = _event_payload(event, owner)
    if payload is None: return False
    kind = event["type"]
    if kind == "event_msg" and payload.get("type") == "token_count":
        return _token_count(payload)
    if kind == "event_msg" and payload.get("type") == "item_completed":
        item = payload.get("item", {})
        if (set(payload) != {"type", "thread_id", "turn_id", "item", "started_at_ms", "completed_at_ms"}
                or payload["thread_id"] != owner["sessionId"] or payload["turn_id"] != owner["turnId"]
                or any(type(payload[k]) is not int or payload[k] < 0 for k in ("started_at_ms", "completed_at_ms"))
                or not isinstance(item, dict) or not isinstance(item.get("id"), str)):
            return False
        if item.get("type") == "Reasoning":
            return (set(item) == {"type", "id", "summary_text", "raw_content"}
                    and isinstance(item["summary_text"], list) and isinstance(item["raw_content"], list))
        return (item.get("type") == "AgentMessage" and set(item) == {"type", "id", "content", "phase"}
                and item["phase"] == "commentary" and _text_blocks(item["content"], "Text"))
    if kind != "response_item" or not _metadata(payload, owner): return False
    tag = payload.get("type")
    common = {"type", "id", "internal_chat_message_metadata_passthrough"}
    if tag == "reasoning":
        return (set(payload) <= common | {"summary", "content", "encrypted_content"}
                and isinstance(payload.get("summary"), list)
                and ("encrypted_content" not in payload or isinstance(payload["encrypted_content"], str)))
    if tag == "message":
        return (set(payload) <= common | {"role", "phase", "content", "end_turn", "status"}
                and payload.get("role") == "assistant" and payload.get("phase") == "commentary"
                and ("status" not in payload or payload["status"] == "completed")
                and _text_blocks(payload.get("content"), "output_text"))
    if tag == "function_call":
        if (set(payload) - common - {"name", "arguments", "call_id", "status"}
                or payload.get("name") != "wait" or owner["cellId"] is None or owner["pendingCallId"] is not None):
            return False
        try: args = json.loads(payload["arguments"])
        except (ValueError, TypeError, KeyError): return False
        if (not isinstance(args, dict) or set(args) - {"cell_id", "yield_time_ms", "max_tokens", "terminate"}
                or args.get("cell_id") != owner["cellId"] or args.get("terminate", False) is not False
                or any(type(args[k]) is not int or args[k] < 0 for k in ("yield_time_ms", "max_tokens") if k in args)
                or not isinstance(payload.get("call_id"), str) or not payload["call_id"]
                or payload["call_id"] in owner["usedCallIds"]):
            return False
        owner["usedCallIds"].add(payload["call_id"])
        owner["pendingCallId"] = payload["call_id"]
        return True
    if tag in {"custom_tool_call_output", "function_call_output"}:
        if not _owner_output_identity(payload, owner):
            return False
        if not isinstance(payload.get("output"), str): return False
        match = re.fullmatch(r"Script running with cell ID ([0-9]+)\nWall time [0-9]+(?:\.[0-9]+)? seconds\nOutput:\n", payload.get("output", ""))
        if not match or owner["cellId"] not in (None, match[1]): return False
        owner["cellId"], owner["pendingCallId"] = match[1], None
        return True
    return False


def _text_blocks(value, kind):
    def valid(block):
        if (not isinstance(block, dict) or block.get("type") != kind
                or not isinstance(block.get("text"), str)):
            return False
        if set(block) == {"type", "text"}: return True
        # The live assistant commentary envelope also carries two empty
        # transport arrays. Nonempty annotations/logprobs are not interpreted
        # here and therefore remain unsupported rather than becoming inputs.
        return (kind == "output_text" and set(block) == {"type", "text", "annotations", "logprobs"}
                and block["annotations"] == [] and block["logprobs"] == [])
    return isinstance(value, list) and all(valid(block) for block in value)


def _validate_native_suffix(suffix, session_id, owner=None):
    if suffix and not suffix.endswith(b"\n"):
        raise ValueError("complete LF-terminated native append required")
    state = deepcopy(owner)
    for line in suffix.splitlines():
        try: event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("complete native append JSON required") from exc
        if not (_observational_runtime_event(event, session_id) or state is not None and _owner_event(event, state)):
            raise ValueError("native session changed during verification: new decision input")


def _assert_native_guard(guard, *, terminal=False):
    for path, snapshot in guard.items():
        context_api._physical(path)
        signature = _signature(path.stat())
        if not terminal and signature == snapshot.get("signature"):
            continue
        before, after, raw = _native_journal_bytes(path)
        identity = (snapshot["device"], snapshot["inode"])
        if ((after.st_dev, after.st_ino) != identity
                or min(before.st_size, len(raw)) < snapshot["observedLength"]
                or context_api._hash(raw[:snapshot["length"]]) != snapshot["prefixSha256"]):
            raise ValueError("native session changed during verification")
        _validate_native_suffix(raw[snapshot["length"]:], snapshot["sessionId"], snapshot.get("owner"))
        snapshot["observedLength"] = max(snapshot["observedLength"], after.st_size)
        snapshot["signature"] = _signature(after) if _signature(before) == _signature(after) else None


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
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(actual_argv)
    if args.command == "prepare" and (args.context is None or args.phase is None):
        parser.error("prepare requires --context and --phase")
    if args.command == "record" and args.approval is None:
        parser.error("record requires --approval")
    if bool(args.model) != bool(args.reasoning_effort):
        parser.error("execution model and reasoning effort must be selected together")
    with native_operation(actual_argv):
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
