"""Register independently found defects in the existing feedback ledger.

Content review is not a formal-save or publication approval. Native session files
are local runtime evidence, not cryptographic signatures. This module neither
opens a database nor writes question artifacts or production documents.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_bank.feedback_improvement_plan import EXPLANATION_FIELDS
from scripts.pipeline.rebuild_gas_shunin_all_year_artifacts import METADATA_FIELDS
from tools.question_review_console import independent_approval as native
from tools.question_review_console.projection import sha256_json

SCHEMA = "feedback-maintenance-task-binding/v1"
PRIVATE = Path("output/user_feedback_response_system/execution")
FIELDS = frozenset({"explanationText", "suggestedQuestions", "suggestedQuestionDetails", "lawRevisionFacts"})
STATES = ("open", "review_pending", "blocked", "formally_saved", "published_unconfirmed", "completed")


def _physical(path):
    path = Path(path).absolute()
    if path != path.resolve() or path.is_symlink():
        raise ValueError("physical root/reference required")
    return path


def _file(root, ref):
    if (not isinstance(ref, dict) or set(ref) != {"ref", "sha256"}
            or not isinstance(ref.get("ref"), str) or not Path(ref["ref"]).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", "")))):
        raise ValueError("exact private file reference required")
    path = _physical(ref["ref"])
    if not path.is_relative_to(root / PRIVATE):
        raise ValueError("reference outside private execution root")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != ref["sha256"]:
        raise ValueError("referenced bytes changed")
    return path, content


def _json(root, ref):
    path, content = _file(root, ref)
    return path, json.loads(content)


def _requirement(connection, task_id):
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return task_requirement_binding(cursor, task_id)
    finally:
        cursor.close()


def _verdict(text):
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        match = re.fullmatch(r"\s*```json\s*([\s\S]*?)\s*```\s*(?:<oai-mem-citation>[\s\S]*</oai-mem-citation>\s*)?", text)
        if not match:
            raise ValueError("structured native content final required")
        result = json.loads(match[1])
    if not isinstance(result, dict):
        raise ValueError("native content final must be an object")
    return result


def _native_review(audit, scope, scope_ref, creator):
    if (audit.get("schemaVersion") != "feedback-native-content-review-record/v1"
            or audit.get("formalSaveApproved") is not False or audit.get("publicationApproved") is not False):
        raise ValueError("dedicated content-only native record required")
    session = _physical(audit["reviewerFinal"]["sessionPath"])
    signature = session.stat()
    final, meta, lines = native._event(audit["reviewerFinal"], production=True)
    complete, complete_meta, _ = native._event(audit["reviewerComplete"], production=True)
    spawn = meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
    reviewer = meta["id"]
    if (not creator or reviewer == creator or meta.get("parent_thread_id") != creator
            or spawn.get("parent_thread_id") != creator
            or audit.get("reviewerSessionId") != reviewer
            or audit.get("reviewerAgentPath") != spawn.get("agent_path")
            or complete_meta["id"] != reviewer
            or audit["reviewerFinal"]["sessionPath"] != audit["reviewerComplete"]["sessionPath"]):
        raise ValueError("creator and independent native reviewer identity differ")
    payload = native._response(final, "message")
    if (payload.get("role") != "assistant" or payload.get("phase") != "final_answer"
            or not payload.get("content")
            or any(c.get("type") != "output_text" for c in payload["content"])):
        raise ValueError("native assistant final required")
    text = native._text(payload)
    result = _verdict(text)
    turn = payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id")
    done = complete.get("payload", {})
    starts = [json.loads(line).get("payload", {}) for line in lines[:audit["reviewerFinal"]["eventLine"] - 1]
              if json.loads(line).get("type") == "event_msg"
              and json.loads(line).get("payload", {}).get("type") == "task_started"
              and json.loads(line).get("payload", {}).get("turn_id") == turn]
    if (not turn or audit.get("reviewerTurnId") != turn
            or len(starts) != 1 or not starts[0].get("root_turn_id")
            or done.get("root_turn_id") != starts[0]["root_turn_id"]
            or complete.get("type") != "event_msg" or done.get("type") != "task_complete"
            or done.get("turn_id") != turn or done.get("last_agent_message") != text
            or audit["reviewerComplete"]["eventLine"] <= audit["reviewerFinal"]["eventLine"]
            or audit.get("rawFinal") != text or audit.get("result") != result):
        raise ValueError("native completed final/record binding differs")
    if (result.get("schemaVersion") != "independent-content-review/v1"
            or result.get("reviewer") != spawn.get("agent_path")
            or result.get("scopeHash") != scope["scopeHash"]
            or result.get("scopeFileSha256") != scope_ref["sha256"]
            or result.get("candidateSha256") != scope["candidate"]["sha256"]
            or result.get("decision") != "approved" or result.get("unresolvedFindings") != []
            or not isinstance(result.get("evidence"), (list, dict)) or not result["evidence"]
            or any(result.get(k) is not False for k in
                   ("formalSaveApproved", "publicationApproved", "filesEdited", "ledgerWritten", "FirestoreWritten"))):
        raise ValueError("approved content-only exact-scope review required")
    # A later correction, rejection or unfinished replacement final invalidates
    # this approval, even if a caller keeps pointing at the earlier good event.
    latest = None
    for number, line in enumerate(lines, 1):
        event = json.loads(line)
        p = event.get("payload", {})
        if (event.get("type") != "response_item" or p.get("type") != "message"
                or p.get("role") != "assistant" or p.get("phase") != "final_answer"):
            continue
        try:
            candidate = _verdict(native._text(p))
        except (ValueError, TypeError):
            continue
        if (candidate.get("scopeHash") == scope["scopeHash"]
                or isinstance(candidate.get("scope"), dict)
                and candidate["scope"].get("scopeHash") == scope["scopeHash"]):
            latest = number
    if latest != audit["reviewerFinal"]["eventLine"]:
        raise ValueError("native review was replaced, rejected or revoked")
    current = session.stat()
    if (signature.st_ino, signature.st_size, signature.st_mtime_ns) != (current.st_ino, current.st_size, current.st_mtime_ns):
        raise ValueError("native session changed during validation")
    return {"reviewerSessionId": reviewer, "reviewerTurnId": turn,
            "nativeEvidenceHash": sha256_json({k: audit[k] for k in ("reviewerFinal", "reviewerComplete")})}


def _derive(connection, root, proposal_ref, audit_ref, creator_session_id):
    root = _physical(root)
    _, scope = _json(root, proposal_ref)
    _, audit = _json(root, audit_ref)
    if (scope.get("schemaVersion") != "feedback-gas-maintenance-content-scope/v1"
            or scope.get("action") != "review_complete_sibling_content_candidate_only"
            or sha256_json({k: v for k, v in scope.items() if k != "scopeHash"}) != scope.get("scopeHash")
            or any(scope.get(k) is not False for k in
                   ("FirestoreWritten", "formalPatchSaved", "formalSaveApproved", "publicationApproved"))):
        raise ValueError("dedicated maintenance content scope required")
    references = [scope["beforeCopy"], scope["candidate"], scope["freshSiblingBaseline"],
                  scope["previousScope"], scope["previousRejectedReview"], *scope["primaryEvidence"]]
    for ref in references:
        _file(root, ref)
    if not scope["primaryEvidence"]:
        raise ValueError("primary maintenance evidence required")
    _, before = _json(root, scope["beforeCopy"])
    _, candidate = _json(root, scope["candidate"])
    _, baseline = _json(root, scope["freshSiblingBaseline"])
    if (before.get("schemaVersion") != "gas-shunin-verified-publication/v1"
            or before.get("qualificationId") not in {"gas-shunin-kou", "gas-shunin-otsu"}
            or not re.fullmatch(r"\d{4}", str(before.get("list_group_id", "")))):
        raise ValueError("supported canonical source identity required")
    canonical = _physical(scope["beforeCanonical"]["ref"])
    expected = root / "output" / before["qualificationId"] / "questions_json" / str(before["list_group_id"]) / "25_verified_publication" / f"{before['list_group_id']}_verified_publication.json"
    if canonical != expected or scope["beforeCanonical"]["sha256"] != scope["beforeCopy"]["sha256"]:
        raise ValueError("canonical root/path/before identity differs")
    rows = before.get("questions")
    if (not isinstance(rows, list) or not rows or before.get("total_count") != len(rows)
            or any(not isinstance(r, dict) or not isinstance(r.get("questionId"), str)
                   or not r["questionId"] or "/" in r["questionId"]
                   or r["questionId"].strip() != r["questionId"]
                   or r.get("qualificationId") != before["qualificationId"]
                   or str(r.get("listGroupId")) != str(before["list_group_id"])
                   or r.get("examYear") != int(before["list_group_id"]) for r in rows)
            or len({r["questionId"] for r in rows}) != len(rows)):
        raise ValueError("canonical IDs/count empty or ambiguous")
    original = scope.get("originalQuestionId")
    siblings = [r for r in rows if r.get("originalQuestionId") == original]
    ids = [r["questionId"] for r in siblings]
    if not isinstance(original, str) or not original or "/" in original or not ids or ids != scope.get("allSiblingIds"):
        raise ValueError("complete sibling identity differs")
    requirement = _requirement(connection, scope["taskId"])
    if (scope.get("taskRequirement") != requirement
            or not {s["question_id"] for s in requirement["requirement"]["sources"]} <= set(ids) | {original}):
        raise ValueError("related improvement task requirement changed or uncovered")
    snapshots = baseline.get("snapshots")
    mask = baseline.get("snapshotFields")
    expected_mask = sorted((set().union(*(set(r) for r in siblings)) | EXPLANATION_FIELDS) -
                           METADATA_FIELDS - {"questionId"})
    if (baseline.get("projectId") != "repaso-rbaqy4" or not isinstance(snapshots, dict)
            or set(snapshots) != set(ids) or not isinstance(mask, list) or not mask
            or mask != expected_mask):
        raise ValueError("complete production sibling baseline required")
    for row in siblings:
        snap = snapshots[row["questionId"]]
        if (snap.get("questionId") != row["questionId"] or snap.get("exists") is not True
                or not isinstance(snap.get("updateTimeExact"), str) or not snap["updateTimeExact"]
                or snap.get("fields") != {k: v for k, v in row.items() if k in mask}):
            raise ValueError("baseline/canonical field or version identity differs")
    projected = deepcopy(before)
    by_id = {r["questionId"]: r for r in projected["questions"]}
    seen, changed_fields = set(), {}
    for update in scope.get("proposedUpdates", []):
        identity = update.get("questionId")
        diffs = update.get("fieldDiffs")
        if (identity not in ids or identity in seen or not isinstance(diffs, list) or not diffs
                or update.get("updateTimeExact") != snapshots[identity]["updateTimeExact"]):
            raise ValueError("maintenance update scope/version differs")
        seen.add(identity)
        fields = set()
        for delta in diffs:
            field = delta.get("field")
            row = by_id[identity]
            if (field not in FIELDS or field in fields or type(delta.get("beforePresent")) is not bool
                    or delta.get("afterPresent", True) is not True or "before" not in delta or "after" not in delta
                    or (field in row) != delta["beforePresent"] or row.get(field) != delta["before"]
                    or delta["beforePresent"] and delta["before"] == delta["after"]):
                raise ValueError("unsupported, duplicate, no-op or drifting field delta")
            fields.add(field)
            row[field] = deepcopy(delta["after"])
        changed_fields[identity] = sorted(fields)
    documents = [{"questionId": identity, "writeFields": changed_fields.get(identity, [])} for identity in ids]
    if not seen or scope.get("publicationDocuments") != documents or candidate != projected:
        raise ValueError("candidate does not exactly replay all approved deltas")
    invariants = scope.get("invariants", {})
    if (any(invariants.get(k) is not True for k in
            ("canonicalUnchanged", "protectedOriginalFieldsAndVerdictsPreserved", "rootMetadataPreserved", "stableIdsAndOrderPreserved"))
            or invariants.get("unaffectedRecordCount") != len(rows) - len(seen)):
        raise ValueError("maintenance invariants differ")
    reviewed = _native_review(audit, scope, proposal_ref, creator_session_id)
    for ref in [proposal_ref, audit_ref, *references]:
        _file(root, ref)
    if _requirement(connection, scope["taskId"]) != requirement:
        raise ValueError("related improvement requirement changed during validation")
    key = {"origin": "independent_audit", "canonicalPath": str(canonical.relative_to(root)),
           "originalQuestionId": original, "relatedImprovementTaskId": scope["taskId"]}
    return {"schemaVersion": SCHEMA, "taskId": sha256_json(key), "status": "open", **key,
            "root": str(root), "proposalRef": dict(proposal_ref), "auditRef": dict(audit_ref),
            "scopeHash": scope["scopeHash"], "beforeSha256": scope["beforeCopy"]["sha256"],
            "candidateSha256": scope["candidate"]["sha256"], "allSiblingIds": ids,
            "publicationDocuments": documents, "diffHash": sha256_json(scope["proposedUpdates"]),
            "taskRequirementHash": requirement["requirementHash"], "creatorSessionId": creator_session_id, **reviewed}


def _tables(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS maintenance_tasks (
        task_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN
        ('open','review_pending','blocked','formally_saved','published_unconfirmed','completed')),
        root TEXT NOT NULL, proposal_ref TEXT NOT NULL, proposal_sha256 TEXT NOT NULL,
        audit_ref TEXT NOT NULL, audit_sha256 TEXT NOT NULL, creator_session_id TEXT NOT NULL,
        binding_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS maintenance_task_history (
        id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES maintenance_tasks(task_id),
        status TEXT NOT NULL CHECK(status='open'), binding_hash TEXT NOT NULL,
        recorded_at TEXT NOT NULL, event_hash TEXT NOT NULL)""")


