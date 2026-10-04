"""Read-only Firestore intake reconciliation and private local feedback ledger.

The ledger contains source paths, hashes and decisions, never user comments or
AI question text. A daily scan is idempotent and does not write to Firestore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.question_bank.question_issue_report_store import SUBMISSION_PATH_RE


DEFAULT_DB = (
    Path.home() / "Library" / "Application Support" / "Repaso"
    / "feedback-ops" / "ledger.sqlite3"
)
AI_MEMO_PATH_RE = re.compile(r"^memos/[^/]+(?:/replies/[^/]+)?$")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"https?://\S+", re.I)
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
DECISIONS = {"fix_required", "no_change", "hold", "app_issue"}
TERMINAL_CASE_STATUSES = {
    "published", "reviewed_no_change", "reviewed_hold", "app_update_queued"
}
AMENDMENT_FIELDS = frozenset({'lawReferences', 'explanationText', 'lawRevisionFacts', 'suggestedQuestionDetailsByChoice'})
AMENDMENT_STATES = {'pending', 'draft', 'system_blocked', 'stale', 'superseded'}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def _private_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    connection = sqlite3.connect(path)
    os.chmod(path, 0o600)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS report_tasks (
          task_id TEXT PRIMARY KEY,
          source_path TEXT NOT NULL UNIQUE,
          report_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          received_at TEXT NOT NULL,
          categories_json TEXT NOT NULL,
          case_ids_json TEXT NOT NULL,
          case_statuses_json TEXT NOT NULL,
          intake_status TEXT NOT NULL,
          decision TEXT,
          decision_reason TEXT,
          evidence_ref TEXT,
          decided_at TEXT,
          proposal_hash TEXT,
          proposal_ref TEXT,
          patch_ref TEXT,
          commit_sha TEXT,
          published_at TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_question_candidates (
          candidate_id TEXT PRIMARY KEY,
          source_path TEXT NOT NULL UNIQUE,
          question_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          text_hash TEXT NOT NULL,
          visibility TEXT NOT NULL,
          review_status TEXT NOT NULL DEFAULT 'unreviewed',
          review_reason TEXT,
          reviewed_at TEXT,
          improvement_task_id TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS improvement_tasks (
          task_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          need TEXT NOT NULL,
          target TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decision_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL,
          case_id TEXT NOT NULL DEFAULT '',
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_ref TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_case_decisions (
          task_id TEXT NOT NULL,
          case_id TEXT NOT NULL,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_ref TEXT NOT NULL,
          decided_at TEXT NOT NULL,
          PRIMARY KEY (task_id, case_id)
        );
        CREATE TABLE IF NOT EXISTS scans (
          scan_id TEXT PRIMARY KEY,
          completed_at TEXT NOT NULL,
          source TEXT NOT NULL,
          report_count INTEGER NOT NULL,
          ai_candidate_count INTEGER NOT NULL,
          intake_gap_count INTEGER NOT NULL
        );
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(decision_history)")
    }
    if "case_id" not in columns:
        connection.execute(
            "ALTER TABLE decision_history ADD COLUMN case_id TEXT NOT NULL DEFAULT ''"
        )
    ai_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(ai_question_candidates)")
    }
    for column in ("review_reason", "reviewed_at"):
        if column not in ai_columns:
            connection.execute(
                f"ALTER TABLE ai_question_candidates ADD COLUMN {column} TEXT"
            )
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS report_task_cases (
          task_id TEXT NOT NULL REFERENCES report_tasks(task_id),
          case_id TEXT NOT NULL, PRIMARY KEY(task_id, case_id));
        CREATE TABLE IF NOT EXISTS proposal_amendments (
          amendment_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, case_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK(revision>0), request_id TEXT NOT NULL,
          request_hash TEXT NOT NULL, base_patch_ref TEXT NOT NULL, base_commit_sha TEXT NOT NULL,
          base_patch_hash TEXT NOT NULL, proposal_hash TEXT NOT NULL, proposal_hash_kind TEXT NOT NULL,
          proposal_ref TEXT NOT NULL, binding_ref TEXT NOT NULL, binding_hash TEXT NOT NULL,
          binding_hash_kind TEXT NOT NULL, binding_json TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('pending','draft','system_blocked','stale','superseded')),
          answer_status TEXT NOT NULL CHECK(answer_status IN ('not_requested','already_asked')),
          asked_at TEXT, blockers_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(task_id,case_id) REFERENCES report_task_cases(task_id,case_id),
          UNIQUE(task_id,case_id,revision), UNIQUE(task_id,case_id,request_id));
        CREATE TABLE IF NOT EXISTS amendment_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, amendment_id TEXT NOT NULL REFERENCES proposal_amendments(amendment_id),
          event TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL,
          reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
    """)
    for task in connection.execute('SELECT task_id,case_ids_json FROM report_tasks').fetchall():
        connection.executemany('INSERT OR IGNORE INTO report_task_cases VALUES (?,?)',
            [(task['task_id'], case) for case in json.loads(task['case_ids_json'])])
    connection.commit()
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def _backup_db(connection: sqlite3.Connection, path: Path) -> None:
    backup_path = path.with_suffix(".backup.sqlite3")
    temporary_path = path.with_suffix(".backup.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    backup = sqlite3.connect(temporary_path)
    try:
        connection.backup(backup)
    finally:
        backup.close()
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(backup_path)


def _live_snapshot(credentials_json: Path | None, project_id: str | None,
                   *, include_ai: bool = True) -> dict[str, Any]:
    from tools.question_bank.question_issue_report_store import FirestoreReportStore

    store = FirestoreReportStore(
        credentials_json=credentials_json, project_id=project_id
    )
    db = store._db
    # Intake reconciliation must also see legacy/incomplete cases. The review
    # workflow's strict case validator would stop the entire daily scan when
    # one old case lacks a review-only field such as listGroupId.
    cases = [
        {"id": doc.id, "workflowStatus": str((doc.to_dict() or {}).get("workflowStatus") or "")}
        for doc in db.collection("questionIssueReportCases").stream()
    ]
    case_reports: dict[str, list[dict[str, str]]] = {}
    for case in cases:
        case_reports[case["id"]] = [
            {"sourceSubmissionPath": str((doc.to_dict() or {}).get("sourceSubmissionPath") or "")}
            for doc in db.collection("questionIssueReportCases")
            .document(case["id"]).collection("reports").stream()
        ]
    receipts = [
        {"id": doc.id, **(doc.to_dict() or {})}
        for doc in db.collection("questionIssueReportReceipts").stream()
    ]
    submissions = []
    for doc in db.collection_group("questionIssueReportSubmissions").stream():
        data = doc.to_dict() or {}
        submissions.append({
            "sourceSubmissionPath": doc.reference.path,
            "reportId": str(data.get("reportId") or doc.id),
            "questionId": str(data.get("questionId") or ""),
            "categories": list(data.get("categories") or []),
            "receivedAt": _timestamp(data.get("createdAt") or data.get("clientEnqueuedAt")),
        })
    result = {
        "cases": cases, "caseReports": case_reports,
        "receipts": receipts, "submissions": submissions,
    }
    if not include_ai:
        return result
    from google.cloud.firestore_v1.base_query import FieldFilter

    ai_questions = []
    eligible_memos: dict[str, tuple[str, str]] = {}
    query = db.collection("memos").where(
        filter=FieldFilter("memoType", "==", "ai_explanation")
    )
    for memo in query.stream():
        data = memo.to_dict() or {}
        if data.get("isDeleted") is True or data.get("visibility") != "public":
            continue
        question_id = str(data.get("questionId") or "")
        if not question_id:
            continue
        eligible_memos[memo.reference.path] = (
            question_id, str(data.get("createdById") or "")
        )
        if isinstance(data.get("content"), str) and data["content"].strip():
            ai_questions.append(_ai_entry(
                memo.reference.path, question_id, data["content"],
                data.get("createdAt"), "public"
            ))
    # One collection-group stream avoids one remote request per memo. Only
    # user-authored replies under eligible public AI memos become candidates.
    for reply in db.collection_group("replies").stream():
        parent_path = reply.reference.parent.parent.path
        eligible = eligible_memos.get(parent_path)
        if eligible is None:
            continue
        question_id, owner_id = eligible
        reply_data = reply.to_dict() or {}
        if (reply_data.get("isDeleted") is True
                or reply_data.get("isAIGenerated") is True
                or str(reply_data.get("createdById") or "") != owner_id
                or not isinstance(reply_data.get("content"), str)
                or not reply_data["content"].strip()):
            continue
        ai_questions.append(_ai_entry(
            reply.reference.path, question_id, reply_data["content"],
            reply_data.get("createdAt"), "public"
        ))
    result["aiQuestions"] = ai_questions
    return result


def _ai_entry(path: str, question_id: str, content: str,
              created_at: Any, visibility: str) -> dict[str, str]:
    return {
        "sourcePath": path,
        "questionId": question_id,
        "createdAt": _timestamp(created_at),
        "textHash": _key(content),
        "visibility": visibility,
    }


def reconcile(connection: sqlite3.Connection, snapshot: Mapping[str, Any],
              *, source: str) -> dict[str, Any]:
    """Mirror all intake metadata without changing operator decisions."""
    now = _now()
    cases_by_path: dict[str, list[dict[str, str]]] = defaultdict(list)
    for case in snapshot.get("cases") or []:
        case_id = str(case.get("id") or "")
        status = str(case.get("workflowStatus") or "")
        for report in (snapshot.get("caseReports") or {}).get(case_id) or []:
            path = str(report.get("sourceSubmissionPath") or "")
            if SUBMISSION_PATH_RE.fullmatch(path):
                cases_by_path[path].append({"caseId": case_id, "status": status})
    receipts = {
        str(item.get("sourceSubmissionPath") or ""): item
        for item in snapshot.get("receipts") or []
        if SUBMISSION_PATH_RE.fullmatch(str(item.get("sourceSubmissionPath") or ""))
    }
    submissions = {}
    for item in snapshot.get("submissions") or []:
        path = str(item.get("sourceSubmissionPath") or "")
        if not SUBMISSION_PATH_RE.fullmatch(path):
            raise ValueError("invalid report submission path in intake snapshot")
        if path in submissions:
            raise ValueError("duplicate report submission path in intake snapshot")
        submissions[path] = item
    stored_paths = {
        row[0] for row in connection.execute("SELECT source_path FROM report_tasks")
    }
    previously_known_ai_paths = {
        row[0] for row in connection.execute(
            "SELECT source_path FROM ai_question_candidates"
        )
    }
    paths = set(submissions) | set(cases_by_path) | set(receipts) | stored_paths
    gaps = []
    with connection:
        for path in sorted(paths):
            item = submissions.get(path) or {}
            linked = sorted(cases_by_path.get(path) or [], key=lambda value: value["caseId"])
            previous = connection.execute(
                "SELECT case_statuses_json,case_ids_json,question_id,categories_json FROM report_tasks WHERE source_path=?", (path,)
            ).fetchone()
            previous_statuses = (
                json.loads(previous["case_statuses_json"]) if previous else {}
            )
            if previous and (previous['question_id'] != str(item.get('questionId') or '')
                    or set(json.loads(previous['case_ids_json'])) != {value['caseId'] for value in linked}
                    or json.loads(previous['categories_json']) != sorted(set(item.get('categories') or []))):
                _invalidate_amendments(connection, _key(path), 'report_input_or_case_binding_changed')
            receipt = receipts.get(path)
            if item and receipt and (linked or receipt.get("status") == "rejected"):
                intake_status = "rejected" if receipt.get("status") == "rejected" else "complete"
            else:
                intake_status = "intake_gap"
                gaps.append(_key(path))
            connection.execute(
                """INSERT INTO report_tasks (
                  task_id, source_path, report_id, question_id, received_at,
                  categories_json, case_ids_json, case_statuses_json, intake_status,
                  first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  report_id=excluded.report_id, question_id=excluded.question_id,
                  received_at=excluded.received_at,
                  categories_json=excluded.categories_json,
                  case_ids_json=excluded.case_ids_json,
                  case_statuses_json=excluded.case_statuses_json,
                  intake_status=excluded.intake_status,
                  last_seen_at=excluded.last_seen_at""",
                (
                    _key(path), path, str(item.get("reportId") or path.rsplit("/", 1)[-1]),
                    str(item.get("questionId") or ""),
                    str(item.get("receivedAt") or ""),
                    json.dumps(sorted(set(item.get("categories") or []))),
                    json.dumps([value["caseId"] for value in linked]),
                    json.dumps({value["caseId"]: value["status"] for value in linked}),
                    intake_status, now, now,
                ),
            )
            linked_ids = {value["caseId"] for value in linked}
            for value in linked:
                prior_status = previous_statuses.get(value["caseId"])
                if (prior_status in TERMINAL_CASE_STATUSES
                        and value["status"] == "unreviewed"):
                    _invalidate_amendments(connection, _key(path), 'case_reopened')
                    connection.execute(
                        "DELETE FROM report_case_decisions WHERE task_id=? AND case_id=?",
                        (_key(path), value["caseId"]),
                    )
            decided_ids = {
                row[0] for row in connection.execute(
                    "SELECT case_id FROM report_case_decisions WHERE task_id=?",
                    (_key(path),),
                )
            }
            if not linked_ids or decided_ids != linked_ids:
                connection.execute(
                    """UPDATE report_tasks SET decision=NULL,
                       decision_reason=NULL, evidence_ref=NULL, decided_at=NULL,
                       proposal_hash=NULL, proposal_ref=NULL
                       WHERE task_id=? AND patch_ref IS NULL""",
                    (_key(path),),
                )
        ai_count = 0
        seen_ai_paths: set[str] = set()
        for item in snapshot.get("aiQuestions") or []:
            path = str(item.get("sourcePath") or "")
            if not AI_MEMO_PATH_RE.fullmatch(path):
                raise ValueError("invalid AI question source path in intake snapshot")
            if item.get("visibility") != "public":
                continue
            if path in seen_ai_paths:
                raise ValueError("duplicate AI question path in intake snapshot")
            seen_ai_paths.add(path)
            text_hash = str(item.get("textHash") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", text_hash):
                raise ValueError("AI question text hash must be sha256")
            ai_count += 1
            connection.execute(
                """INSERT INTO ai_question_candidates (
                  candidate_id, source_path, question_id, created_at, text_hash,
                  visibility, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  question_id=excluded.question_id, created_at=excluded.created_at,
                  text_hash=excluded.text_hash,
                  review_status=CASE WHEN text_hash=excluded.text_hash
                    THEN review_status ELSE 'unreviewed' END,
                  review_reason=CASE WHEN text_hash=excluded.text_hash
                    THEN review_reason ELSE NULL END,
                  reviewed_at=CASE WHEN text_hash=excluded.text_hash
                    THEN reviewed_at ELSE NULL END,
                  improvement_task_id=CASE WHEN text_hash=excluded.text_hash
                    THEN improvement_task_id ELSE NULL END,
                  last_seen_at=excluded.last_seen_at""",
                (_key(path), path, str(item.get("questionId") or ""),
                 str(item.get("createdAt") or ""), text_hash, "public", now, now),
            )
        if "aiQuestions" in snapshot:
            for row in connection.execute("SELECT source_path FROM ai_question_candidates"):
                if row[0] not in seen_ai_paths:
                    connection.execute(
                        "DELETE FROM ai_question_candidates WHERE source_path=?", (row[0],)
                    )
        scan_id = _key(f"{source}:{now}")
        connection.execute(
            "INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, now, source, len(paths), ai_count, len(gaps)),
        )
    refresh_amendments(connection)
    summary = daily_summary(connection, scan_id=scan_id, gaps=gaps)
    summary["newReportTasks"] = len(paths - stored_paths)
    summary["newAiQuestionCandidates"] = (
        len(seen_ai_paths - previously_known_ai_paths)
        if "aiQuestions" in snapshot else 0
    )
    return summary


def daily_summary(connection: sqlite3.Connection, *, scan_id: str = "",
                  gaps: list[str] | None = None) -> dict[str, Any]:
    counts = dict(connection.execute(
        "SELECT intake_status, COUNT(*) FROM report_tasks GROUP BY intake_status"
    ).fetchall())
    undecided = connection.execute(
        "SELECT COUNT(*) FROM report_tasks WHERE decision IS NULL AND intake_status='complete'"
    ).fetchone()[0]
    pending_approval = connection.execute(
        "SELECT COUNT(*) FROM report_tasks WHERE proposal_hash IS NOT NULL AND patch_ref IS NULL"
    ).fetchone()[0]
    pending_proposal = connection.execute(
        """SELECT COUNT(*) FROM report_tasks
           WHERE decision IN ('fix_required', 'mixed')
             AND proposal_hash IS NULL AND patch_ref IS NULL"""
    ).fetchone()[0]
    pending_publication = connection.execute(
        "SELECT COUNT(*) FROM report_tasks WHERE patch_ref IS NOT NULL AND published_at IS NULL"
    ).fetchone()[0]
    ai_unreviewed = connection.execute(
        "SELECT COUNT(*) FROM ai_question_candidates WHERE review_status='unreviewed'"
    ).fetchone()[0]
    improvement_open = connection.execute(
        "SELECT COUNT(*) FROM improvement_tasks WHERE status='open'"
    ).fetchone()[0]
    amendments = dict(connection.execute("SELECT state,COUNT(*) FROM proposal_amendments WHERE state!='superseded' GROUP BY state").fetchall())
    answers = connection.execute("SELECT COUNT(*) FROM proposal_amendments WHERE state!='superseded' AND answer_status='already_asked'").fetchone()[0]
    return {
        "schemaVersion": "feedback-daily/v1", "generatedAt": _now(),
        "scanId": scan_id, "reportTasks": sum(counts.values()),
        "intakeGaps": counts.get("intake_gap", 0),
        "intakeGapTaskIds": gaps or [],
        "rejectedIntake": counts.get("rejected", 0),
        "reportsAwaitingDecision": undecided,
        "fixesAwaitingProposal": pending_proposal,
        "patchApprovalsWaiting": pending_approval,
        "patchesAwaitingPublication": pending_publication,
        "aiQuestionsAwaitingReview": ai_unreviewed,
        "improvementTasksOpen": improvement_open,
        "amendmentsByState": amendments,
        "amendmentApprovalsWaiting": amendments.get('pending', 0),
        "amendmentResponsesWaiting": answers,
        "approvalWaitingTotal": pending_approval + amendments.get('pending', 0),
    }


def record_decision(connection: sqlite3.Connection, *, task_id: str,
                    decision: str, reason: str, evidence_ref: str,
                    case_id: str | None = None) -> None:
    if decision not in DECISIONS or not reason.strip() or not evidence_ref.strip():
        raise ValueError("decision, reason and evidence reference are required")
    task = connection.execute(
        "SELECT case_ids_json, intake_status FROM report_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if task is None or task["intake_status"] != "complete":
        raise ValueError("report task is missing or intake is incomplete")
    case_ids = json.loads(task["case_ids_json"])
    if case_id is None and len(case_ids) == 1:
        case_id = case_ids[0]
    if case_id not in case_ids:
        raise ValueError("case ID is required and must belong to this report")
    recorded_at = _now()
    with connection:
        connection.execute(
            """INSERT INTO report_case_decisions
               (task_id, case_id, decision, reason, evidence_ref, decided_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id, case_id) DO UPDATE SET
                 decision=excluded.decision, reason=excluded.reason,
                 evidence_ref=excluded.evidence_ref, decided_at=excluded.decided_at""",
            (task_id, case_id, decision, reason.strip(), evidence_ref.strip(), recorded_at),
        )
        connection.execute(
            """INSERT INTO decision_history
               (task_id, case_id, decision, reason, evidence_ref, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, case_id, decision, reason.strip(), evidence_ref.strip(), recorded_at),
        )
        decisions = connection.execute(
            "SELECT decision, reason, evidence_ref FROM report_case_decisions WHERE task_id=?",
            (task_id,),
        ).fetchall()
        if len(decisions) == len(case_ids):
            outcomes = {row["decision"] for row in decisions}
            overall = next(iter(outcomes)) if len(outcomes) == 1 else "mixed"
            connection.execute(
                """UPDATE report_tasks SET decision=?, decision_reason=?,
                   evidence_ref=?, decided_at=? WHERE task_id=?""",
                (
                    overall,
                    decisions[0]["reason"] if len(decisions) == 1 else "case decisions differ",
                    decisions[0]["evidence_ref"] if len(decisions) == 1 else "report_case_decisions",
                    recorded_at, task_id,
                ),
            )


def _artifact(ref: str, digest: str, kind: str) -> Any:
    if kind not in {'file_bytes_sha256', 'canonical_json_sha256'} or not re.fullmatch(r'[0-9a-f]{64}', str(digest)):
        raise ValueError('explicit SHA256 serialization contract required')
    path = Path(ref).expanduser()
    if not path.is_file() or path.is_symlink():
        raise ValueError('fixed artifact is missing or not a regular file')
    data = path.read_bytes()
    if kind == 'canonical_json_sha256':
        data = json.dumps(json.loads(data), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('fixed artifact hash differs')
    return json.loads(path.read_bytes()) if path.suffix == '.json' else None


def _amendment_gate(binding: Mapping[str, Any]) -> list[str]:
    proposal = binding['proposal']
    content = _artifact(proposal['ref'], proposal['hash'], proposal['hashKind'])
    scope = [change['field'] for change in content.get('changes', [])]
    if len(scope) != 4 or set(scope) != AMENDMENT_FIELDS or set(binding.get('fieldScope', [])) != AMENDMENT_FIELDS:
        raise ValueError('amendment must bind exactly the four additional fields')
    blockers = []
    inputs = binding.get('inputs') or []
    policy = binding.get('policy')
    evidence = binding.get('evidence') or []
    if not inputs: blockers.append('input_binding_missing')
    if not policy: blockers.append('policy_code_revalidation_missing')
    if not evidence: blockers.append('primary_evidence_missing')
    references = [*inputs, *evidence, *([policy] if policy else [])]
    for reference in references:
        _artifact(reference['ref'], reference['hash'], reference['hashKind'])
    if not any(str(item['ref']).endswith('.py') for item in inputs):
        blockers.append('current_code_binding_missing')
    plan = binding.get('formalPlan') or {}
    paths = plan.get('fieldStages') or {}
    if set(paths) != AMENDMENT_FIELDS or plan.get('recordType') != 'source-bound-field-amendment/v1':
        blockers.append('formal_path_type_unresolved')
    else:
        for field, entry in paths.items():
            path = Path(entry.get('patchRef', ''))
            stage = '18' if field == 'lawReferences' else '21'
            if not entry.get('patchRef') or path.is_absolute() or '..' in path.parts or path.suffix != '.json' or entry.get('stage') != stage:
                blockers.append('formal_path_type_unresolved');break
    source = plan.get('sourceBinding')
    ids = plan.get('publicationIds') or []
    if (not isinstance(source, dict) or source != content.get('sourceIdentity')
            or not all(source.get(k) for k in ('sourceQuestionKey', 'reviewQuestionId', 'sourceRecordRef'))
            or not ids or len(ids) != len(set(ids)) or not all(isinstance(v, str) and v for v in ids)
            or binding.get('contextMissing') is not False):
        blockers.append('source_public_context_missing')
    return sorted(set(blockers))


def _amendment_event(connection, row, state, reason):
    now = _now()
    connection.execute('UPDATE proposal_amendments SET state=?,blockers_json=?,updated_at=? WHERE amendment_id=?',
        (state, json.dumps([reason]), now, row['amendment_id']))
    connection.execute('INSERT INTO amendment_history(amendment_id,event,from_state,to_state,reason,recorded_at) VALUES (?,?,?,?,?,?)',
        (row['amendment_id'], 'invalidated', row['state'], state, reason, now))


def _invalidate_amendments(connection, task_id, reason):
    for row in connection.execute("SELECT * FROM proposal_amendments WHERE task_id=? AND state NOT IN ('superseded','stale')", (task_id,)).fetchall():
        _amendment_event(connection, row, 'stale', reason)


def refresh_amendments(connection):
    """Invalidate changed bindings; never infer or restore pending approval."""
    with connection:
        rows = connection.execute("SELECT * FROM proposal_amendments WHERE state NOT IN ('superseded','stale')").fetchall()
        for row in rows:
            parent = connection.execute('SELECT * FROM report_tasks WHERE task_id=?', (row['task_id'],)).fetchone()
            reason = None
            if (parent is None or row['case_id'] not in json.loads(parent['case_ids_json'])
                    or parent['patch_ref'] != row['base_patch_ref'] or parent['commit_sha'] != row['base_commit_sha']):
                reason = 'parent_case_or_base_binding_changed'
            else:
                try:
                    _artifact(row['base_patch_ref'], row['base_patch_hash'], 'file_bytes_sha256')
                    binding = _artifact(row['binding_ref'], row['binding_hash'], row['binding_hash_kind'])
                    blockers = _amendment_gate(binding)
                    if row['state'] == 'pending' and blockers: reason = 'readiness_became_blocked'
                except (ValueError, KeyError, TypeError, OSError):
                    reason = 'fixed_input_or_policy_changed'
            if reason: _amendment_event(connection, row, 'stale', reason)


def register_amendment(connection, *, task_id, case_id, request_id, expected_revision,
                       binding_ref, binding_hash, binding_hash_kind='file_bytes_sha256',
                       state='system_blocked', answer_status='not_requested', asked_at=None):
    if state not in {'pending', 'draft', 'system_blocked'} or answer_status not in {'not_requested','already_asked'} or not request_id:
        raise ValueError('valid amendment state, answer status and request ID required')
    if answer_status == 'already_asked':
        try:
            if datetime.fromisoformat(str(asked_at).replace('Z','+00:00')).tzinfo is None: raise ValueError
        except ValueError: raise ValueError('already_asked requires actual timezone timestamp') from None
    elif asked_at is not None: raise ValueError('not_requested cannot carry asked timestamp')
    binding = _artifact(binding_ref, binding_hash, binding_hash_kind)
    if binding.get('schemaVersion') != 'approval-amendment-binding/v1' or binding.get('taskId') != task_id or binding.get('caseId') != case_id:
        raise ValueError('amendment binding belongs to another task/case')
    blockers = _amendment_gate(binding)
    if state == 'pending' and blockers: raise ValueError('pending readiness is blocked: ' + ','.join(blockers))
    base = binding['base']; proposal = binding['proposal']
    _artifact(base['patchRef'], base['patchHash'], 'file_bytes_sha256')
    request_hash = _key(json.dumps({'bindingHash': binding_hash, 'bindingRef': str(binding_ref),
        'hashKind': binding_hash_kind, 'expectedRevision': expected_revision, 'state': state,
        'answerStatus': answer_status, 'askedAt': asked_at}, sort_keys=True, separators=(',', ':')))
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        parent = connection.execute('SELECT * FROM report_tasks WHERE task_id=?', (task_id,)).fetchone()
        if (parent is None or case_id not in json.loads(parent['case_ids_json']) or parent['intake_status'] != 'complete'
                or parent['patch_ref'] != base['patchRef'] or parent['commit_sha'] != base['commitSha']
                or not parent['patch_ref'] or not parent['commit_sha']):
            raise ValueError('approved base patch/commit and current parent case must match')
        if state == 'pending' and connection.execute("SELECT 1 FROM report_case_decisions WHERE task_id=? AND case_id=? AND decision='fix_required'", (task_id,case_id)).fetchone() is None:
            raise ValueError('pending amendment requires current case-level fix decision')
        prior = connection.execute('SELECT * FROM proposal_amendments WHERE task_id=? AND case_id=? AND request_id=?', (task_id, case_id, request_id)).fetchone()
        if prior:
            if prior['request_hash'] != request_hash: raise ValueError('request replay content conflict')
            return prior['amendment_id']
        latest = connection.execute('SELECT * FROM proposal_amendments WHERE task_id=? AND case_id=? ORDER BY revision DESC LIMIT 1', (task_id, case_id)).fetchone()
        if type(expected_revision) is not int or expected_revision != (latest['revision'] if latest else 0):
            raise ValueError('expected amendment revision conflict')
        revision = expected_revision + 1
        connection.execute('INSERT OR IGNORE INTO report_task_cases VALUES (?,?)', (task_id, case_id))
        amendment_id = _key(f'{task_id}:{case_id}:{revision}:{request_hash}')
        now = _now()
        if latest:
            connection.execute('UPDATE proposal_amendments SET state=?,updated_at=? WHERE amendment_id=?', ('superseded',now,latest['amendment_id']))
            connection.execute('INSERT INTO amendment_history(amendment_id,event,from_state,to_state,reason,recorded_at) VALUES (?,?,?,?,?,?)',
                (latest['amendment_id'],'superseded',latest['state'],'superseded','replaced by explicit next revision',now))
        connection.execute('INSERT INTO proposal_amendments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (amendment_id,task_id,case_id,revision,request_id,request_hash,base['patchRef'],base['commitSha'],base['patchHash'],
             proposal['hash'],proposal['hashKind'],proposal['ref'],str(binding_ref),binding_hash,binding_hash_kind,
             json.dumps(binding,sort_keys=True),state,answer_status,asked_at,json.dumps(blockers),now,now))
        connection.execute('INSERT INTO amendment_history(amendment_id,event,from_state,to_state,reason,recorded_at) VALUES (?,?,?,?,?,?)',
            (amendment_id,'registered',None,state,'independent additional proposal; base approval not inherited',now))
    return amendment_id


def list_amendments(connection, *, limit=100, task_id=None):
    rows = connection.execute("SELECT * FROM proposal_amendments WHERE state!='superseded' AND (? IS NULL OR task_id=?) ORDER BY created_at,amendment_id LIMIT ?", (task_id,task_id,limit)).fetchall()
    return [{'amendmentId':r['amendment_id'],'taskId':r['task_id'],'caseId':r['case_id'],'revision':r['revision'],
        'state':r['state'],'answerStatus':r['answer_status'],'askedAt':r['asked_at'],
        'proposalHash':r['proposal_hash'],'proposalHashKind':r['proposal_hash_kind'],
        'bindingHash':r['binding_hash'],'baseCommitSha':r['base_commit_sha'],'blockers':json.loads(r['blockers_json'])} for r in rows]


def amendment_history(connection, amendment_id):
    return [dict(r) for r in connection.execute('SELECT event,from_state,to_state,reason,recorded_at FROM amendment_history WHERE amendment_id=? ORDER BY id',(amendment_id,))]


def record_proposal(connection: sqlite3.Connection, *, task_id: str,
                    proposal_hash: str, proposal_ref: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", proposal_hash) or not proposal_ref.strip():
        raise ValueError("fixed proposal hash and reference are required")
    has_fix = connection.execute(
        """SELECT 1 FROM report_case_decisions
           WHERE task_id=? AND decision='fix_required' LIMIT 1""",
        (task_id,),
    ).fetchone()
    if has_fix is None:
        raise ValueError("a case-level fix_required decision is required")
    with connection:
        updated = connection.execute(
            """UPDATE report_tasks SET proposal_hash=?, proposal_ref=?
               WHERE task_id=? AND decision IN ('fix_required', 'mixed')
                 AND patch_ref IS NULL""",
            (proposal_hash, proposal_ref.strip(), task_id),
        )
    if updated.rowcount != 1:
        raise ValueError("task must have a fix_required decision before proposal")


def promote_ai_candidate(connection: sqlite3.Connection, *, candidate_id: str,
                         title: str, need: str, target: str,
                         existing_task_id: str | None = None) -> str:
    if not all(value.strip() for value in (title, need, target)):
        raise ValueError("title, need and target are required")
    candidate = connection.execute(
        "SELECT source_path, text_hash FROM ai_question_candidates WHERE candidate_id=?",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise ValueError("AI question candidate not found")
    now = _now()
    task_id = existing_task_id or _key(f"need:{candidate_id}:{candidate['text_hash']}")
    with connection:
        if existing_task_id:
            exists = connection.execute(
                "SELECT 1 FROM improvement_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("existing improvement task not found")
        else:
            connection.execute(
                """INSERT OR IGNORE INTO improvement_tasks
                   (task_id, title, need, target, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, title.strip(), need.strip(), target.strip(), now, now),
            )
        connection.execute(
            """UPDATE ai_question_candidates SET review_status='promoted',
               review_reason=?, reviewed_at=?, improvement_task_id=?
               WHERE candidate_id=?""",
            (need.strip(), now, task_id, candidate_id),
        )
    return task_id


def dismiss_ai_candidate(connection: sqlite3.Connection, *, candidate_id: str,
                         reason: str) -> None:
    if not reason.strip():
        raise ValueError("review reason is required")
    with connection:
        updated = connection.execute(
            """UPDATE ai_question_candidates SET review_status='no_task',
               review_reason=?, reviewed_at=?, improvement_task_id=NULL
               WHERE candidate_id=?""",
            (reason.strip(), _now(), candidate_id),
        )
    if updated.rowcount != 1:
        raise ValueError("AI question candidate not found")


def _redact_user_text(value: str) -> str:
    value = EMAIL_RE.sub("[メールアドレス]", value)
    value = URL_RE.sub("[URL]", value)
    return LONG_NUMBER_RE.sub("[長い数字]", value)


def inspect_ai_candidate(connection: sqlite3.Connection, *, candidate_id: str,
                         credentials_json: Path | None,
                         project_id: str | None) -> dict[str, str]:
    row = connection.execute(
        """SELECT source_path, question_id, text_hash
           FROM ai_question_candidates WHERE candidate_id=?""",
        (candidate_id,),
    ).fetchone()
    if row is None or not AI_MEMO_PATH_RE.fullmatch(row["source_path"]):
        raise ValueError("AI question candidate not found")
    from tools.question_bank.question_issue_report_store import FirestoreReportStore

    store = FirestoreReportStore(
        credentials_json=credentials_json, project_id=project_id
    )
    path = row["source_path"]
    memo_path = "/".join(path.split("/")[:2])
    memo = store._db.document(memo_path).get()
    if not memo.exists:
        raise ValueError("source memo no longer exists")
    memo_data = memo.to_dict() or {}
    if (memo_data.get("memoType") != "ai_explanation"
            or memo_data.get("visibility") != "public"
            or memo_data.get("isDeleted") is True):
        raise ValueError("source memo is no longer eligible")
    document = store._db.document(path).get()
    if not document.exists:
        raise ValueError("source AI question no longer exists")
    data = document.to_dict() or {}
    if path != memo_path and (
        data.get("isAIGenerated") is True
        or data.get("isDeleted") is True
        or data.get("createdById") != memo_data.get("createdById")
    ):
        raise ValueError("source reply is not user-authored")
    content = str(data.get("content") or "")
    if _key(content) != row["text_hash"]:
        raise ValueError("source question changed since the ledger scan")
    return {
        "candidateId": candidate_id,
        "questionId": row["question_id"],
        "redactedQuestion": _redact_user_text(content),
    }


def list_report_tasks(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT task_id, report_id, question_id, categories_json,
                  case_ids_json, case_statuses_json, intake_status,
                  decision, proposal_hash, received_at
           FROM report_tasks
           WHERE intake_status='intake_gap' OR decision IS NULL
              OR (decision IN ('fix_required', 'mixed') AND patch_ref IS NULL)
              OR EXISTS (SELECT 1 FROM proposal_amendments a WHERE a.task_id=report_tasks.task_id AND a.state!='superseded')
           ORDER BY CASE WHEN intake_status='intake_gap' THEN 0
                         WHEN decision IN ('fix_required', 'mixed') AND proposal_hash IS NULL THEN 1
                         WHEN proposal_hash IS NOT NULL THEN 2 ELSE 3 END,
                    received_at, task_id LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "taskId": row["task_id"], "reportId": row["report_id"],
            "questionId": row["question_id"],
            "categories": json.loads(row["categories_json"]),
            "caseIds": json.loads(row["case_ids_json"]),
            "caseStatuses": json.loads(row["case_statuses_json"]),
            "intakeStatus": row["intake_status"], "decision": row["decision"],
            "proposalHash": row["proposal_hash"],
            "receivedAt": row["received_at"],
            "amendments": list_amendments(connection, task_id=row['task_id']),
        }
        for row in rows
    ]


def list_ai_candidates(connection: sqlite3.Connection, *, limit: int,
                       since: str = "") -> list[dict[str, str]]:
    rows = connection.execute(
        """SELECT candidate_id, question_id, created_at
           FROM ai_question_candidates
           WHERE review_status='unreviewed' AND created_at >= ?
           ORDER BY created_at DESC, candidate_id LIMIT ?""",
        (since, limit),
    ).fetchall()
    return [
        {"candidateId": row["candidate_id"],
         "questionId": row["question_id"], "createdAt": row["created_at"]}
        for row in rows
    ]


def list_improvement_tasks(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, str]]:
    rows = connection.execute(
        """SELECT task_id, title, need, target, status, created_at
           FROM improvement_tasks WHERE status='open'
           ORDER BY created_at, task_id LIMIT ?""", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Read-only intake reconciliation")
    scan.add_argument("--fixture", type=Path)
    scan.add_argument("--credentials-json", type=Path)
    scan.add_argument("--project-id")
    scan.add_argument("--json-output", type=Path)
    scan.add_argument("--reports-only", action="store_true")
    sub.add_parser("summary")
    reports = sub.add_parser("list-reports")
    reports.add_argument("--limit", type=int, default=20)
    ai_list = sub.add_parser("list-ai")
    ai_list.add_argument("--limit", type=int, default=20)
    ai_list.add_argument("--since", default="")
    improvements = sub.add_parser("list-improvements")
    improvements.add_argument("--limit", type=int, default=20)
    inspect = sub.add_parser("inspect-ai")
    inspect.add_argument("candidate_id")
    inspect.add_argument("--credentials-json", type=Path)
    inspect.add_argument("--project-id")
    dismiss = sub.add_parser("dismiss-ai")
    dismiss.add_argument("candidate_id")
    dismiss.add_argument("--reason", required=True)
    decision = sub.add_parser("decide")
    decision.add_argument("task_id")
    decision.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    decision.add_argument("--reason", required=True)
    decision.add_argument("--evidence-ref", required=True)
    decision.add_argument("--case-id")
    proposal = sub.add_parser("propose")
    proposal.add_argument("task_id")
    proposal.add_argument("--proposal-hash", required=True)
    proposal.add_argument("--proposal-ref", required=True)
    amendment = sub.add_parser('amend-register', help='Track a separately bound additional proposal; never approve or publish')
    amendment.add_argument('task_id')
    amendment.add_argument('--case-id', required=True)
    amendment.add_argument('--request-id', required=True)
    amendment.add_argument('--expected-revision', type=int, required=True)
    amendment.add_argument('--binding-ref', required=True)
    amendment.add_argument('--binding-hash', required=True)
    amendment.add_argument('--binding-hash-kind', choices=['file_bytes_sha256','canonical_json_sha256'], default='file_bytes_sha256')
    amendment.add_argument('--state', choices=['pending','draft','system_blocked'], default='system_blocked')
    amendment.add_argument('--answer-status', choices=['not_requested','already_asked'], default='not_requested')
    amendment.add_argument('--asked-at')
    amendment_list = sub.add_parser('amend-list')
    amendment_list.add_argument('--limit', type=int, default=100)
    amendment_list.add_argument('--task-id')
    amendment_history_parser = sub.add_parser('amend-history')
    amendment_history_parser.add_argument('amendment_id')
    sub.add_parser('amend-check', help='Invalidate changed bindings without restoring readiness')
    promote = sub.add_parser("promote-ai")
    promote.add_argument("candidate_id")
    promote.add_argument("--title", required=True)
    promote.add_argument("--need", required=True)
    promote.add_argument("--target", required=True)
    promote.add_argument("--existing-task-id")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = args.db.expanduser().resolve()
    connection = _private_db(db_path)
    changed = args.command in {"scan", "dismiss-ai", "decide", "propose", "promote-ai", 'amend-register', 'amend-check'}
    if args.command == "scan":
        if args.fixture:
            snapshot = json.loads(args.fixture.read_text(encoding="utf-8"))
            source = "fixture"
        else:
            snapshot = _live_snapshot(
                args.credentials_json, args.project_id,
                include_ai=not args.reports_only,
            )
            source = "firestore"
        result = reconcile(connection, snapshot, source=source)
        if args.json_output:
            output = args.json_output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(output, 0o600)
    elif args.command == "summary":
        result = daily_summary(connection)
    elif args.command == "list-reports":
        result = {"reportTasks": list_report_tasks(connection, limit=args.limit)}
    elif args.command == "list-ai":
        result = {"aiQuestionCandidates": list_ai_candidates(
            connection, limit=args.limit, since=args.since,
        )}
    elif args.command == "list-improvements":
        result = {"improvementTasks": list_improvement_tasks(
            connection, limit=args.limit,
        )}
    elif args.command == "inspect-ai":
        result = inspect_ai_candidate(
            connection, candidate_id=args.candidate_id,
            credentials_json=args.credentials_json, project_id=args.project_id,
        )
    elif args.command == "dismiss-ai":
        dismiss_ai_candidate(
            connection, candidate_id=args.candidate_id, reason=args.reason,
        )
        result = daily_summary(connection)
    elif args.command == "decide":
        record_decision(connection, task_id=args.task_id, decision=args.decision,
                        reason=args.reason, evidence_ref=args.evidence_ref,
                        case_id=args.case_id)
        result = daily_summary(connection)
    elif args.command == 'amend-register':
        amendment_id = register_amendment(connection, task_id=args.task_id, case_id=args.case_id,
            request_id=args.request_id, expected_revision=args.expected_revision,
            binding_ref=args.binding_ref, binding_hash=args.binding_hash,
            binding_hash_kind=args.binding_hash_kind, state=args.state,
            answer_status=args.answer_status, asked_at=args.asked_at)
        result = {'amendmentId': amendment_id, 'summary': daily_summary(connection)}
    elif args.command == 'amend-list':
        result = {'amendments': list_amendments(connection, limit=args.limit, task_id=args.task_id)}
    elif args.command == 'amend-history':
        result = {'history': amendment_history(connection, args.amendment_id)}
    elif args.command == 'amend-check':
        refresh_amendments(connection)
        result = daily_summary(connection)
    elif args.command == "propose":
        record_proposal(connection, task_id=args.task_id,
                        proposal_hash=args.proposal_hash,
                        proposal_ref=args.proposal_ref)
        result = daily_summary(connection)
    else:
        task_id = promote_ai_candidate(
            connection, candidate_id=args.candidate_id,
            title=args.title, need=args.need, target=args.target,
            existing_task_id=args.existing_task_id,
        )
        result = daily_summary(connection)
        result["createdImprovementTaskId"] = task_id
    if changed:
        _backup_db(connection, db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("intakeGaps", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
