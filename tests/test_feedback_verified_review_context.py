"""Verified25 context fixtures only: no real ledger, native files, or network."""
from copy import deepcopy
import contextlib
import hashlib
import json
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.question_bank import feedback_verified_review_context as module


class VerifiedReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = sqlite3.connect(":memory:"); self.addCleanup(self.db.close)
        self.private = self.root / module.saver.PRIVATE / "fixture"
        self.private.mkdir(parents=True)
        self.output = self.private / "review"
        self.path = self.private / "formal-save-plan.json"
        self.canonical = self.root / "output/gas-shunin-otsu/questions_json/2021/25_verified_publication/2021_verified_publication.json"
        self.ids = [f"gas-shunin-otsu-2021-law-q02-s{i:02d}" for i in range(1, 6)]
        self.rows = [{"questionId": identity, "originalQuestionId": "original", "qualificationId": "gas-shunin-otsu",
            "listGroupId": "2021", "examYear": 2021, "questionType": "true_false",
            "originalQuestionBodyText": "正しい記述の個数を答える問題。",
            "originalQuestionChoiceText": f"原肢{i}", "questionText": f"公開原肢{i}",
            "correctChoiceText": "正しい", "explanationText": f"正しい。肢{i}の説明。",
            "isDeleted": False, "isChoiceOnly": False, "isLawRelated": True,
            "suggestedQuestions": [f"補足{i}"],
            "suggestedQuestionDetails": [{"question": f"補足{i}", "answer": f"回答{i}"}],
            "lawReferences": [{"article": str(i)}], "lawRevisionFacts": {"differenceFacts": [f"根拠{i}"]},
            "questionImageStorageUrls": [f"body-image-{i}"],
            "originalQuestionChoiceImageUrls": [f"choice-image-{i}"], "questionTags": [f"tag{i}"]}
            for i, identity in enumerate(self.ids, 1)]
        self.bundle = {"schemaVersion": module.saver.gas.SCHEMA_VERSION, "qualificationId": "gas-shunin-otsu",
                       "list_group_id": "2021", "total_count": 5, "questions": self.rows}
        canonical = self.write(self.canonical, self.bundle)
        question_pdf = self.root / "output/pdf/q.pdf"; answer_pdf = self.root / "output/pdf/a.pdf"
        question_pdf.parent.mkdir(parents=True); question_pdf.write_bytes(b"%PDF fixture question")
        answer_pdf.write_bytes(b"%PDF fixture answer")
        pdf = {"path": str(question_pdf.relative_to(self.root)), "sha256": self.digest(question_pdf), "pdfPage": 4}
        answer = {"path": str(answer_pdf.relative_to(self.root)), "sha256": self.digest(answer_pdf), "pdfPage": 1}
        self.index = {"records": [{"questionId": identity, "grade": "otsu", "examYear": 2021,
            "section": "law", "questionNumber": 2, "requiresContentRepair": False,
            "match": {"status": "verified_question_block", "bestBlockIdentity": {
                "grade": "otsu", "examYear": 2021, "section": "law", "questionNumber": 2}},
            "officialQuestionPdf": deepcopy(pdf), "officialAnswerPdf": deepcopy(answer),
            # Deliberately unrelated to canonical/official order: never join by it.
            "choiceIndex": 4 - number} for number, identity in enumerate(self.ids)]}
        self.index_path = self.private / "official-index.json"
        self.plan = {"schemaVersion": module.saver.MAINTENANCE_SCHEMA, "planHash": "fixed-plan",
            "candidateSha256": canonical["sha256"], "formalPath": str(self.canonical.relative_to(self.root)),
            "siblingIds": self.ids, "inputs": {}, "taskRequirement": {"requirementHash": "purpose"},
            "documentIndexRef": self.write(self.index_path, self.index),
            "pdfVerificationRef": self.write(self.private / "pdf-verification.json", {"result": "pass"}),
            "beforeRef": str(self.private / "before.json"), "candidateRef": str(self.private / "candidate.json"),
            "publicationDocuments": [{"questionId": i, "writeFields": ["explanationText"]} for i in self.ids]}
        self.write(Path(self.plan["beforeRef"]), self.bundle)
        self.write(Path(self.plan["candidateRef"]), self.bundle)
        verdict = {"schemaVersion": "independent-scope-review/v1", "scope": {
            "planHash": "fixed-plan", "candidateSha256": canonical["sha256"]},
            "decision": "approved", "unresolvedFindings": [],
            "evidence": ["原問対応はs01=イ、s02=ニ、s03=ハ、s04=ホ、s05=ロ。"]}
        self.verdict = verdict
        self.sessions = self.root / "native"; self.sessions.mkdir()
        self.session = self.sessions / "review.jsonl"
        self.approval = self.native_final(verdict)
        original = self.private / "journal/original.json"
        self.write(original, self.bundle); self.write(original.parent / "intent.json", {"planHash": "fixed-plan"})
        self.receipt = {"schemaVersion": module.saver.MAINTENANCE_RECEIPT, "planHash": "fixed-plan",
            "FirestoreWritten": False, "originalRef": str(original), "approvalEvidence": self.approval,
            "approvalBinding": {"resultHash": module.sha256_json(verdict)}}
        self.write(self.private / "save-receipt.json", self.receipt)
        self.approval_ref = self.write(self.private / "final-approval.json", self.approval)
        order_ids = [self.ids[n] for n in (0, 4, 2, 1, 3)]
        self.mapping = {"schemaVersion": module.ORDER_SCHEMA, "planHash": "fixed-plan",
            "originalQuestionId": "original", "officialQuestionPdf": {
                "ref": str(question_pdf), "sha256": self.digest(question_pdf), "pdfPage": 4},
            "approvalRef": self.approval_ref,
            "statements": [{"questionId": i, "officialLabel": label, "officialOrderIndex": n,
                "choiceTextSha256": hashlib.sha256(next(r["originalQuestionChoiceText"] for r in self.rows if r["questionId"] == i).encode()).hexdigest()}
                for n, (i, label) in enumerate(zip(order_ids, module.LABELS, strict=True))]}
        self.mapping_path = self.private / "official-order.json"; self.write(self.mapping_path, self.mapping)
        self.policies = {"maintenance": {stage: {"id": stage, "automatic": True,
            "policyVersion": "2.0", "policyFingerprint": stage + "-fingerprint"}
            for stage in ("question_type", "question_intent", "correct_choice", "law_context", "explanation", "law_audit", "question_set")},
            "evaluation": {"id": "evaluation", "policyVersion": "5.9", "policyFingerprint": "eval"}}
        self.policies["maintenance"]["originalize"] = {"id": "originalize", "automatic": False,
            "policyVersion": "2.10", "policyFingerprint": "opt-in-only"}
        self.policy_file = self.private / "policy.txt"; self.policy_file.write_text("fixed policy")
        self.policy_inputs = {str(self.policy_file): self.digest(self.policy_file)}
        self.saved = patch.object(module.saver, "load_saved", side_effect=lambda *a, **k: deepcopy(self.receipt))
        self.mock_saved = self.saved.start(); self.addCleanup(self.saved.stop)
        for target, kwargs in [
            ("_current_policies", {"side_effect": lambda *a: deepcopy(self.policies)}),
            ("_policy_inputs", {"side_effect": lambda *a: dict(self.policy_inputs)})]:
            p = patch.object(module, target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(module.native, "NATIVE_SESSION_ROOT", self.sessions)
        p.start(); self.addCleanup(p.stop)
        self.seal_plan()

    @staticmethod
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def write(cls, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(module._bytes(value))
        return {"ref": str(path), "sha256": cls.digest(path)}

    def native_final(self, verdict):
        lines = [module._bytes({"type": "session_meta", "payload": {"id": "fixture-reviewer"}}).replace(b"\n", b""),
                 json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                    "phase": "final_answer", "content": [{"type": "output_text", "text": json.dumps(verdict, ensure_ascii=False)}]}}, ensure_ascii=False).encode()]
        self.session.write_bytes(b"\n".join(lines) + b"\n")
        return {"reviewerFinal": {"sessionPath": str(self.session), "eventLine": 2,
            "eventSha256IncludingFinalLf": hashlib.sha256(lines[1] + b"\n").hexdigest()}}

    def seal_plan(self):
        self.plan["documentIndexRef"] = self.write(self.index_path, self.index)
        self.plan["planHash"] = module.sha256_json({k: v for k, v in self.plan.items() if k != "planHash"})
        self.verdict["scope"]["planHash"] = self.plan["planHash"]
        self.verdict["scope"]["candidateSha256"] = self.plan["candidateSha256"]
        self.approval = self.native_final(self.verdict)
        self.receipt.update(planHash=self.plan["planHash"], candidateSha256=self.plan["candidateSha256"],
                            formalPath=self.plan["formalPath"], approvalEvidence=self.approval,
                            approvalBinding={"resultHash": module.sha256_json(self.verdict)})
        self.write(self.private / "save-receipt.json", self.receipt)
        self.mapping["planHash"] = self.plan["planHash"]
        self.mapping["approvalRef"] = self.write(self.private / "final-approval.json", self.approval)
        self.write(self.mapping_path, self.mapping)
        self.write(self.path, self.plan)

    def prepare(self, **kwargs):
        return module.prepare(self.root, self.path, self.output, connection=self.db, **kwargs)

    def inspect(self): return module.inspect(self.root, self.output, connection=self.db)

    def test_genuine_saved_boundary_and_missing_runner_are_never_success_checkpoints(self):
        receipt = self.prepare()
        self.assertEqual(receipt, self.inspect())
        self.mock_saved.assert_called_with(self.root, self.path, self.private / "save-receipt.json", connection=self.db)
        context = json.loads((self.output / "context.json").read_bytes())
        self.assertFalse(context["preflight"]["machineReady"])
        self.assertFalse(context["preflight"]["publicationReady"])
        self.assertEqual(len(context["preflight"]["missingStages"]), 7)
        self.assertNotIn("originalize", context["preflight"]["requiredStageIds"])
        self.assertEqual(context["preflight"]["nonApplicableStages"],
                         [{"stageId": "originalize", "reason": "official_exam_year_verified"}])
        self.assertEqual(context["preflight"]["applicabilityUnresolvedStageIds"], [])
        self.assertIsNone(context["officialOrder"])
        self.assertIn("official_order_unresolved", context["preflight"]["blockers"])
        self.assertEqual(context["identity"]["kind"], "verified25_publication")
        self.assertNotIn("sourceRecordRef", json.dumps(context))
        self.assertNotIn("sourceQuestionKey", json.dumps(context))

    def test_explicit_native_order_joins_every_field_by_id_not_array_or_choice_index(self):
        self.index["records"].reverse(); self.seal_plan()
        self.prepare(official_order=self.mapping_path)
        review = json.loads((self.output / "review-input.json").read_bytes())
        expected = [self.ids[n] for n in (0, 4, 2, 1, 3)]
        self.assertEqual([v["questionId"] for v in review["choiceBindings"]], expected)
        for n, identity in enumerate(expected):
            row = next(r for r in self.rows if r["questionId"] == identity)
            self.assertEqual(review["documentsById"][identity], row)
            self.assertEqual(review["projected"]["choiceTextList"][n], row["originalQuestionChoiceText"])
            self.assertEqual(review["projected"]["explanationText"][n], row["explanationText"])
            self.assertEqual(review["projected"]["lawReferencesByChoice"][n], row["lawReferences"])
            self.assertEqual(review["projected"]["originalQuestionChoiceImageUrls"][n], row["originalQuestionChoiceImageUrls"])
        self.assertEqual(self.inspect()["publicationReady"], False)

    def test_no_load_saved_assertion_or_test_runner_bypass_in_real_boundary(self):
        self.saved.stop()
        with self.assertRaises((ValueError, KeyError)):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_missing_duplicate_and_reordered_siblings_reject_before_output(self):
        for mode in ("missing", "duplicate", "order"):
            original = deepcopy(self.plan)
            if mode == "missing": self.plan["siblingIds"] += ["absent"]
            elif mode == "duplicate": self.plan["siblingIds"] += [self.ids[0]]
            else: self.plan["siblingIds"] = list(reversed(self.ids))
            self.seal_plan()
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.output.exists()); self.plan = original

    def test_official_index_missing_duplicate_mixed_question_and_pdf_page_rejected(self):
        original = deepcopy(self.index)
        for mode in ("missing", "duplicate", "question", "page"):
            self.index = deepcopy(original)
            if mode == "missing": self.index["records"].pop()
            elif mode == "duplicate": self.index["records"].append(self.index["records"][0])
            elif mode == "question": self.index["records"][0]["questionNumber"] = 3
            else: self.index["records"][0]["officialQuestionPdf"]["pdfPage"] = 5
            self.seal_plan()
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.output.exists())

    def test_official_order_wrong_ids_labels_positions_text_or_approval_rejected(self):
        original = deepcopy(self.mapping)
        for mode in ("identity", "label", "position", "boolean_position", "text", "approval"):
            mapping = deepcopy(original)
            if mode == "identity": mapping["statements"][0]["questionId"] = self.ids[1]
            elif mode == "label": mapping["statements"][0]["officialLabel"] = "ロ"
            elif mode == "position": mapping["statements"].reverse()
            elif mode == "boolean_position": mapping["statements"][0]["officialOrderIndex"] = False
            elif mode == "text": mapping["statements"][0]["choiceTextSha256"] = "0" * 64
            else: mapping["approvalRef"] = self.write(self.private / "fake-approval.json", {})
            self.write(self.mapping_path, mapping)
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.prepare(official_order=self.mapping_path)
            self.assertFalse(self.output.exists())

    def test_native_mapping_absent_ambiguous_or_wrong_candidate_is_not_inferred(self):
        for mode in ("absent", "ambiguous", "candidate"):
            verdict = deepcopy(self.verdict)
            if mode == "absent": verdict["evidence"] = ["5件を確認した"]
            elif mode == "ambiguous": verdict["evidence"].append("s01=ロ")
            else: verdict["scope"]["candidateSha256"] = "0" * 64
            self.approval = self.native_final(verdict)
            self.receipt["approvalEvidence"] = self.approval
            self.receipt["approvalBinding"]["resultHash"] = module.sha256_json(verdict)
            self.write(self.private / "save-receipt.json", self.receipt)
            self.mapping["approvalRef"] = self.write(self.private / "final-approval.json", self.approval)
            self.write(self.mapping_path, self.mapping)
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.prepare(official_order=self.mapping_path)
            self.assertFalse(self.output.exists())

    def test_input_hash_drift_and_canonical_third_value_fail_closed(self):
        self.prepare()
        before = (self.output / "receipt.json").read_bytes()
        self.policy_file.write_text("policy changed")
        with self.assertRaises(ValueError): self.inspect()
        self.assertEqual((self.output / "receipt.json").read_bytes(), before)
        self.policy_file.write_text("fixed policy")
        self.canonical.write_bytes(b"third value")
        with self.assertRaisesRegex(ValueError, "canonical third value"): self.inspect()

    def test_current_policy_change_invalidates_inspect_without_success_promotion(self):
        self.prepare()
        self.policies["maintenance"]["explanation"]["policyVersion"] = "3.0"
        with self.assertRaises(ValueError): self.inspect()

    def test_resend_is_read_only_and_conflicting_output_never_overwritten(self):
        receipt = self.prepare()
        stamps = {n: (self.output / n).stat().st_mtime_ns for n in module.FILES}
        self.assertEqual(self.prepare(), receipt)
        self.assertEqual(stamps, {n: (self.output / n).stat().st_mtime_ns for n in module.FILES})
        (self.output / "review-input.json").write_text("third output")
        with self.assertRaisesRegex(ValueError, "immutable review output conflict"): self.prepare()
        self.assertEqual((self.output / "review-input.json").read_text(), "third output")

    def test_interruption_before_receipt_is_incomplete_then_resumes_identical_bytes(self):
        def stop(name):
            if name == "review-input.json": raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError): self.prepare(failure_hook=stop)
        with self.assertRaisesRegex(ValueError, "complete review context"): self.inspect()
        stamps = {n: (self.output / n).stat().st_mtime_ns for n in module.FILES[:2]}
        self.prepare(); self.inspect()
        self.assertEqual(stamps, {n: (self.output / n).stat().st_mtime_ns for n in module.FILES[:2]})

    def test_competing_writer_is_not_overwritten_or_issued_a_receipt(self):
        original = module._exclusive
        def competing(path, raw):
            if path.name == "review-input.json": path.write_bytes(b"competing writer")
            return original(path, raw)
        with patch.object(module, "_exclusive", side_effect=competing):
            with self.assertRaisesRegex(ValueError, "competing immutable output"): self.prepare()
        self.assertEqual((self.output / "review-input.json").read_bytes(), b"competing writer")
        self.assertFalse((self.output / "receipt.json").exists())

    def test_temporary_canonical_or_plan_swap_uses_only_hashed_bytes_in_prepare_and_inspect(self):
        # The old hash-read / decode-read gap could ingest alternate JSON and
        # then accept the original file restored before the final hash check.
        for target_name in ("canonical", "plan"):
            target = self.canonical if target_name == "canonical" else self.path
            original_raw = target.read_bytes()
            alternate = json.loads(original_raw)
            if target_name == "canonical":
                alternate["questions"][0]["explanationText"] = "未承認の別内容。"
            else:
                alternate["taskRequirement"] = {"requirementHash": "unapproved alternate purpose"}
            alternate_raw = module._bytes(alternate)
            for operation in ("prepare", "inspect"):
                with self.subTest(target=target_name, operation=operation):
                    if operation == "prepare" and self.output.exists():
                        for child in self.output.iterdir(): child.unlink()
                        self.output.rmdir()
                    if operation == "inspect" and not self.output.exists(): self.prepare()
                    real_read = Path.read_bytes
                    swapped = False
                    def racing_read(path):
                        nonlocal swapped
                        raw = real_read(path)
                        if path == target and not swapped:
                            swapped = True
                            target.write_bytes(alternate_raw)
                        return raw
                    def restore_at_policy(*args):
                        target.write_bytes(original_raw)
                        return deepcopy(self.policies)
                    try:
                        with patch.object(Path, "read_bytes", racing_read), \
                                patch.object(module, "_current_policies", side_effect=restore_at_policy):
                            result = self.prepare() if operation == "prepare" else self.inspect()
                        self.assertTrue(swapped)
                        context = json.loads((self.output / "context.json").read_bytes())
                        review = json.loads((self.output / "review-input.json").read_bytes())
                        self.assertEqual(context["taskRequirement"], self.plan["taskRequirement"])
                        self.assertEqual(review["documentsById"][self.ids[0]]["explanationText"], self.rows[0]["explanationText"])
                        self.assertFalse(result["machineReady"])
                    finally:
                        target.write_bytes(original_raw)

    def test_separate_load_saved_receipt_must_equal_snapshotted_receipt(self):
        alternate = deepcopy(self.receipt)
        alternate["approvalBinding"]["resultHash"] = "0" * 64
        self.mock_saved.side_effect = lambda *a, **k: alternate
        with self.assertRaisesRegex(ValueError, "receipt changed during verification"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_unsealed_plan_is_rejected_before_saved_boundary_or_output(self):
        self.plan["taskRequirement"] = {"requirementHash": "unapproved"}
        self.write(self.path, self.plan)
        with self.assertRaisesRegex(ValueError, "snapshotted save plan hash"):
            self.prepare()
        self.mock_saved.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_foreign_scope_added_during_prepare_is_rejected_before_receipt(self):
        def foreign_scope(name):
            if name == "context.json": (self.output / "other-scope.json").write_text("keep")
        with self.assertRaisesRegex(ValueError, "output scope changed"):
            self.prepare(failure_hook=foreign_scope)
        self.assertFalse((self.output / "receipt.json").exists())
        self.assertEqual((self.output / "other-scope.json").read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "complete review context"):
            self.inspect()

    def test_foreign_scope_added_after_receipt_never_returns_completion(self):
        def foreign_scope(name):
            if name == "receipt.json": (self.output / "other-scope.json").write_text("keep")
        with self.assertRaisesRegex(ValueError, "output scope changed before completion"):
            self.prepare(failure_hook=foreign_scope)
        with self.assertRaisesRegex(ValueError, "complete review context"):
            self.inspect()

    def test_foreign_scope_added_during_inspect_derivation_is_rejected_at_return(self):
        self.prepare()
        original = module._derive
        def inject(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.output / "other-scope.json").write_text("keep")
            return result
        with patch.object(module, "_derive", side_effect=inject):
            with self.assertRaisesRegex(ValueError, "output scope changed before completion"):
                self.inspect()
        self.assertEqual((self.output / "other-scope.json").read_text(), "keep")

    def test_foreign_scope_added_during_final_known_file_comparison_is_rejected(self):
        original_output = self.output
        original_read = Path.read_bytes
        original_verify = module._verify_complete_outputs
        try:
            for operation in ("prepare", "inspect"):
                for filename in module.FILES:
                    with self.subTest(operation=operation, filename=filename):
                        self.output = original_output.parent / f"race-{operation}-{filename}"
                        if operation == "inspect":
                            self.prepare()
                        verifying = False
                        injected = False
                        def inject(path):
                            nonlocal injected
                            raw = original_read(path)
                            if verifying and path == self.output / filename:
                                injected = True
                                (self.output / "other-scope.json").write_text("keep")
                            return raw
                        def verify(*args, **kwargs):
                            nonlocal verifying
                            verifying = True
                            return original_verify(*args, **kwargs)
                        with patch.object(Path, "read_bytes", inject), \
                                patch.object(module, "_verify_complete_outputs", side_effect=verify):
                            with self.assertRaisesRegex(ValueError, "output scope changed before completion"):
                                self.prepare() if operation == "prepare" else self.inspect()
                        self.assertTrue(injected)
                        self.assertEqual((self.output / "other-scope.json").read_text(), "keep")
        finally:
            self.output = original_output

    def test_input_changes_during_final_output_comparison_are_rejected_in_both_paths(self):
        original_output = self.output
        original_read = Path.read_bytes
        original_verify = module._verify_complete_outputs
        try:
            for operation in ("prepare", "inspect"):
                for filename in module.FILES:
                    for input_name, target in (("canonical", self.canonical), ("policy", self.policy_file)):
                        with self.subTest(operation=operation, filename=filename, input=input_name):
                            self.output = original_output.parent / f"input-race-{operation}-{filename}-{input_name}"
                            if operation == "inspect":
                                self.prepare()
                            original_input = original_read(target)
                            verifying = False
                            injected = False
                            def inject(path):
                                nonlocal injected
                                raw = original_read(path)
                                if verifying and path == self.output / filename:
                                    injected = True
                                    target.write_bytes(original_input + b" ")
                                return raw
                            def verify(*args, **kwargs):
                                nonlocal verifying
                                verifying = True
                                return original_verify(*args, **kwargs)
                            try:
                                with patch.object(Path, "read_bytes", inject), \
                                        patch.object(module, "_verify_complete_outputs", side_effect=verify):
                                    with self.assertRaisesRegex(ValueError, "fixed review input changed"):
                                        self.prepare() if operation == "prepare" else self.inspect()
                                self.assertTrue(injected)
                            finally:
                                target.write_bytes(original_input)
        finally:
            self.output = original_output

    def test_input_drift_during_prepare_cannot_issue_receipt(self):
        def drift(name):
            if name == "context.json": self.policy_file.write_text("changed during write")
        with self.assertRaisesRegex(ValueError, "fixed review input changed"): self.prepare(failure_hook=drift)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_transaction_escape_symlink_and_unknown_output_are_rejected(self):
        self.db.execute("CREATE TABLE fixture(x)"); self.db.execute("INSERT INTO fixture VALUES(1)")
        with self.assertRaisesRegex(ValueError, "outside a ledger transaction"): self.prepare()
        self.db.rollback()
        with self.assertRaises(ValueError): module.prepare(self.root, self.path, self.root / "outside", connection=self.db)
        link = self.private / "link"; link.symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(ValueError): module.prepare(self.root, self.path, link / "out", connection=self.db)
        self.output.mkdir(); (self.output / "unexpected").write_text("keep")
        with self.assertRaisesRegex(ValueError, "output conflict"): self.prepare()
        self.assertEqual((self.output / "unexpected").read_text(), "keep")

    def test_outputs_are_private_and_context_does_not_modify_sources(self):
        canonical = self.canonical.read_bytes(); plan = self.path.read_bytes()
        self.prepare(official_order=self.mapping_path)
        self.assertEqual(self.canonical.read_bytes(), canonical); self.assertEqual(self.path.read_bytes(), plan)
        for n in module.FILES:
            self.assertEqual(os.stat(self.output / n).st_mode & 0o777, 0o600)

    def test_unknown_law_classification_or_new_policy_is_held_without_inventing_applicability(self):
        self.bundle["questions"][0].pop("isLawRelated")
        canonical = self.write(self.canonical, self.bundle)
        self.plan["candidateSha256"] = canonical["sha256"]; self.seal_plan()
        self.policies["maintenance"]["new_stage"] = {"id": "new_stage", "automatic": True,
            "policyVersion": "1.0", "policyFingerprint": "new"}
        self.prepare()
        context = json.loads((self.output / "context.json").read_bytes())
        self.assertEqual(set(context["preflight"]["requiredStageIds"]), module.CORE_STAGES)
        self.assertEqual(set(context["preflight"]["applicabilityUnresolvedStageIds"]),
                         {"law_context", "law_audit", "new_stage"})
        self.assertIn("stage_applicability_unresolved", context["preflight"]["blockers"])

    def test_cli_uses_existing_read_only_ledger_and_has_no_bypass_flag(self):
        ledger = self.private / "ledger.sqlite3"
        connection = sqlite3.connect(ledger); connection.execute("CREATE TABLE fixture(x)")
        connection.commit(); connection.close()
        before = ledger.read_bytes()
        with patch.object(module.saver, "LEDGER", ledger), contextlib.redirect_stdout(io.StringIO()) as stdout:
            module.main(["prepare", "--repo-root", str(self.root), "--plan", str(self.path),
                         "--output", str(self.output), "--official-order", str(self.mapping_path)])
            module.main(["inspect", "--repo-root", str(self.root), "--output", str(self.output)])
        self.assertEqual(ledger.read_bytes(), before)
        self.assertEqual(len(stdout.getvalue().splitlines()), 2)
        with patch.object(module.saver, "LEDGER", self.private / "absent.sqlite3"):
            with self.assertRaises(sqlite3.OperationalError):
                module.main(["inspect", "--repo-root", str(self.root), "--output", str(self.output)])
        self.assertFalse((self.private / "absent.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