def _row(connection, task_id):
    cursor = connection.execute("SELECT * FROM maintenance_tasks WHERE task_id=?", (task_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("maintenance task not found")
    return dict(zip([d[0] for d in cursor.description], row))


def load_binding(connection, *, root, task_id):
    row = _row(connection, task_id)
    binding = _derive(connection, root,
        {"ref": row["proposal_ref"], "sha256": row["proposal_sha256"]},
        {"ref": row["audit_ref"], "sha256": row["audit_sha256"]}, row["creator_session_id"])
    if (row["root"] != str(_physical(root)) or row["task_id"] != binding["taskId"]
            or row["status"] != "open" or row["binding_hash"] != sha256_json(binding)):
        raise ValueError("maintenance registry binding/status differs; publication completion unsupported")
    history = connection.execute("SELECT status,binding_hash,recorded_at,event_hash FROM maintenance_task_history WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    expected = {"taskId": task_id, "status": "open", "bindingHash": row["binding_hash"], "recordedAt": row["created_at"]}
    if len(history) != 1 or tuple(history[0]) != ("open", row["binding_hash"], row["created_at"], sha256_json(expected)):
        raise ValueError("maintenance history missing or conflicting")
    return binding


def register(connection, *, root, proposal_ref, audit_ref, creator_session_id, failure_hook=None):
    """Atomic open-only registration; identical resend is read-only/idempotent.

    A changed proposal, audit, purpose or creator requires a separately designed
    revision contract. It never overwrites an already registered maintenance task.
    """
    if connection.in_transaction:
        raise ValueError("independent maintenance transaction required")
    expected = _derive(connection, root, proposal_ref, audit_ref, creator_session_id)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _tables(connection)
        current = _derive(connection, root, proposal_ref, audit_ref, creator_session_id)
        if current != expected:
            raise ValueError("maintenance inputs changed during registration")
        existing = connection.execute("SELECT task_id FROM maintenance_tasks WHERE task_id=?", (current["taskId"],)).fetchone()
        if existing:
            registered = load_binding(connection, root=root, task_id=current["taskId"])
            if registered != current:
                raise ValueError("maintenance registration conflict")
            connection.rollback()
            return registered
        now = datetime.now(timezone.utc).isoformat()
        digest = sha256_json(current)
        connection.execute("INSERT INTO maintenance_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
            (current["taskId"], "open", current["root"], proposal_ref["ref"], proposal_ref["sha256"],
             audit_ref["ref"], audit_ref["sha256"], creator_session_id, digest, now))
        if failure_hook:
            failure_hook("after_task_insert")
        event = {"taskId": current["taskId"], "status": "open", "bindingHash": digest, "recordedAt": now}
        connection.execute("INSERT INTO maintenance_task_history (task_id,status,binding_hash,recorded_at,event_hash) VALUES (?,?,?,?,?)",
            (current["taskId"], "open", digest, now, sha256_json(event)))
        if _derive(connection, root, proposal_ref, audit_ref, creator_session_id) != current:
            raise ValueError("maintenance evidence changed before commit")
        load_binding(connection, root=root, task_id=current["taskId"])
        connection.commit()
        return current
    except BaseException:
        connection.rollback()
        raise


def main(argv=None):
    from tools.question_bank.feedback_daily import DEFAULT_DB, _backup_db
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("register")
    create.add_argument("--proposal", type=Path, required=True)
    create.add_argument("--proposal-sha256", required=True)
    create.add_argument("--audit", type=Path, required=True)
    create.add_argument("--audit-sha256", required=True)
    create.add_argument("--creator-session-id", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    root = _physical(args.repo_root.expanduser())
    db = _physical(args.db.expanduser())
    # This is an extension of an existing intake ledger, never a separate new DB.
    mode = "rw" if args.command == "register" else "ro"
    connection = sqlite3.connect(db.as_uri() + "?mode=" + mode, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if args.command == "register":
            os.chmod(db.parent, 0o700)
            os.chmod(db, 0o600)
            _backup_db(connection, db)
            binding = register(connection, root=root,
                proposal_ref={"ref": str(_physical(args.proposal.expanduser())), "sha256": args.proposal_sha256},
                audit_ref={"ref": str(_physical(args.audit.expanduser())), "sha256": args.audit_sha256},
                creator_session_id=args.creator_session_id)
        else:
            binding = load_binding(connection, root=root, task_id=args.task_id)
        print(json.dumps(binding, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
