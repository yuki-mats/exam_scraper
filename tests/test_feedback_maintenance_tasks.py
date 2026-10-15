from copy import deepcopy
import hashlib
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.question_bank import feedback_maintenance_tasks as tasks
from tools.question_bank.feedback_task_requirements import task_requirement_binding


class MaintenanceTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.connection = sqlite3.connect(self.root / "ledger.sqlite3")
        self.connection.row_factory = sqlite3.Row; self.addCleanup(self.connection.close)
        self.connection.executescript("""CREATE TABLE improvement_tasks(task_id,title,need,target);
            CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);
            INSERT INTO improvement_tasks VALUES ('improvement','補足改善','制度を説明','q1');
            INSERT INTO ai_question_candidates VALUES ('source','q1','source-hash','promoted','improvement');""")
        self.private = self.root / tasks.PRIVATE / "run"
        self.private.mkdir(parents=True)
        self.sessions = self.root / "sessions"; self.sessions.mkdir()
        native = patch.object(tasks.native, "NATIVE_SESSION_ROOT", self.sessions)
        native.start(); self.addCleanup(native.stop)
        base = {"originalQuestionId": "original", "qualificationId": "gas-shunin-otsu",
                "listGroupId": "2021", "examYear": 2021, "questionText": "命題",
                "correctChoiceText": "正しい", "explanationText": "正しい。旧説明。"}
        before = {"schemaVersion": "gas-shunin-verified-publication/v1", "qualificationId": "gas-shunin-otsu",
                  "list_group_id": "2021", "total_count": 3,
                  "questions": [{**base, "questionId": "q1"}, {**base, "questionId": "q2"},
                                {**base, "questionId": "outside", "originalQuestionId": "other"}]}
        self.before = before
        self.canonical = self.root / "output/gas-shunin-otsu/questions_json/2021/25_verified_publication/2021_verified_publication.json"
        canonical = self.write(self.canonical, before)
        copy = self.write(self.private / "before.json", before)
        candidate = deepcopy(before); candidate["questions"][0]["explanationText"] = "正しい。制度を具体的に説明。"
        candidate_ref = self.write(self.private / "candidate.json", candidate)
        mask = sorted((set(base) | tasks.EXPLANATION_FIELDS) - tasks.METADATA_FIELDS - {"questionId"})
        snapshots = {r["questionId"]: {"questionId": r["questionId"], "exists": True,
            "updateTimeExact": "2026-10-15T00:00:00.123456789Z", "fields": {k:v for k,v in r.items() if k in mask}}
            for r in before["questions"][:2]}
        baseline = self.write(self.private / "baseline.json", {
            "projectId": "repaso-rbaqy4", "snapshotFields": mask, "snapshots": snapshots})
        self.scope = {"schemaVersion": "feedback-gas-maintenance-content-scope/v1",
            "action": "review_complete_sibling_content_candidate_only", "taskId": "improvement",
            "taskRequirement": task_requirement_binding(self.connection, "improvement"),
            "originalQuestionId": "original", "allSiblingIds": ["q1", "q2"],
            "beforeCanonical": canonical, "beforeCopy": copy, "candidate": candidate_ref,
            "freshSiblingBaseline": baseline,
            "previousScope": self.write(self.private / "previous.json", {"schemaVersion": "fixture-old-scope"}),
            "previousRejectedReview": self.write(self.private / "rejected.json", {"decision": "rejected"}),
            "primaryEvidence": [self.write(self.private / "primary.json", {"locator": "一次資料の位置", "bodyHash": "fixed"})],
            "proposedUpdates": [{"questionId": "q1", "updateTimeExact": snapshots["q1"]["updateTimeExact"],
                "fieldDiffs": [{"field": "explanationText", "beforePresent": True,
                                "before": base["explanationText"], "after": candidate["questions"][0]["explanationText"]}]}],
            "publicationDocuments": [{"questionId": "q1", "writeFields": ["explanationText"]},
                                     {"questionId": "q2", "writeFields": []}],
            "invariants": {"canonicalUnchanged": True, "protectedOriginalFieldsAndVerdictsPreserved": True,
                "rootMetadataPreserved": True, "stableIdsAndOrderPreserved": True, "unaffectedRecordCount": 2},
            "FirestoreWritten": False, "formalPatchSaved": False, "formalSaveApproved": False, "publicationApproved": False}
        self.seal()

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return {"ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def seal(self, *, decision="approved", findings=None):
        self.scope["scopeHash"] = tasks.sha256_json({k:v for k,v in self.scope.items() if k != "scopeHash"})
        self.proposal_ref = self.write(self.private / "scope.json", self.scope)
        result = {"schemaVersion": "independent-content-review/v1", "reviewer": "/root/reviewer",
            "scopeFileSha256": self.proposal_ref["sha256"], "scopeHash": self.scope["scopeHash"],
            "candidateSha256": self.scope["candidate"]["sha256"], "decision": decision,
            "unresolvedFindings": [] if findings is None else findings, "evidence": {"verified": True},
            "formalSaveApproved": False, "publicationApproved": False, "filesEdited": False,
            "ledgerWritten": False, "FirestoreWritten": False}
        self.raw = "```json\n" + json.dumps(result, ensure_ascii=False) + "\n```"
        self.events = [
            {"type": "session_meta", "payload": {"id": "reviewer-session", "parent_thread_id": "creator",
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": "creator", "agent_path": "/root/reviewer"}}}}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "review-turn", "root_turn_id": "root-turn"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                "internal_chat_message_metadata_passthrough": {"turn_id": "review-turn"},
                "content": [{"type": "output_text", "text": self.raw}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "review-turn", "root_turn_id": "root-turn", "last_agent_message": self.raw}}]
        self.session = self.sessions / "review.jsonl"
        lines = [(json.dumps(e, ensure_ascii=False) + "\n").encode() for e in self.events]
        self.session.write_bytes(b"".join(lines))
        def ref(number): return {"sessionPath": str(self.session), "eventLine": number,
            "eventSha256IncludingFinalLf": hashlib.sha256(lines[number - 1]).hexdigest()}
        self.audit = {"schemaVersion": "feedback-native-content-review-record/v1", "rawFinal": self.raw,
            "result": result, "reviewerSessionId": "reviewer-session", "reviewerTurnId": "review-turn",
            "reviewerAgentPath": "/root/reviewer", "reviewerFinal": ref(3), "reviewerComplete": ref(4),
            "formalSaveApproved": False, "publicationApproved": False}
        self.audit_ref = self.write(self.private / "audit.json", self.audit)

    def register(self, **kwargs):
        return tasks.register(self.connection, root=self.root, proposal_ref=self.proposal_ref,
            audit_ref=self.audit_ref, creator_session_id=kwargs.pop("creator_session_id", "creator"), **kwargs)

    def load(self, identity):
        return tasks.load_binding(self.connection, root=self.root, task_id=identity)

    def test_register_load_and_resend_use_same_ledger_without_body_copies(self):
        binding = self.register()
        self.assertEqual(binding["origin"], "independent_audit")
        self.assertEqual(binding["status"], "open")
        self.assertEqual(binding["allSiblingIds"], ["q1", "q2"])
        self.assertEqual(binding, self.register())
        self.assertEqual(binding, self.load(binding["taskId"]))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM maintenance_task_history").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM improvement_tasks").fetchone()[0], 1)
        dump = json.dumps([tuple(r) for r in self.connection.execute("SELECT * FROM maintenance_tasks")], ensure_ascii=False)
        self.assertNotIn("制度を具体的に説明", dump)
        self.assertNotIn("source-hash", dump)

    def test_formal_save_changes_canonical_without_breaking_fixed_maintenance_binding(self):
        binding = self.register()
        self.canonical.write_bytes(Path(self.scope["candidate"]["ref"]).read_bytes())
        self.assertEqual(self.load(binding["taskId"]), binding)

    def test_unchanged_sibling_has_empty_mask_and_absent_optional_fields_stay_in_baseline_mask(self):
        binding = self.register()
        self.assertEqual(binding["publicationDocuments"][1], {"questionId": "q2", "writeFields": []})
        baseline = json.loads(Path(self.scope["freshSiblingBaseline"]["ref"]).read_bytes())
        self.assertIn("suggestedQuestions", baseline["snapshotFields"])
        self.assertNotIn("suggestedQuestions", baseline["snapshots"]["q1"]["fields"])
        self.assertEqual(self.load(binding["taskId"]), binding)
        self.scope["publicationDocuments"][1]["writeFields"] = ["explanationText"]
        self.seal()
        with self.assertRaisesRegex(ValueError, "exactly replay"): self.register()

    def test_rejected_revoked_or_unresolved_native_review_never_registers(self):
        for decision, findings in [("rejected", []), ("revoked", []), ("approved", ["remaining defect"])]:
            with self.subTest(decision=decision, findings=findings):
                self.seal(decision=decision, findings=findings)
                with self.assertRaises(ValueError): self.register()

    def test_latest_native_rejection_invalidates_prior_approval_and_existing_binding(self):
        binding = self.register()
        final = deepcopy(self.events[2]); result = deepcopy(self.audit["result"]); result["decision"] = "rejected"
        final["payload"]["content"][0]["text"] = json.dumps(result)
        with self.session.open("ab") as stream: stream.write((json.dumps(final) + "\n").encode())
        with self.assertRaisesRegex(ValueError, "replaced, rejected or revoked"): self.load(binding["taskId"])
        with self.assertRaises(ValueError): self.register()

    def test_asserted_approval_self_review_and_missing_completion_rejected(self):
        with self.assertRaises(ValueError): self.register(creator_session_id="reviewer-session")
        self.audit["result"]["scopeHash"] = "forged"
        self.audit_ref = self.write(self.private / "audit.json", self.audit)
        with self.assertRaisesRegex(ValueError, "binding differs"): self.register()
        self.seal(); self.session.write_bytes(self.session.read_bytes().splitlines(keepends=True)[0] + b"{}\n")
        with self.assertRaises(ValueError): self.register()

    def test_present_completion_missing_final_memory_footer_rejected(self):
        self.audit["rawFinal"] += "\n<oai-mem-citation>fixture footer</oai-mem-citation>"
        lines = self.session.read_bytes().splitlines(keepends=True)
        event = json.loads(lines[2])
        event["payload"]["content"][0]["text"] = self.audit["rawFinal"]
        lines[2] = (json.dumps(event, ensure_ascii=False) + "\n").encode()
        self.session.write_bytes(b"".join(lines))
        self.audit["reviewerFinal"]["eventSha256IncludingFinalLf"] = hashlib.sha256(lines[2]).hexdigest()
        self.audit_ref = self.write(self.private / "audit.json", self.audit)
        # rawFinal/result/native final bind, but the present completion text differs.
        with self.assertRaisesRegex(ValueError, "native completed final/record binding differs"):
            self.register()

    def test_native_event_bytes_and_scope_material_hash_drift_rejected(self):
        binding = self.register()
        self.session.write_bytes(self.session.read_bytes().replace(b'"review-turn"', b'"other-turn"'))
        with self.assertRaisesRegex(ValueError, "native event bytes"): self.load(binding["taskId"])
        self.seal(); binding = self.load(binding["taskId"])
        Path(self.scope["primaryEvidence"][0]["ref"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "referenced bytes"): self.load(binding["taskId"])

    def test_task_requirement_or_source_links_changed_rejected(self):
        binding = self.register()
        with self.connection: self.connection.execute("UPDATE improvement_tasks SET need='別の目的'")
        with self.assertRaisesRegex(ValueError, "requirement changed"): self.load(binding["taskId"])
        with self.connection:
            self.connection.execute("UPDATE improvement_tasks SET need='制度を説明'")
            self.connection.execute("INSERT INTO ai_question_candidates VALUES ('new','outside','other','promoted','improvement')")
        with self.assertRaises(ValueError): self.load(binding["taskId"])

    def test_new_audit_or_proposal_cannot_silently_replace_existing_registration(self):
        binding = self.register()
        # A genuine later completion for the same scope still changes immutable evidence.
        self.audit["extraReceiptNote"] = "different registration evidence"
        alternate = self.write(self.private / "other-audit.json", self.audit)
        with self.assertRaisesRegex(ValueError, "registration conflict"):
            tasks.register(self.connection, root=self.root, proposal_ref=self.proposal_ref,
                audit_ref=alternate, creator_session_id="creator")
        self.assertEqual(self.load(binding["taskId"]), binding)

    def test_candidate_outside_change_duplicate_id_partial_sibling_and_root_escape_rejected(self):
        for mutation in ("outside", "duplicate", "siblings", "root", "mask"):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.scope)
                if mutation in {"outside", "duplicate"}:
                    value = json.loads(Path(self.scope["candidate"]["ref"]).read_bytes())
                    value["questions"][2]["questionId" if mutation == "duplicate" else "explanationText"] = "q1"
                    self.scope["candidate"] = self.write(self.private / (mutation + ".json"), value)
                elif mutation == "siblings": self.scope["allSiblingIds"] = ["q1"]
                elif mutation == "root": self.scope["beforeCanonical"]["ref"] = str(self.root / "elsewhere.json")
                else:
                    value = json.loads(Path(self.scope["freshSiblingBaseline"]["ref"]).read_bytes())
                    value["snapshotFields"] = ["originalQuestionId"]
                    self.scope["freshSiblingBaseline"] = self.write(self.private / "narrow-mask.json", value)
                self.seal()
                with self.assertRaises(ValueError): self.register()
                self.scope = original; self.seal()

    def test_interrupted_registration_rolls_back_and_resumes_without_duplicate_history(self):
        def fail(stage): raise RuntimeError("interrupted transaction")
        with self.assertRaises(RuntimeError): self.register(failure_hook=fail)
        self.assertFalse(self.connection.in_transaction)
        binding = self.register()
        self.assertEqual(self.load(binding["taskId"]), binding)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM maintenance_task_history").fetchone()[0], 1)

    def test_drift_inside_transaction_rolls_back(self):
        def drift(stage): Path(self.scope["candidate"]["ref"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "referenced bytes"): self.register(failure_hook=drift)
        self.assertFalse(self.connection.in_transaction)
        exists = self.connection.execute("SELECT count(*) FROM sqlite_master WHERE name='maintenance_tasks'").fetchone()[0]
        self.assertEqual(exists, 0)

    def test_caller_transaction_is_preserved_and_completion_cannot_be_asserted(self):
        self.connection.execute("UPDATE improvement_tasks SET title='uncommitted'")
        with self.assertRaisesRegex(ValueError, "independent maintenance"): self.register()
        self.assertTrue(self.connection.in_transaction); self.connection.rollback()
        binding = self.register()
        with self.connection: self.connection.execute("UPDATE maintenance_tasks SET status='completed'")
        with self.assertRaisesRegex(ValueError, "completion unsupported"): self.load(binding["taskId"])

    def test_changed_root_and_symlink_and_missing_history_rejected(self):
        binding = self.register()
        with self.assertRaises(ValueError): tasks.load_binding(self.connection, root=self.root / "other", task_id=binding["taskId"])
        copy = Path(self.scope["beforeCopy"]["ref"]); content = copy.read_bytes(); copy.unlink()
        target = self.private / "moved-before.json"; target.write_bytes(content); copy.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "physical"): self.load(binding["taskId"])
        copy.unlink(); copy.write_bytes(content)
        with self.connection: self.connection.execute("DELETE FROM maintenance_task_history")
        with self.assertRaisesRegex(ValueError, "history"): self.load(binding["taskId"])

    def test_plain_sqlite_connection_row_factory_supported(self):
        self.connection.row_factory = None
        binding = self.register()
        self.assertEqual(self.load(binding["taskId"]), binding)

    def test_requirement_and_artifact_drift_during_native_validation_are_rechecked(self):
        binding = self.register(); original = tasks._native_review
        def race(*args):
            result = original(*args)
            with self.connection: self.connection.execute("UPDATE improvement_tasks SET need='new purpose'")
            return result
        with patch.object(tasks, "_native_review", side_effect=race):
            with self.assertRaisesRegex(ValueError, "changed during validation"):
                self.load(binding["taskId"])
        with self.connection: self.connection.execute("UPDATE improvement_tasks SET need='制度を説明'")
        def artifact_race(*args):
            result = original(*args)
            Path(self.scope["primaryEvidence"][0]["ref"]).write_text("{}")
            return result
        with patch.object(tasks, "_native_review", side_effect=artifact_race):
            with self.assertRaisesRegex(ValueError, "referenced bytes"):
                self.load(binding["taskId"])

    def test_cli_explicit_fixture_db_has_prewrite_backup_and_read_only_inspect(self):
        db = self.root / "ledger.sqlite3"
        common = ["--repo-root", str(self.root), "--db", str(db)]
        args = [*common, "register", "--proposal", self.proposal_ref["ref"],
                "--proposal-sha256", self.proposal_ref["sha256"], "--audit", self.audit_ref["ref"],
                "--audit-sha256", self.audit_ref["sha256"], "--creator-session-id", "creator"]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tasks.main(args), 0)
        binding = json.loads(output.getvalue())
        backup = db.with_suffix(".backup.sqlite3")
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)
        saved = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(saved.execute("SELECT count(*) FROM improvement_tasks").fetchone()[0], 1)
            self.assertEqual(saved.execute("SELECT count(*) FROM sqlite_master WHERE name='maintenance_tasks'").fetchone()[0], 0)
        finally:
            saved.close()
        stamps = (db.stat().st_mtime_ns, backup.stat().st_mtime_ns)
        with contextlib.redirect_stdout(io.StringIO()) as inspected:
            self.assertEqual(tasks.main([*common, "inspect", "--task-id", binding["taskId"]]), 0)
        self.assertEqual(json.loads(inspected.getvalue()), binding)
        self.assertEqual(stamps, (db.stat().st_mtime_ns, backup.stat().st_mtime_ns))
        self.assertNotIn("制度を具体的に説明", inspected.getvalue())

    def test_cli_does_not_create_empty_replacement_ledger(self):
        absent = self.root / "absent.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            tasks.main(["--repo-root", str(self.root), "--db", str(absent), "inspect", "--task-id", "unknown"])
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
