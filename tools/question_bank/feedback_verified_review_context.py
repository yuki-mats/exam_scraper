"""Immutable, read-only review inputs for a genuinely saved verified25 bundle.

This is not a maintenance executor, checkpoint importer, or publisher.  The
dedicated identity never stands in for a record under 00_source.  Production
always replays load_saved; tests may mock that boundary, the CLI cannot.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from tools.question_bank import feedback_verified_save as saver
from tools.question_review_console import independent_approval as native
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.work_versions import evaluation_policy

SCHEMA = "feedback-verified-review-context/v1"
INPUT_SCHEMA = "feedback-verified-readonly-question/v1"
RECEIPT_SCHEMA = "feedback-verified-review-context-receipt/v1"
ORDER_SCHEMA = "feedback-verified-official-order/v1"
FILES = ("context.json", "review-input.json", "receipt.json")
LABELS = ("イ", "ロ", "ハ", "ニ", "ホ")
CORE_STAGES = frozenset({"question_type", "question_intent", "correct_choice", "explanation", "question_set"})
LAW_STAGES = frozenset({"law_context", "law_audit"})


def _physical(path):
    value = Path(path).absolute()
    if value != value.resolve() or value.is_symlink():
        raise ValueError("physical path required")
    return value


def _private(root, path):
    value = _physical(path)
    if value == root / saver.PRIVATE or not value.is_relative_to(root / saver.PRIVATE):
        raise ValueError("dedicated private execution path required")
    return value


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _ref(path):
    path = _physical(path)
    return {"ref": str(path), "sha256": _hash(path.read_bytes())}


def _json_snapshot(path):
    """Decode exactly the bytes used for this file reference, never re-read."""
    path = _physical(path)
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON object input required")
    return {"ref": str(path), "sha256": _hash(raw)}, value


def _bound(root, ref, *, within_root=True):
    if (not isinstance(ref, dict) or set(ref) != {"ref", "sha256"}
            or not isinstance(ref["ref"], str) or not Path(ref["ref"]).is_absolute()
            or re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"])) is None):
        raise ValueError("exact input reference required")
    path = _physical(ref["ref"])
    if within_root and not path.is_relative_to(root):
        raise ValueError("input outside checkout")
    raw = path.read_bytes()
    if _hash(raw) != ref["sha256"]:
        raise ValueError("input hash changed")
    return path, raw


def _current_policies(root, qualification):
    stages = QualificationWorkflow(root, None).versioned_policies(qualification)
    if not stages:
        raise ValueError("current maintenance policies unavailable")
    policy = evaluation_policy(root)
    return {"maintenance": stages, "evaluation": policy}


def _policy_inputs(root, policies):
    paths = {Path(__file__).resolve()}
    from tools.question_review_console import qualification_workflow, work_versions, workflow_catalog
    for module in (qualification_workflow, work_versions, workflow_catalog):
        paths.add(Path(module.__file__).resolve())
    fallback = Path(__file__).resolve().parents[2]
    for policy in [*policies["maintenance"].values(), policies["evaluation"]]:
        for relative in [*policy.get("canonicalDocs", []), *policy.get("inputs", [])]:
            path = root / relative
            if not path.is_file():
                path = fallback / relative
            if not path.is_file():
                raise ValueError("current policy input missing")
            paths.add(_physical(path))
    for base in (root, fallback):
        catalog = base / "config/question_maintenance_workflow.toml"
        if catalog.is_file():
            paths.add(_physical(catalog))
    return {str(path): _hash(path.read_bytes()) for path in sorted(paths)}


def _official_order(root, mapping_ref, *, plan, receipt, rows, official_pdf):
    """Use explicit IDs and an already verified native final, never row indexes.

    Only unambiguous ``s01=イ`` / ``イ=s01`` evidence tokens are supported.
    Parallel lists, OCR approximations and choiceIndex guesses are not authority.
    """
    if mapping_ref is None:
        return None, {}
    mapping_path, raw = _bound(root, mapping_ref)
    mapping = json.loads(raw)
    if (not isinstance(mapping, dict)
            or set(mapping) != {"schemaVersion", "planHash", "originalQuestionId", "officialQuestionPdf", "approvalRef", "statements"}
            or mapping["schemaVersion"] != ORDER_SCHEMA or mapping["planHash"] != plan["planHash"]
            or mapping["originalQuestionId"] != rows[0]["originalQuestionId"]
            or mapping["officialQuestionPdf"] != official_pdf):
        raise ValueError("explicit official order binding differs")
    approval_path, approval_raw = _bound(root, mapping["approvalRef"])
    approval = json.loads(approval_raw)
    if approval != receipt["approvalEvidence"]:
        raise ValueError("official mapping requires saved final native approval")
    event, _, _ = native._event(approval["reviewerFinal"], production=True)
    final = native._response(event, "message")
    verdict = json.loads(native._text(final))
    if (sha256_json(verdict) != receipt["approvalBinding"]["resultHash"]
            or verdict.get("schemaVersion") != "independent-scope-review/v1"
            or verdict.get("decision") != "approved" or verdict.get("unresolvedFindings") != []
            or verdict.get("scope", {}).get("planHash") != plan["planHash"]
            or verdict["scope"].get("candidateSha256") != plan["candidateSha256"]):
        raise ValueError("official mapping native final differs")
    evidence = verdict.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(v, str) for v in evidence):
        raise ValueError("native mapping evidence required")
    text = "\n".join(evidence)
    statements = mapping["statements"]
    ids = [row["questionId"] for row in rows]
    if (len(rows) != len(LABELS) or not isinstance(statements, list)
            or len(statements) != len(rows) or any(not isinstance(s, dict) for s in statements)
            or any(type(s.get("officialOrderIndex")) is not int for s in statements)
            or [s.get("officialOrderIndex") for s in statements] != list(range(len(rows)))
            or [s.get("officialLabel") for s in statements] != list(LABELS)
            or {s.get("questionId") for s in statements} != set(ids)):
        raise ValueError("complete unique official order required")
    suffixes = [identity.rsplit("-", 1)[-1] for identity in ids]
    for statement in statements:
        if set(statement) != {"questionId", "officialOrderIndex", "officialLabel", "choiceTextSha256"}:
            raise ValueError("exact official statement mapping required")
        identity = statement["questionId"]
        row = next(row for row in rows if row["questionId"] == identity)
        if statement["choiceTextSha256"] != _hash(row["originalQuestionChoiceText"].encode()):
            raise ValueError("official statement text binding differs")
        aliases = [identity]
        short = identity.rsplit("-", 1)[-1]
        if len(set(suffixes)) == len(ids) and re.fullmatch(r"s\d{2}", short):
            aliases.append(short)
        found = set()
        for alias in aliases:
            escaped = re.escape(alias)
            found.update(re.findall(r"(?<![A-Za-z0-9_-])" + escaped + r"\s*=\s*([イロハニホ])(?![イロハニホ])", text))
            found.update(re.findall(r"(?<![イロハニホ])([イロハニホ])\s*=\s*" + escaped + r"(?![A-Za-z0-9_-])", text))
        if found != {statement["officialLabel"]}:
            raise ValueError("native evidence lacks unique explicit ID/label mapping")
    return deepcopy(statements), {str(mapping_path): _hash(raw), str(approval_path): _hash(approval_raw)}


def _derive(root, plan_path, *, connection, official_order_ref=None):
    if connection.in_transaction:
        raise ValueError("review verification must run outside a ledger transaction")
    root, plan_path = _physical(root), _private(_physical(root), plan_path)
    plan_ref, plan = _json_snapshot(plan_path)
    if sha256_json({k: v for k, v in plan.items() if k != "planHash"}) != plan.get("planHash"):
        raise ValueError("snapshotted save plan hash differs")
    receipt_path = plan_path.parent / "save-receipt.json"
    receipt_ref, receipt = _json_snapshot(receipt_path)
    # Mandatory production boundary. No assertion, CLI flag, or fake receipt mode.
    verified_receipt = saver.load_saved(root, plan_path, receipt_path, connection=connection)
    # load_saved replays read_plan and checks its planHash against this receipt.
    # The independently sealed planHash above fixes the exact decoded plan;
    # equality here prevents using a different receipt from its separate reads.
    if verified_receipt != receipt:
        raise ValueError("saved receipt changed during verification")
    if (receipt.get("schemaVersion") not in {saver.RECEIPT, saver.MAINTENANCE_RECEIPT}
            or receipt.get("planHash") != plan["planHash"]
            or receipt.get("candidateSha256") != plan["candidateSha256"]
            or receipt.get("formalPath") != plan["formalPath"]
            or receipt.get("FirestoreWritten") is not False):
        raise ValueError("genuine formal save receipt required")
    canonical = _physical(root / plan["formalPath"])
    canonical_ref = {"ref": str(canonical), "sha256": plan["candidateSha256"]}
    try:
        _, canonical_raw = _bound(root, canonical_ref)
    except ValueError as exc:
        raise ValueError("canonical third value differs from saved candidate") from exc
    bundle = json.loads(canonical_raw)
    qualification, group = bundle.get("qualificationId"), str(bundle.get("list_group_id", ""))
    expected = root / "output" / str(qualification) / "questions_json" / group / "25_verified_publication" / f"{group}_verified_publication.json"
    records = bundle.get("questions")
    if (bundle.get("schemaVersion") != saver.gas.SCHEMA_VERSION
            or qualification not in saver.gas.QUALIFICATIONS or re.fullmatch(r"\d{4}", group) is None
            or canonical != expected or not isinstance(records, list) or not records
            or bundle.get("total_count") != len(records)):
        raise ValueError("verified25 canonical identity/count differs")
    all_ids = [row.get("questionId") if isinstance(row, dict) else None for row in records]
    if (any(not isinstance(i, str) or not i or i.strip() != i or "/" in i for i in all_ids)
            or len(set(all_ids)) != len(all_ids)):
        raise ValueError("canonical document IDs missing or duplicate")
    ids = plan["siblingIds"]
    if not ids or len(set(ids)) != len(ids) or not set(ids) <= set(all_ids):
        raise ValueError("complete saved sibling IDs required")
    by_id = dict(zip(all_ids, records, strict=True))
    original = by_id[ids[0]].get("originalQuestionId")
    rows = [row for row in records if row.get("originalQuestionId") == original]
    if (not isinstance(original, str) or not original or "/" in original
            or [row["questionId"] for row in rows] != ids
            or any(row.get("qualificationId") != qualification or str(row.get("listGroupId")) != group
                   or row.get("examYear") != int(group) or row.get("questionType") != "true_false"
                   or row.get("isDeleted") is not False or row.get("isChoiceOnly") is not False for row in rows)):
        raise ValueError("saved sibling order or identity differs")
    bodies = {row.get("originalQuestionBodyText") for row in rows}
    if len(bodies) != 1 or not all(isinstance(body, str) and body.strip() for body in bodies):
        raise ValueError("one immutable original body required")
    for row in rows:
        if (not isinstance(row.get("originalQuestionChoiceText"), str) or not row["originalQuestionChoiceText"].strip()
                or row.get("correctChoiceText") not in {"正しい", "間違い"}
                or not isinstance(row.get("explanationText"), str) or not row["explanationText"].strip()):
            raise ValueError("complete statement/verdict/explanation required")
    index_path, index_raw = _bound(root, plan["documentIndexRef"])
    verification_path, verification_raw = _bound(root, plan["pdfVerificationRef"])
    indexed = json.loads(index_raw).get("records", [])
    index_ids = [r.get("questionId") for r in indexed]
    if (len(index_ids) != len(set(index_ids)) or not set(ids) <= set(index_ids)):
        raise ValueError("official index ID coverage differs")
    index_by_id = dict(zip(index_ids, indexed, strict=True))
    official = [index_by_id[identity] for identity in ids]
    grade = qualification.removeprefix("gas-shunin-")
    keys = {(r.get("grade"), r.get("examYear"), r.get("section"), r.get("questionNumber")) for r in official}
    if (len(keys) != 1 or next(iter(keys))[:2] != (grade, int(group))
            or next(iter(keys))[2] not in {"law", "basic", "gas"}
            or type(next(iter(keys))[3]) is not int or next(iter(keys))[3] < 1
            or any(r.get("requiresContentRepair") is not False
                   or r.get("match", {}).get("status") not in {"verified_question_block", "verified_visual_override"}
                   or r.get("match", {}).get("bestBlockIdentity") != {
                       "grade": grade, "examYear": int(group),
                       "section": r.get("section"), "questionNumber": r.get("questionNumber")}
                   for r in official)):
        raise ValueError("official index question identity unresolved or mixed")
    pdfs, inputs = {}, dict(plan["inputs"])
    for row in official:
        for field in ("officialQuestionPdf", "officialAnswerPdf"):
            ref = row[field]
            if type(ref.get("pdfPage")) is not int or ref["pdfPage"] < 1:
                raise ValueError("official PDF page required")
            file_ref = {"ref": str(root / ref["path"]), "sha256": ref["sha256"]}
            path, raw = _bound(root, file_ref)
            inputs[str(path)] = _hash(raw)
            value = {**file_ref, "pdfPage": ref["pdfPage"]}
            if field in pdfs and pdfs[field] != value:
                raise ValueError("official sibling PDF locators differ")
            pdfs[field] = value
    order, order_inputs = _official_order(root, official_order_ref, plan=plan, receipt=receipt,
                                        rows=rows, official_pdf=pdfs["officialQuestionPdf"])
    policies = _current_policies(root, qualification)
    inputs.update(_policy_inputs(root, policies))
    inputs.update(order_inputs)
    extra_refs = [_ref(plan["beforeRef"]), _ref(plan["candidateRef"]),
                  _ref(receipt["originalRef"]), _ref(Path(receipt["originalRef"]).parent / "intent.json")]
    for ref in (plan_ref, receipt_ref, canonical_ref, plan["documentIndexRef"], plan["pdfVerificationRef"], *extra_refs):
        if str(_physical(ref["ref"])) in inputs and inputs[ref["ref"]] != ref["sha256"]:
            raise ValueError("conflicting fixed input hashes")
        inputs[ref["ref"]] = ref["sha256"]
    policy_ids = set(policies["maintenance"])
    law_known = all(row.get("isLawRelated") is True for row in rows)
    required_set = CORE_STAGES | (LAW_STAGES if law_known else set())
    unresolved = (policy_ids - CORE_STAGES - LAW_STAGES - {"originalize"}) | (required_set - policy_ids)
    if not law_known:
        unresolved |= LAW_STAGES
    required = [key for key in policies["maintenance"] if key in required_set]
    missing = [{"stageId": key, "policyVersion": policies["maintenance"][key]["policyVersion"],
                "evidenceStatus": "not_validated", "executionAdapterSupported": False} for key in required]
    blockers = ["verified25_stage_execution_adapter_unimplemented", "current_stage_receipts_not_validated",
                "independent_evaluation_not_executed", "verified25_publication_adapter_unimplemented"]
    if unresolved:
        blockers.append("stage_applicability_unresolved")
    if order is None:
        blockers.append("official_order_unresolved")
    positions = {identity: all_ids.index(identity) for identity in ids}
    selected = [by_id[s["questionId"]] for s in order] if order is not None else rows
    bindings = []
    for number, row in enumerate(selected):
        identity = row["questionId"]
        binding = {"questionId": identity, "projectionChoiceIndex": number,
                   "canonicalRecordIndex": positions[identity],
                   "officialDocumentIndexRecord": deepcopy(index_by_id[identity]),
                   "officialStatementLabel": order[number]["officialLabel"] if order is not None else None,
                   "officialOrderIndex": number if order is not None else None}
        bindings.append(binding)
    review_input = {"schemaVersion": INPUT_SCHEMA,
        "identityKind": "verified25_publication", "originalQuestionId": original,
        "projectionOrder": "official_explicit_native_mapping" if order is not None else "canonical_only_not_official",
        "choiceBindings": bindings, "documentsById": {row["questionId"]: deepcopy(row) for row in rows},
        "projected": {"questionBodyText": next(iter(bodies)), "questionType": "true_false",
            "choiceTextList": [r["originalQuestionChoiceText"] for r in selected],
            "correctChoiceText": [r["correctChoiceText"] for r in selected],
            "explanationText": [r["explanationText"] for r in selected],
            "suggestedQuestionsByChoice": [r.get("suggestedQuestions") for r in selected],
            "suggestedQuestionDetailsByChoice": [r.get("suggestedQuestionDetails") for r in selected],
            "lawReferencesByChoice": [r.get("lawReferences") for r in selected],
            "lawRevisionFactsByChoice": [r.get("lawRevisionFacts") for r in selected],
            "questionImageStorageUrlsByChoice": [r.get("questionImageStorageUrls") for r in selected],
            "originalQuestionChoiceImageUrls": [r.get("originalQuestionChoiceImageUrls") for r in selected],
            "classificationByChoice": [{k: r[k] for k in ("questionIntent", "isCalculationQuestion", "isLawRelated", "lawGroundedExplanationNotNeeded", "questionLearningPatternId", "questionSetId", "questionTags") if k in r} for r in selected]}}
    identity = {"kind": "verified25_publication", "qualificationId": qualification, "listGroupId": group,
                "originalQuestionId": original, "canonical": canonical_ref, "siblingIds": list(ids)}
    context = {"schemaVersion": SCHEMA, "repoRoot": str(root), "identity": identity,
        "save": {"plan": plan_ref, "planHash": plan["planHash"], "receipt": receipt_ref,
                 "receiptSchemaVersion": receipt["schemaVersion"]},
        "officialEvidence": {"documentIndex": plan["documentIndexRef"], "pdfVerification": plan["pdfVerificationRef"], **pdfs},
        "officialOrderInput": deepcopy(official_order_ref), "officialOrder": order,
        "canonicalSiblingOrder": list(ids), "fieldScopes": deepcopy(plan["publicationDocuments"]),
        "taskRequirement": deepcopy(plan["taskRequirement"]), "policies": policies,
        "inputs": inputs, "inputSetHash": sha256_json(inputs), "reviewInputHash": sha256_json(review_input),
        "preflight": {"machineReady": False, "publicationReady": False,
                      "checkpointRecorded": False, "evaluationPerformed": False,
                      "requiredStageIds": required,
                      "nonApplicableStages": [{"stageId": "originalize", "reason": "official_exam_year_verified"}]
                          if "originalize" in policy_ids else [],
                      "applicabilityUnresolvedStageIds": sorted(unresolved),
                      "missingStages": missing, "blockers": blockers}}
    context["contextHash"] = sha256_json(context)
    _check_inputs(context)
    return context, review_input


def _check_inputs(context):
    if context["inputSetHash"] != sha256_json(context["inputs"]):
        raise ValueError("input set hash differs")
    for path, digest in context["inputs"].items():
        if _hash(_physical(path).read_bytes()) != digest:
            raise ValueError("fixed review input changed")
    identity = context["identity"]
    # Store root explicitly; do not derive a checkout from private path depth.
    root = Path(context["repoRoot"])
    if _current_policies(root, identity["qualificationId"]) != context["policies"]:
        raise ValueError("current review policy changed")


def _artifacts(output, context, review_input):
    contents = {"context.json": _bytes(context), "review-input.json": _bytes(review_input)}
    receipt = {"schemaVersion": RECEIPT_SCHEMA, "contextHash": context["contextHash"],
        "inputSetHash": context["inputSetHash"],
        "artifacts": {name: {"ref": str(output / name), "sha256": _hash(raw)} for name, raw in contents.items()},
        "machineReady": False, "publicationReady": False, "checkpointRecorded": False,
        "evaluationPerformed": False, "FirestoreWritten": False}
    contents["receipt.json"] = _bytes(receipt)
    return contents, receipt


def _exclusive(path, raw):
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(pending, path)  # Atomic no-replace; complete bytes only.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(pending)


def _check_output_set(output, *, complete, message="review output scope changed before completion"):
    """The same physical directory/name contract at both entry and return."""
    _physical(output)
    if not output.is_dir():
        raise ValueError(message)
    names = {path.name for path in output.iterdir()}
    if names - set(FILES) or complete and names != set(FILES):
        raise ValueError(message)


def _verify_complete_outputs(output, contents, context):
    """Verify artifacts, fixed inputs/policies, then the final output scope."""
    _check_output_set(output, complete=True)
    if any(_physical(output / name).read_bytes() != raw for name, raw in contents.items()):
        raise ValueError("review context inputs, policy or artifacts changed before completion")
    _check_inputs(context)
    _check_output_set(output, complete=True)


def prepare(root, plan_path, output, *, connection, official_order=None, failure_hook=None):
    root = _physical(root); output = _private(root, output)
    order_ref = _ref(official_order) if official_order is not None else None
    context, review_input = _derive(root, plan_path, connection=connection, official_order_ref=order_ref)
    contents, receipt = _artifacts(output, context, review_input)
    if output.exists():
        _check_output_set(output, complete=False, message="review output conflict")
    for name, raw in contents.items():
        path = output / name
        if path.exists() and (_physical(path).read_bytes() != raw):
            raise ValueError("immutable review output conflict")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, raw in contents.items():
        _check_inputs(context)
        _check_output_set(output, complete=False, message="review output scope changed during preparation")
        path = output / name
        if not path.exists():
            try:
                _exclusive(path, raw)
            except FileExistsError:
                if _physical(path).read_bytes() != raw:
                    raise ValueError("competing immutable output")
        if failure_hook:
            failure_hook(name)
    _check_inputs(context)
    _verify_complete_outputs(output, contents, context)
    return receipt


def inspect(root, output, *, connection):
    root = _physical(root); output = _private(root, output)
    _check_output_set(output, complete=True, message="complete review context receipt required")
    saved = json.loads(_physical(output / "context.json").read_bytes())
    if saved.get("schemaVersion") != SCHEMA:
        raise ValueError("dedicated review context required")
    context, review_input = _derive(root, saved["save"]["plan"]["ref"], connection=connection,
                                    official_order_ref=saved["officialOrderInput"])
    contents, receipt = _artifacts(output, context, review_input)
    _verify_complete_outputs(output, contents, context)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "inspect"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official-order", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare" and args.plan is None:
        parser.error("prepare requires --plan")
    connection = sqlite3.connect(saver.LEDGER.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if args.command == "prepare":
            result = prepare(args.repo_root, args.plan, args.output, connection=connection,
                             official_order=args.official_order)
        else:
            result = inspect(args.repo_root, args.output, connection=connection)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
