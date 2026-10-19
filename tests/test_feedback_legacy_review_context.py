"""Focused, dependency-injected boundary tests; no production success is claimed."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.common.aggregate_answer_decomposition import generate_statement_candidates
from scripts.common.question_identity import load_source_record_inventory
from scripts.common.scoped_canonical_context import reader_dependencies
from tools.question_bank import feedback_legacy_review_context as legacy


class LegacyReviewContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.dependencies = reader_dependencies(cls.repo, [cls.repo / "tools/question_bank/feedback_legacy_review_context.py"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.code_paths = []
        for source in self.dependencies:
            destination = self.root / source.relative_to(self.repo)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            self.code_paths.append(destination)
        # Cache dependency selection only; every real dependency byte remains pinned.
        dependency_patch = patch.object(legacy, "reader_dependencies", return_value=self.code_paths)
        dependency_patch.start()
        self.addCleanup(dependency_patch.stop)
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            CREATE TABLE improvement_tasks(task_id,title,need,target,status);
            CREATE TABLE ai_question_candidates(candidate_id,question_id,text_hash,review_status,improvement_task_id);
            INSERT INTO improvement_tasks VALUES ('task','建替え','違いの理解','live-a','blocked');
            INSERT INTO ai_question_candidates VALUES ('candidate','live-a','hash','promoted','task');
        """)
        self.ids = ["live-a", "live-i", "live-u", "live-e"]
        self.record = {"questionBodyText": "不適切なものはいくつあるか。\nア　条件A。\nイ　条件B。\nウ　条件C。\nエ　承認決議と承諾。",
            "questionLabel": "問33", "examYear": 2023, "choiceTextList": ["一つ", "二つ", "三つ", "四つ"],
            "correctChoiceText": ["正しい"] * 4, "canonical_question_key": "qualification:2023:q033",
            "source_question_id": "qualification:source:33", "original_question_id": "original-source",
            "public_question_id": "original-source", "sourceUniqueKeys": [f"unique-{label}" for label in "アイウエ"]}
        self.source_path = self.root / "output/qualification/questions_json/group/00_source/source.json"
        source_ref = self.ref(self.source_path, {"question_bodies": [self.record]})
        self.preservation()
        entry = load_source_record_inventory(self.source_path.parent, qualification="qualification", list_group_id="group")[0]
        generated = generate_statement_candidates(self.record["questionBodyText"], self.record["choiceTextList"])
        chosen = generated["candidates"][0]
        self.mappings = []
        self.snapshots = {}
        for label, qid, span in zip("アイウエ", self.ids, chosen["spans"]):
            offsets = {k: span[k] for k in ("start", "end")}
            self.mappings.append(dict(label=label, uniqueKey=f"unique-{label}", span=offsets, questionId=qid))
            statement = self.record["questionBodyText"][span["start"]:span["end"]][1:].lstrip()
            if label == "エ":
                statement = "承認決議を議決し、承諾。"
            fields = dict(correctChoiceText="間違い" if label in "アエ" else "正しい", examSource=f"問33 {label}",
                examYear=2023, explanationText=f"旧説明{label}", isChoiceOnly=False, isDeleted=False,
                originalQuestionBodyText="不適切なものはいくつあるか。", originalQuestionChoiceText=statement,
                originalQuestionId="legacy-original", qualificationId="legacy-qualification", questionBodyText="正誤を答えよ。",
                questionSetId="set", questionText=f"正誤を答えよ。{statement}", questionType="true_false")
            self.snapshots[qid] = dict(questionId=qid, exists=True, fields=fields, updateTimeExact="2026-10-19T00:00:00.123456789Z")
        self.private = self.root / legacy.PRIVATE / "fixture"
        baseline = self.ref(self.private / "baseline.json", [{k: v for k, v in s.items() if k != "exists"}
            for s in self.snapshots.values()])
        pdf_ref = self.ref(self.private / "official.pdf", b"%PDF-1.7\nfixture only\n")
        self.candidate = dict(schemaVersion="feedback-improvement-result/v1", taskId="task", status="blocked",
            approvalReady=False, formalPatchSaved=False, FirestoreWritten=False, questionIds=self.ids,
            sourceBinding=dict(canonicalQuestionKey="qualification:2023:q033", sourceQuestionId="qualification:source:33",
                sourceOriginalId="original-source", recordIndex=0, liveOriginalQuestionId="legacy-original", source=source_ref,
                labelToLiveId={m["label"]: m["questionId"] for m in self.mappings}, sourceTfFlagsTrusted=False,
                officialAnswerText="二つ", officialOriginalAnswerNumber="2"),
            primaryEvidence=[pdf_ref], proposedUpdates=[dict(questionId="live-a", baseline=baseline,
                updateTimeExact=self.snapshots["live-a"]["updateTimeExact"], fieldDiffs=[dict(field="explanationText",
                    beforePresent=True, before="旧説明ア", after="新説明ア")])])
        self.candidate_path = self.private / "candidate.json"
        candidate_ref = self.ref(self.candidate_path, self.candidate)
        self.request = dict(schemaVersion=legacy.REQUEST, taskId="task",
            source={**source_ref, "qualification": "qualification", "listGroupId": "group", "recordIndex": 0,
                "binding": entry.identity.binding.as_mapping(), "canonicalQuestionKey": "qualification:2023:q033",
                "sourceQuestionId": "qualification:source:33", "sourceOriginalId": "original-source"},
            officialPdf={**pdf_ref, "questionPage": 26, "answerPage": 40, "questionNumber": 33, "answerNumber": 2, "answerText": "二つ"},
            candidate=candidate_ref, liveOriginalQuestionId="legacy-original", liveQualificationId="legacy-qualification",
            statementCandidateId=chosen["candidateId"], mappings=self.mappings)
        self.request_path = self.private / "request.json"
        self.ref(self.request_path, self.request)
        self.output = self.private / "frozen"
        self.read_calls = []

    def ref(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else legacy._bytes(value))
        return dict(ref=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def preservation(self):
        self.ref(self.root / legacy.MANIFEST,
            (json.dumps(dict(path=str(self.source_path.relative_to(self.root)),
                            sha256=hashlib.sha256(self.source_path.read_bytes()).hexdigest())) + "\n").encode())

    def reader(self, ids, fields):
        self.read_calls.append((ids, fields))
        return deepcopy(self.snapshots)

    def prepare(self):
        return legacy.prepare(self.root, self.request_path, self.output, connection=self.connection, _snapshot_reader=self.reader)

    def inspect(self):
        return legacy.inspect(self.root, self.output, connection=self.connection)

    def save_request(self):
        self.ref(self.request_path, self.request)

    def save_candidate(self):
        self.request["candidate"] = self.ref(self.candidate_path, self.candidate)
        self.save_request()

    def test_full_context_preserves_source_and_distinguishes_read_and_write(self):
        source_before = self.source_path.read_bytes()
        ledger_before = list(self.connection.iterdump())
        receipt = self.prepare()
        self.assertEqual(receipt, self.inspect())
        self.assertEqual(receipt["readSetIds"], sorted(self.ids))
        self.assertEqual(receipt["writeSetIds"], ["live-a"])
        self.assertEqual(self.source_path.read_bytes(), source_before)
        self.assertEqual(list(self.connection.iterdump()), ledger_before)
        context = json.loads((self.output / "context.json").read_bytes())
        self.assertEqual(context["sourceRecord"], self.record)
        self.assertFalse(context["sourceCorrectChoiceVectorTrusted"])
        self.assertFalse(context["officialAnswerTransfersToTf"])
        self.assertEqual(context["observation"]["reader"], "injected-test-reader")
        self.assertEqual([m["sourceLiveTextEqual"] for m in context["mappings"]], [True, True, True, False])
        self.assertTrue(all(m["independentMappingReviewRequired"] for m in context["mappings"]))
        self.assertEqual(context["writeSet"][0]["writeFields"], ["explanationText"])
        for value in (context, receipt, json.loads((self.output / "review-input.json").read_bytes())):
            self.assertTrue(all(value[key] is False for key in legacy.FLAGS))
        self.assertEqual(len(self.read_calls), 1)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.output.iterdir()))

    def test_retransmission_is_exact_bytes_and_reads_fresh(self):
        first = self.prepare()
        files = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(self.prepare(), first)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, files)
        self.assertEqual(len(self.read_calls), 2)
        self.snapshots["live-e"]["fields"]["explanationText"] = "競合"
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.prepare()
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, files)

    def test_prepare_default_calls_production_snapshot_reader(self):
        with patch.object(legacy, "_snapshots", side_effect=self.reader) as live:
            legacy.prepare(self.root, self.request_path, self.output, connection=self.connection)
        self.assertEqual(live.call_count, 1)
        self.assertEqual(json.loads((self.output / "context.json").read_bytes())["observation"]["reader"],
                         "production-fresh-FirestoreReadback")

    def test_unknown_or_missing_request_fields_fail_before_read(self):
        paths = [(), ("source",), ("officialPdf",), ("source", "binding")]
        for path in paths:
            with self.subTest(path=path):
                changed = deepcopy(self.request)
                target = changed
                for segment in path:
                    target = target[segment]
                target["unknown"] = True
                self.ref(self.request_path, changed)
                with self.assertRaisesRegex(ValueError, "unknown or missing"):
                    self.prepare()
        self.assertEqual(self.read_calls, [])

    def test_duplicate_json_key_is_rejected(self):
        self.request_path.write_bytes(b'{"schemaVersion":"x","schemaVersion":"y"}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            self.prepare()

    def test_missing_duplicate_and_mixed_sibling_mapping_are_rejected(self):
        for mode in ("missing", "id", "label", "uniqueKey", "span", "foreign-key"):
            with self.subTest(mode=mode):
                changed = deepcopy(self.request)
                mappings = changed["mappings"]
                if mode == "missing": mappings.pop()
                elif mode in ("id", "label", "uniqueKey"):
                    key = "questionId" if mode == "id" else mode
                    mappings[1][key] = mappings[0][key]
                elif mode == "span": mappings[1]["span"] = mappings[0]["span"]
                else: mappings[0]["uniqueKey"] = "other-source"
                self.ref(self.request_path, changed)
                with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.read_calls, [])

    def test_cross_source_identity_candidate_and_pdf_locator_are_rejected(self):
        mutations = [("source", "sourceQuestionId", "other"), ("source", "canonicalQuestionKey", "other"),
            ("source", "sourceOriginalId", "other"), ("source", "recordIndex", 1),
            ("officialPdf", "questionNumber", 32), ("officialPdf", "answerNumber", 1),
            ("officialPdf", "answerText", "三つ")]
        for parent, key, value in mutations:
            with self.subTest(key=key):
                changed = deepcopy(self.request); changed[parent][key] = value
                self.ref(self.request_path, changed)
                with self.assertRaises(ValueError): self.prepare()

    def test_source_registered_manifest_hash_is_required(self):
        self.ref(self.root / legacy.MANIFEST, b'{"path":"elsewhere","sha256":"unused"}\n')
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.prepare()
        self.assertEqual(self.read_calls, [])

    def test_source_candidate_pdf_and_dependency_drift_fail_inspect(self):
        self.prepare()
        paths = [self.source_path, self.candidate_path, Path(self.request["officialPdf"]["ref"]),
                 self.root / "scripts/common/question_identity.py", self.request_path, self.root / legacy.MANIFEST]
        for path in paths:
            with self.subTest(path=path):
                before = path.read_bytes()
                path.write_bytes(before + b" ")
                with self.assertRaises((ValueError, SyntaxError)):
                    self.inspect()
                path.write_bytes(before)
        self.inspect()

    def test_output_or_receipt_drift_and_partial_outputs_fail(self):
        self.prepare()
        for name in ("context.json", "review-input.json", "receipt.json"):
            with self.subTest(name=name):
                path = self.output / name; before = path.read_bytes()
                path.write_bytes(before + b" ")
                with self.assertRaises(ValueError): self.inspect()
                path.unlink()
                with self.assertRaises(ValueError): self.inspect()
                with self.assertRaises(ValueError): self.prepare()
                path.write_bytes(before)
        self.inspect()

    def test_partial_write_interruption_cannot_be_retried_as_success(self):
        original = Path.open
        def interrupted(path, *args, **kwargs):
            if path == self.output / "review-input.json" and args == ("xb",):
                raise OSError("interruption")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", interrupted):
            with self.assertRaisesRegex(OSError, "interruption"): self.prepare()
        self.assertEqual({p.name for p in self.output.iterdir()}, {"context.json"})
        with self.assertRaisesRegex(ValueError, "incomplete"): self.prepare()
        with self.assertRaisesRegex(ValueError, "incomplete"): self.inspect()

    def test_third_value_field_presence_and_update_time_conflicts(self):
        for kind in ("third", "missing", "time", "extra", "identity", "missing-sibling"):
            with self.subTest(kind=kind):
                original = deepcopy(self.snapshots)
                if kind == "third": self.snapshots["live-a"]["fields"]["explanationText"] = "third"
                elif kind == "missing": del self.snapshots["live-a"]["fields"]["explanationText"]
                elif kind == "time": self.snapshots["live-a"]["updateTimeExact"] = "2026-10-19T00:00:00.123456788Z"
                elif kind == "extra": self.snapshots["live-a"]["fields"]["suggestedQuestions"] = []
                elif kind == "identity": self.snapshots["live-a"]["fields"]["originalQuestionId"] = "other"
                else: del self.snapshots["live-i"]
                with self.assertRaises(ValueError): self.prepare()
                self.snapshots = original

    def test_absent_noop_truth_and_unknown_diff_fields_cannot_be_added(self):
        original = deepcopy(self.candidate)
        for kind in ("absent", "noop", "truth", "unknown", "metadata", "duplicate"):
            with self.subTest(kind=kind):
                self.candidate = deepcopy(original)
                diffs = self.candidate["proposedUpdates"][0]["fieldDiffs"]
                if kind == "absent": diffs[0]["beforePresent"] = False
                elif kind == "noop": diffs[0]["after"] = diffs[0]["before"]
                elif kind == "truth": diffs[0]["field"] = "correctChoiceText"
                elif kind == "metadata": diffs[0]["field"] = "lawRevisionFacts"
                elif kind == "duplicate": diffs.append(deepcopy(diffs[0]))
                else: diffs[0]["afterPresent"] = True
                self.save_candidate()
                with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.read_calls, [])

    def test_readonly_task_requirements_drift_fails(self):
        self.prepare()
        self.connection.execute("UPDATE improvement_tasks SET need='changed'")
        with self.assertRaisesRegex(ValueError, "drift"):
            self.inspect()

    def test_task_sources_cannot_refer_to_another_question(self):
        self.connection.execute("UPDATE ai_question_candidates SET question_id='another-source'")
        with self.assertRaisesRegex(ValueError, "requirements"):
            self.prepare()

    def test_symlink_and_escape_input_output_rejected(self):
        outside = self.root.parent / (self.root.name + "-outside.json")
        outside.write_bytes(b"{}")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        for target in (outside, self.private / "linked.json"):
            if target.parent == self.private: target.symlink_to(self.request_path)
            with self.assertRaises(ValueError):
                legacy.prepare(self.root, target, self.output, connection=self.connection, _snapshot_reader=self.reader)
        real = self.private / "real"; real.mkdir()
        linked = self.private / "linked"; linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            legacy.prepare(self.root, self.request_path, linked / "output", connection=self.connection, _snapshot_reader=self.reader)
        with self.assertRaises(ValueError):
            legacy.prepare(self.root, self.request_path, self.root / "output/escape", connection=self.connection, _snapshot_reader=self.reader)

    def test_symlinked_fixed_input_and_output_fail_inspect(self):
        self.prepare()
        path = self.candidate_path
        real = self.private / "candidate-real.json"
        path.rename(real); path.symlink_to(real)
        with self.assertRaises(ValueError): self.inspect()
        path.unlink(); real.rename(path)
        path = self.output / "receipt.json"
        real = self.private / "receipt-real.json"
        path.rename(real); path.symlink_to(real)
        with self.assertRaises(ValueError): self.inspect()

    def test_output_changed_during_inspection_fails(self):
        self.prepare()
        original = legacy._build
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.output / "receipt.json"
            path.write_bytes(path.read_bytes() + b" ")
            return result
        with patch.object(legacy, "_build", side_effect=drift):
            with self.assertRaisesRegex(ValueError, "during inspection"):
                self.inspect()

    def test_input_changed_during_live_read_fails_without_outputs(self):
        def drift(ids, fields):
            self.request_path.write_bytes(self.request_path.read_bytes() + b" ")
            return self.reader(ids, fields)
        with self.assertRaisesRegex(ValueError, "drift"):
            legacy.prepare(self.root, self.request_path, self.output, connection=self.connection, _snapshot_reader=drift)
        self.assertFalse(self.output.exists())

    def test_concurrent_directory_creator_is_preserved(self):
        original = Path.mkdir
        def compete(path, *args, **kwargs):
            if path == self.output:
                original(path, *args, **kwargs)
                (path / "competitor").write_bytes(b"owned by another invocation")
            return original(path, *args, **kwargs)
        with patch.object(Path, "mkdir", compete):
            with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual({p.name for p in self.output.iterdir()}, {"competitor"})
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.inspect()

    def test_cli_opens_only_existing_readonly_ledger(self):
        self.prepare()
        self.connection.commit()
        database = self.private / "ledger.sqlite3"
        with closing(sqlite3.connect(database)) as copied:
            self.connection.backup(copied)
        original = legacy.inspect
        def readonly(root, output, *, connection):
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE improvement_tasks SET need='forbidden'")
            return original(root, output, connection=connection)
        with patch.object(legacy, "inspect", side_effect=readonly), patch("builtins.print"):
            self.assertEqual(legacy.main(["--repo-root", str(self.root), "--db", str(database),
                "inspect", "--output", str(self.output)]), 0)
        missing = self.private / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            legacy.main(["--repo-root", str(self.root), "--db", str(missing),
                "inspect", "--output", str(self.output)])
        self.assertFalse(missing.exists())

    def test_same_values_new_input_bytes_and_live_failure_never_overwrite(self):
        self.prepare()
        old = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.request_path.write_bytes(self.request_path.read_bytes() + b" ")
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, old)
        with patch.object(legacy, "_snapshots", side_effect=RuntimeError("live unavailable")):
            with self.assertRaisesRegex(RuntimeError, "live unavailable"):
                legacy.prepare(self.root, self.request_path, self.private / "failed", connection=self.connection)
        self.assertFalse((self.private / "failed").exists())


if __name__ == "__main__":
    unittest.main()
