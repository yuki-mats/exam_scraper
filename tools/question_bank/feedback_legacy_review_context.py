"""Freeze read-only aggregate-source / existing legacy-TF review inputs.

This private context is neither a decomposition approval nor a formal-save or
publication adapter. Production prepare always reads fresh masked Firestore data.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from scripts.common.aggregate_answer_decomposition import generate_statement_candidates
from scripts.common.question_identity import SourceIdentityBinding, load_source_record_inventory
from scripts.common.scoped_canonical_context import reader_dependencies
from tools.question_bank.feedback_task_requirements import task_requirement_binding
from tools.question_bank.feedback_verified_save import _snapshots

REQUEST = "feedback-legacy-review-context-request/v1"
SCHEMA = "feedback-legacy-review-context/v1"
REVIEW = "feedback-legacy-review-input/v1"
RECEIPT = "feedback-legacy-review-context-receipt/v1"
PRIVATE = Path("output/user_feedback_response_system/execution")
MANIFEST = Path("docs/contracts/00_source_sha256_manifest.jsonl")
LEDGER = Path.home() / "Library/Application Support/Repaso/feedback-ops/ledger.sqlite3"
READ_FIELDS = frozenset({"correctChoiceText", "examSource", "examYear", "explanationText",
    "isChoiceOnly", "isDeleted", "originalQuestionBodyText", "originalQuestionChoiceText",
    "originalQuestionId", "qualificationId", "questionBodyText", "questionSetId", "questionText", "questionType"})
DIFF_FIELDS = frozenset({"explanationText", "suggestedQuestions", "suggestedQuestionDetails"})
FLAGS = {key: False for key in ("machineReady", "publicationReady", "checkpointRecorded",
    "evaluationPerformed", "formalPatchSaved", "FirestoreWritten", "officialMappingApproved")}
GATES = ["真正に独立した集約分解レビューA/B（候補境界の生成はレビュー完了ではない）",
    "全siblingのsource label・原文span・statement hash・live ID対応の独立mapping監査",
    "真正A/B後のcanonical derived_source_unique_keys materialize正式adapter（未対応）",
    "専用adapterによる正式保存と保存receipt検証", "全現行MAJOR工程と真正checkpoint",
    "出題当時と現行法の法令監査", "真正に独立した評価", "必須checker・quality gate・限定upload dry-runの検査",
    "全sibling read-setと実変更write-setを守る限定公開・CAS・対象内外のlive readback"]


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("unknown or missing fields")
    return value


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("nonempty exact text required")
    return value


def _physical(path):
    path = Path(path).absolute()
    if path != path.resolve() or path.is_symlink():
        raise ValueError("physical path required; symlink/escape rejected")
    return path


def _inside(root, path, *, private=False):
    path = _physical(path)
    if not path.is_relative_to(root / PRIVATE if private else root):
        raise ValueError("path outside permitted repo/private root")
    return path


def _read(root, path, files, *, private=False):
    path = _inside(root, path, private=private)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("regular input file required")
        data = stream.read()
        after = os.fstat(stream.fileno())
    current = path.stat()
    if ((before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_ino, after.st_size, after.st_mtime_ns) or
            (after.st_ino, after.st_size, after.st_mtime_ns) !=
            (current.st_ino, current.st_size, current.st_mtime_ns)):
        raise ValueError("input changed while reading")
    _inside(root, path, private=private)
    digest = _hash(data)
    relative = str(path.relative_to(root))
    if relative in files and files[relative] != digest:
        raise ValueError("input drift")
    files[relative] = digest
    return data


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _ref(root, value, files, *, private=False, decode=True):
    _object(value, {"ref", "sha256"})
    if not re.fullmatch(r"[a-f0-9]{64}", str(value["sha256"])):
        raise ValueError("SHA256 required")
    data = _read(root, value["ref"], files, private=private)
    if _hash(data) != value["sha256"]:
        raise ValueError("fixed input hash differs")
    return _json(data) if decode else data


def _unchanged(root, files):
    for path, digest in files.items():
        if _hash(_read(root, root / path, {})) != digest:
            raise ValueError("fixed input/implementation drift")


def _requirement(connection, task_id):
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return task_requirement_binding(cursor, task_id)
    finally:
        cursor.close()


def _request(root, path, files):
    request = _json(_read(root, path, files, private=True))
    _object(request, {"schemaVersion", "taskId", "source", "officialPdf", "candidate",
        "liveOriginalQuestionId", "liveQualificationId", "statementCandidateId", "mappings"})
    if request["schemaVersion"] != REQUEST:
        raise ValueError("dedicated legacy request schema required")
    for key in ("taskId", "liveOriginalQuestionId", "liveQualificationId", "statementCandidateId"):
        _text(request[key])
    source = request["source"]
    _object(source, {"ref", "sha256", "qualification", "listGroupId", "recordIndex", "binding",
        "canonicalQuestionKey", "sourceQuestionId", "sourceOriginalId"})
    _object(source["binding"], {"sourceQuestionKey", "reviewQuestionId", "sourceRecordRef"})
    for key in ("qualification", "listGroupId"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(source[key])):
            raise ValueError("invalid source directory segment")
    if type(source["recordIndex"]) is not int or source["recordIndex"] < 0:
        raise ValueError("source recordIndex required")
    official = request["officialPdf"]
    _object(official, {"ref", "sha256", "questionPage", "answerPage", "questionNumber", "answerNumber", "answerText"})
    for key in ("questionPage", "answerPage", "questionNumber", "answerNumber"):
        if type(official[key]) is not int or official[key] < 1:
            raise ValueError("one-based official PDF locator required")
    _text(official["answerText"])
    if not isinstance(request["mappings"], list) or not request["mappings"]:
        raise ValueError("explicit full sibling mapping required")
    for mapping in request["mappings"]:
        _object(mapping, {"label", "sourceStatementSha256", "span", "questionId"})
        _object(mapping["span"], {"start", "end"})
        for key in ("label", "questionId"):
            _text(mapping[key])
        if not re.fullmatch(r"[a-f0-9]{64}", str(mapping["sourceStatementSha256"])):
            raise ValueError("exact source statement bytes SHA256 required")
        if any(type(mapping["span"][k]) is not int for k in ("start", "end")):
            raise ValueError("integer source spans required")
    for key in ("label", "questionId"):
        if len({m[key] for m in request["mappings"]}) != len(request["mappings"]):
            raise ValueError("duplicate sibling mapping")
    return request


def _source(root, request, files):
    source = request["source"]
    path = _inside(root, source["ref"])
    expected = root / "output" / source["qualification"] / "questions_json" / source["listGroupId"] / "00_source"
    if path.parent != expected:
        raise ValueError("source outside exact 00_source directory")
    manifest = {}
    for line in _read(root, root / MANIFEST, files).decode("utf-8").splitlines():
        if not line:
            continue
        row = _object(_json(line.encode()), {"path", "sha256"})
        if row["path"] in manifest:
            raise ValueError("duplicate preservation manifest entry")
        manifest[row["path"]] = row["sha256"]
    # Pin the full selected source inventory, including files the canonical reader
    # may exclude by name. The manifest cannot bless an unregistered source.
    payloads = {}
    for item in sorted(expected.glob("*.json")):
        data = _read(root, item, files)
        relative = str(item.relative_to(root))
        if manifest.get(relative) != _hash(data):
            raise ValueError("source preservation manifest hash differs/missing")
        payloads[item] = _json(data)
    if not payloads or source["sha256"] != files.get(str(path.relative_to(root))):
        raise ValueError("source reference differs")
    selected = SourceIdentityBinding.from_mapping(source["binding"])
    if not selected.is_complete() or selected.as_mapping() != source["binding"]:
        raise ValueError("exact complete SourceIdentityBinding required")
    inventory = load_source_record_inventory(expected, qualification=source["qualification"], list_group_id=source["listGroupId"])
    for entry in inventory:
        payload = payloads[entry.path]
        records = payload.get("question_bodies") if isinstance(payload, dict) else payload
        if entry.record != records[entry.record_index]:
            raise ValueError("reader record differs from fixed bytes")
    matches = [entry for entry in inventory if entry.identity.binding == selected]
    if len(matches) != 1:
        raise ValueError("source binding must resolve exactly once")
    entry = matches[0]
    if entry.path != path or entry.record_index != source["recordIndex"]:
        raise ValueError("source binding/path/index disagree")
    record = dict(entry.record)
    for field, key in (("canonical_question_key", "canonicalQuestionKey"), ("source_question_id", "sourceQuestionId"),
                       ("original_question_id", "sourceOriginalId")):
        if record.get(field) != source[key]:
            raise ValueError("source identity differs")
    candidates = generate_statement_candidates(record["questionBodyText"], record["choiceTextList"])
    chosen = [c for c in candidates["candidates"] if c["candidateId"] == request["statementCandidateId"]]
    if len(chosen) != 1:
        raise ValueError("source-owned candidate boundary required")
    mappings = request["mappings"]
    if {tuple(m["span"][k] for k in ("start", "end")) for m in mappings} != {
            (s["start"], s["end"]) for s in chosen[0]["spans"]} or len(mappings) != len(chosen[0]["spans"]):
        raise ValueError("all source spans must be explicitly mapped once")
    for mapping in mappings:
        span = mapping["span"]
        text = record["questionBodyText"][span["start"]:span["end"]]
        if _hash(text.encode("utf-8")) != mapping["sourceStatementSha256"]:
            raise ValueError("source statement span bytes hash differs")
        if not text.startswith(mapping["label"]) or not text[len(mapping["label"]):].startswith((" ", "\u3000", "\t")):
            raise ValueError("source label/span disagree")
    official = request["officialPdf"]
    pdf = _ref(root, {k: official[k] for k in ("ref", "sha256")}, files, private=True, decode=False)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("official PDF bytes required")
    # Locator is fixed review evidence, not machine certification of PDF meaning.
    if record.get("questionLabel") != f"問{official['questionNumber']}":
        raise ValueError("official question locator/source disagree")
    choices = record["choiceTextList"]
    if official["answerNumber"] > len(choices) or choices[official["answerNumber"] - 1] != official["answerText"]:
        raise ValueError("official aggregate selection differs from source choices")
    return record, candidates


def _candidate(root, request, files):
    candidate = _ref(root, request["candidate"], files, private=True)
    if (candidate.get("schemaVersion") != "feedback-improvement-result/v1" or candidate.get("taskId") != request["taskId"]
            or any(candidate.get(k) is not False for k in ("approvalReady", "formalPatchSaved", "FirestoreWritten"))):
        raise ValueError("unapproved same-task private candidate required")
    ids = {m["questionId"] for m in request["mappings"]}
    if len(candidate.get("questionIds", [])) != len(ids) or set(candidate["questionIds"]) != ids:
        raise ValueError("candidate full sibling membership differs")
    binding = candidate.get("sourceBinding", {})
    if (binding.get("canonicalQuestionKey") != request["source"]["canonicalQuestionKey"]
            or binding.get("sourceQuestionId") != request["source"]["sourceQuestionId"]
            or binding.get("sourceOriginalId") != request["source"]["sourceOriginalId"]
            or binding.get("recordIndex") != request["source"]["recordIndex"]
            or binding.get("liveOriginalQuestionId") != request["liveOriginalQuestionId"]
            or binding.get("source") != {k: request["source"][k] for k in ("ref", "sha256")}
            or binding.get("labelToLiveId") != {m["label"]: m["questionId"] for m in request["mappings"]}
            or binding.get("sourceTfFlagsTrusted") is not False
            or binding.get("officialAnswerText") != request["officialPdf"]["answerText"]
            or binding.get("officialOriginalAnswerNumber") != str(request["officialPdf"]["answerNumber"])):
        raise ValueError("candidate contains another source/mapping")
    for evidence in candidate.get("primaryEvidence", []):
        _ref(root, evidence, files, private=True, decode=False)
    updates = candidate.get("proposedUpdates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("candidate field differences required")
    seen = set()
    for update in updates:
        if update.get("questionId") not in ids or update["questionId"] in seen:
            raise ValueError("candidate update outside/duplicate sibling")
        seen.add(update["questionId"])
        baseline = _ref(root, update["baseline"], files, private=True)
        if not isinstance(baseline, list) or {s["questionId"] for s in baseline} != ids or len(baseline) != len(ids):
            raise ValueError("full sibling baseline required")
        diffs = update.get("fieldDiffs")
        if not isinstance(diffs, list) or not diffs:
            raise ValueError("nonempty field diffs required")
        fields = []
        for diff in diffs:
            _object(diff, {"field", "beforePresent", "before", "after"})
            if diff["field"] not in DIFF_FIELDS or diff["beforePresent"] is not True or diff["before"] == diff["after"]:
                raise ValueError("existing changed explanation fields only")
            if diff["field"] == "explanationText" and (not isinstance(diff["after"], str) or not diff["after"].strip()):
                raise ValueError("nonempty explanation required")
            if diff["field"] != "explanationText" and not isinstance(diff["after"], list):
                raise ValueError("suggestion list required")
            fields.append(diff["field"])
        if len(fields) != len(set(fields)):
            raise ValueError("duplicate field diff")
    return candidate


def _build(root, request_path, connection, snapshots, observation):
    files = {}
    request = _request(root, request_path, files)
    record, candidates = _source(root, request, files)
    candidate = _candidate(root, request, files)
    requirement = _requirement(connection, request["taskId"])
    ids = {m["questionId"] for m in request["mappings"]}
    sources = requirement["requirement"]["sources"]
    if not sources or not {s["question_id"] for s in sources}.issubset(ids):
        raise ValueError("task source requirements outside full sibling set")
    if not isinstance(snapshots, dict) or set(snapshots) != ids:
        raise ValueError("fresh complete sibling read-set required")
    mask = sorted(READ_FIELDS | {d["field"] for u in candidate["proposedUpdates"] for d in u["fieldDiffs"]})
    head_end = min(m["span"]["start"] for m in request["mappings"])
    stem = record["questionBodyText"][:head_end].rstrip()
    mappings = []
    for mapping in request["mappings"]:
        qid = mapping["questionId"]
        snapshot = snapshots[qid]
        _object(snapshot, {"questionId", "exists", "updateTimeExact", "fields"})
        values = snapshot["fields"]
        if (snapshot["questionId"] != qid or snapshot["exists"] is not True
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", str(snapshot["updateTimeExact"]))
                or not isinstance(values, dict) or set(values) - set(mask) or not READ_FIELDS.issubset(values)
                or values["originalQuestionId"] != request["liveOriginalQuestionId"]
                or values["qualificationId"] != request["liveQualificationId"]
                or values["examYear"] != record["examYear"] or values["originalQuestionBodyText"] != stem
                or values["isChoiceOnly"] is not False or values["isDeleted"] is not False
                or values["questionType"] != "true_false" or values["correctChoiceText"] not in ("正しい", "間違い")):
            raise ValueError("fresh masked sibling identity/state differs")
        text = record["questionBodyText"][mapping["span"]["start"]:mapping["span"]["end"]]
        body = text[len(mapping["label"]):].lstrip()
        mappings.append({**mapping, "sourceSpanText": text, "sourceStatementText": body,
            "liveOriginalQuestionChoiceText": values["originalQuestionChoiceText"],
            "sourceLiveTextEqual": body == values["originalQuestionChoiceText"],
            "independentMappingReviewRequired": True})
    writes = []
    for update in candidate["proposedUpdates"]:
        baseline = _ref(root, update["baseline"], files, private=True)
        for prior in baseline:
            current = snapshots[prior["questionId"]]
            if prior.get("updateTimeExact") != current["updateTimeExact"] or prior.get("fields") != current["fields"]:
                raise ValueError("fresh baseline third value/field presence/time conflict")
        current = snapshots[update["questionId"]]
        if update.get("updateTimeExact") != current["updateTimeExact"]:
            raise ValueError("candidate exact update time differs")
        for diff in update["fieldDiffs"]:
            if diff["field"] not in current["fields"] or current["fields"][diff["field"]] != diff["before"]:
                raise ValueError("candidate baseline field presence/third value conflict")
        writes.append({"questionId": update["questionId"], "fieldDiffs": update["fieldDiffs"],
            "writeFields": sorted(d["field"] for d in update["fieldDiffs"]), "updateTimeExact": current["updateTimeExact"]})
    initial = [root / path for path in ("tools/question_bank/feedback_legacy_review_context.py",
        "scripts/common/question_identity.py", "scripts/common/aggregate_answer_decomposition.py",
        "scripts/common/scoped_canonical_context.py", "tools/question_bank/feedback_verified_save.py",
        "tools/question_bank/feedback_task_requirements.py")]
    for path in reader_dependencies(root, initial):
        _read(root, path, files)
    # These source keys belong to aggregate answer choices, never statements.
    # Canonical statement keys require the still-missing post-A/B save adapter.
    context = {"schemaVersion": SCHEMA, "request": request, "requestPath": str(request_path.relative_to(root)), "inputFiles": files, "taskRequirement": requirement,
        "sourceRecord": record, "sourceChoiceUniqueKeys": deepcopy(record.get("sourceUniqueKeys")),
        "sourceChoiceUniqueKeysAreStatementKeys": False, "statementCandidates": candidates, "mappings": mappings, "officialPdf": request["officialPdf"],
        "sourceCorrectChoiceVectorTrusted": False, "officialAnswerTransfersToTf": False,
        "independentMappingReviewRequired": True, "observation": observation,
        "readFields": mask, "readSet": [snapshots[qid] for qid in sorted(ids)],
        "writeSet": sorted(writes, key=lambda w: w["questionId"]), "remainingGates": GATES, **FLAGS}
    _unchanged(root, files)
    if _requirement(connection, request["taskId"]) != requirement:
        raise ValueError("task requirement drift")
    return context


def _outputs(context):
    review = {"schemaVersion": REVIEW, "contextSha256": _hash(_bytes(context)),
        "taskRequirement": context["taskRequirement"], "sourceRecord": context["sourceRecord"],
        "sourceChoiceUniqueKeys": context["sourceChoiceUniqueKeys"], "sourceChoiceUniqueKeysAreStatementKeys": False,
        "officialPdf": context["officialPdf"], "statementCandidates": context["statementCandidates"],
        "mappings": context["mappings"], "readSet": context["readSet"], "writeSet": context["writeSet"],
        "remainingGates": GATES, "independentMappingReviewRequired": True, **FLAGS}
    contents = {"context.json": _bytes(context), "review-input.json": _bytes(review)}
    receipt = {"schemaVersion": RECEIPT, "status": "read_only_context_frozen", "taskId": context["request"]["taskId"],
        "outputs": {name: _hash(data) for name, data in contents.items()}, "inputFiles": context["inputFiles"],
        "readSetIds": [s["questionId"] for s in context["readSet"]],
        "writeSetIds": [s["questionId"] for s in context["writeSet"]],
        "remainingGates": GATES, "independentMappingReviewRequired": True, **FLAGS}
    contents["receipt.json"] = _bytes(receipt)
    return contents, receipt


def inspect(root, destination, *, connection):
    root = _physical(root)
    destination = _inside(root, destination, private=True)
    if {p.name for p in destination.iterdir()} != {"context.json", "review-input.json", "receipt.json"}:
        raise ValueError("incomplete/conflicting context outputs")
    stored = {name: _read(root, destination / name, {}, private=True)
        for name in ("context.json", "review-input.json", "receipt.json")}
    context = _json(stored["context.json"])
    # Verify the saved implementation fingerprint before interpreting its request.
    # A context made by an older mapping contract cannot survive a code revision.
    _unchanged(root, context["inputFiles"])
    request_path = _inside(root, root / context["requestPath"], private=True)
    if context["requestPath"] not in context["inputFiles"]:
        raise ValueError("fixed request input required")
    observation = _object(context["observation"], {"startedAt", "finishedAt", "reader"})
    if (observation["reader"] not in ("production-fresh-FirestoreReadback", "injected-test-reader")
            or datetime.fromisoformat(observation["startedAt"].replace("Z", "+00:00")) >
               datetime.fromisoformat(observation["finishedAt"].replace("Z", "+00:00"))):
        raise ValueError("invalid fresh read observation")
    snapshots = {s["questionId"]: s for s in context["readSet"]}
    rebuilt = _build(root, request_path, connection, snapshots, observation)
    expected, receipt = _outputs(rebuilt)
    if expected != stored:
        raise ValueError("fixed context/output/receipt drift")
    if {p.name for p in destination.iterdir()} != set(stored) or any(
            _read(root, destination / name, {}, private=True) != data for name, data in stored.items()):
        raise ValueError("output changed during inspection")
    return receipt


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare(root, request_path, destination, *, connection, _snapshot_reader=None):
    root = _physical(root)
    request_path = _inside(root, request_path, private=True)
    destination = _inside(root, destination, private=True)
    if destination == root / PRIVATE or destination in request_path.parents:
        raise ValueError("dedicated new private output directory required")
    # Validate all local evidence before a production read; no fixture CLI exists.
    files = {}
    request = _request(root, request_path, files)
    _source(root, request, files)
    candidate = _candidate(root, request, files)
    ids = sorted(m["questionId"] for m in request["mappings"])
    fields = sorted(READ_FIELDS | {d["field"] for u in candidate["proposedUpdates"] for d in u["fieldDiffs"]})
    now = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started = now()
    snapshots = deepcopy((_snapshots if _snapshot_reader is None else _snapshot_reader)(ids, fields))
    observation = {"startedAt": started, "finishedAt": now(),
        "reader": "production-fresh-FirestoreReadback" if _snapshot_reader is None else "injected-test-reader"}
    if destination.exists():
        inspect(root, destination, connection=connection)
        old = _json(_read(root, destination / "context.json", {}, private=True))
        if old["request"] != request or old["observation"]["reader"] != observation["reader"]:
            raise ValueError("same request/reader retransmission only")
        observation = old["observation"]
    context = _build(root, request_path, connection, snapshots, observation)
    _unchanged(root, files)
    contents, receipt = _outputs(context)
    if destination.exists():
        for name, data in contents.items():
            if _read(root, destination / name, {}, private=True) != data:
                raise ValueError("retransmission input/live/output differs")
        return inspect(root, destination, connection=connection)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _inside(root, destination, private=True)
    destination.mkdir(mode=0o700)  # Exclusive directory claim: concurrent creators fail.
    try:
        for name, data in contents.items():
            _inside(root, destination, private=True)
            with (destination / name).open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        _sync(destination)
        _sync(destination.parent)
        return inspect(root, destination, connection=connection)
    except BaseException:
        # Keep incomplete artifacts as evidence; a retry cannot fill/overwrite them.
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--db", type=Path, default=LEDGER)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--request", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("inspect")
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    ledger = _physical(args.db)
    with closing(sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        receipt = (prepare(args.repo_root, args.request, args.output, connection=connection)
            if args.command == "prepare" else inspect(args.repo_root, args.output, connection=connection))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
