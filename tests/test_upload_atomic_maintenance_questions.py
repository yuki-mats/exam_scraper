"""The mixed-mask transport has no gate waiver or publication-completion role.

All changed targets must share one batch/CAS. Partial unchanged scopes are held
because omitting one write would omit that target's CAS from the batch. A caller
must independently verify complete sibling membership and persist/reconcile its
operation before deciding whether publication can be completed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import contextlib
import io
import json
import tempfile
import unittest

from google.api_core.datetime_helpers import DatetimeWithNanoseconds

from scripts.upload import upload_questions_to_firestore as module


class AtomicMaintenanceUploadTests(unittest.TestCase):
    before_stamp = "2026-10-16T00:00:00.123456789Z"
    after_stamp = "2026-10-16T01:00:00.987654321Z"
    ids = [f"gas-fixture-s{i:02}" for i in range(1, 6)]

    def fixture(self):
        masks = [module.DETAILS_ONLY_WRITE_FIELDS, module.DETAILS_ONLY_WRITE_FIELDS,
                 module.EXPLANATION_DETAILS_WRITE_FIELDS, module.LAW_FACTS_DETAILS_WRITE_FIELDS,
                 module.DETAILS_ONLY_WRITE_FIELDS]
        questions = [{"questionId": identity, "originalQuestionId": "original-fixture",
                      "qualificationId": "gas-fixture", "questionText": "対象本文",
                      "isDeleted": False, "isChoiceOnly": False,
                      "explanationText": "正しい。新しい説明。", "suggestedQuestions": ["補足"],
                      "suggestedQuestionDetails": [{"question": "補足", "answer": "新しい回答"}],
                      "lawRevisionFacts": {"auditStatus": "same_as_current",
                                           "auditedAt": "2026-07-28", "differenceFacts": ["新しい補足。"]}}
                     for identity in self.ids]
        live = {question["questionId"]: deepcopy(question) for question in questions}
        for identity in self.ids:
            live[identity]["suggestedQuestionDetails"][0]["answer"] = "元の回答"
            live[identity]["explanationText"] = "正しい。元の説明。"
            live[identity]["lawRevisionFacts"]["differenceFacts"] = ["元の補足。"]
            live[identity]["createdById"] = "preserved-author"
            live[identity]["privateUnchangedField"] = {"kept": True}
        payload = {"schemaVersion": module.ATOMIC_MAINTENANCE_SCHEMA,
                   "originalQuestionId": "original-fixture", "questions": questions,
                   "writeFieldsByDocument": {identity: list(mask) for identity, mask in zip(self.ids, masks)},
                   "expectedUpdateTimes": {identity: self.before_stamp for identity in self.ids},
                   "expectedLiveFingerprint": module.firestore_live_fingerprint(self.ids, live)}
        return payload, live

    class Database:
        def __init__(self, live, stamp, *, mode=None, snapshot_transform=None):
            self.live = deepcopy(live)
            self.times = {identity: DatetimeWithNanoseconds.from_rfc3339(stamp) for identity in live}
            self.mode = mode
            self.snapshot_transform = snapshot_transform
            self.reads = 0
            self.collections = []
            self.batches = 0
            self.commits = 0
            self.pending = []
            self.applied = []

        def collection(self, name):
            self.collections.append(name)
            return self

        def document(self, identity):
            return identity

        def get_all(self, refs, field_paths):
            self.reads += 1
            snapshots = [SimpleNamespace(
                id=identity, exists=identity in self.live, update_time=self.times.get(identity),
                to_dict=lambda identity=identity: deepcopy(self.live.get(identity)),
            ) for identity in refs]
            return self.snapshot_transform(snapshots) if self.snapshot_transform else snapshots

        def batch(self):
            self.batches += 1
            db = self

            class Batch:
                def update(self, ref, values, *, option):
                    db.pending.append((ref, deepcopy(values), option))

                def create(self, *args, **kwargs):
                    raise AssertionError("create is forbidden")

                def commit(self):
                    db.commits += 1
                    if db.mode == "conflict":
                        db.times[db.pending[-1][0]] = DatetimeWithNanoseconds.from_rfc3339(
                            "2026-10-16T00:00:00.123456790Z")
                    # Validate the whole batch before applying any fake write.
                    if any(option._last_update_time.rfc3339() != db.times[identity].rfc3339()
                           for identity, _, option in db.pending):
                        raise RuntimeError("CAS conflict")
                    if db.mode == "raise_before_commit":
                        raise RuntimeError("unavailable")
                    responses = []
                    for index, (identity, values, _) in enumerate(db.pending):
                        stamp = DatetimeWithNanoseconds.from_rfc3339(
                            f"2026-10-16T01:00:00.{987654321 + index}Z")
                        db.live[identity].update(values)
                        db.times[identity] = stamp
                        db.applied.append(identity)
                        responses.append(SimpleNamespace(update_time=stamp))
                    if db.mode == "raise_after_commit":
                        raise RuntimeError("response lost")
                    if db.mode == "wrong_count":
                        return responses[:-1]
                    if db.mode == "no_time":
                        responses[-1] = SimpleNamespace(update_time=None)
                    if db.mode == "bad_time":
                        responses[-1] = SimpleNamespace(update_time=SimpleNamespace(rfc3339=lambda: "invalid"))
                    return responses

            return Batch()

    def upload(self, payload, db=None, *, dry_run=False, raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "upload.json"
            path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
            with patch.object(module, "init_firestore", return_value=db) as init:
                result = module.upload_questions(str(path), dry_run=dry_run)
                return result, init.call_count

    def test_three_masks_five_documents_one_batch_with_exact_cas_and_response_order(self):
        payload, live = self.fixture()
        db = self.Database(live, self.before_stamp, snapshot_transform=lambda values: list(reversed(values)))
        result, initialized = self.upload(payload, db)
        self.assertEqual((initialized, db.reads, db.batches, db.commits), (1, 1, 1, 1))
        self.assertEqual(db.applied, self.ids)
        self.assertEqual(set(db.collections), {"questions"})
        self.assertEqual(result["status"], "committed")
        self.assertFalse(result["publicationConfirmed"])
        self.assertEqual(result["schemaVersion"], module.ATOMIC_MAINTENANCE_RESULT_SCHEMA)
        self.assertEqual(result["writeFieldsByDocument"], payload["writeFieldsByDocument"])
        self.assertEqual(result["changedDocumentIds"], self.ids)
        for index, (identity, values, option) in enumerate(db.pending):
            self.assertEqual(set(values), set(payload["writeFieldsByDocument"][identity]) | {"updatedAt", "updatedById"})
            self.assertEqual(option._last_update_time.rfc3339(), self.before_stamp)
            self.assertEqual(result["writeResults"][index], {
                "questionId": identity, "updateTime": f"2026-10-16T01:00:00.{987654321 + index}Z"})
            for key, value in live[identity].items():
                if key not in values:
                    self.assertEqual(db.live[identity][key], value)

    def test_dry_run_is_structural_only_and_never_initializes_sdk(self):
        payload, _ = self.fixture()
        result, initialized = self.upload(payload, dry_run=True)
        self.assertEqual(initialized, 0)
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["publicationConfirmed"])
        self.assertEqual(result["writeResults"], [])
        self.assertEqual(result["auditWriteFields"], ["updatedAt", "updatedById"])

    def test_caller_mutation_during_sdk_read_cannot_replace_validated_request(self):
        payload, live = self.fixture()
        approved = deepcopy(payload)

        def mutate_caller(values):
            # Reproduce the audit finding, then also attack masks/identity/CAS.
            payload["questions"][0]["suggestedQuestionDetails"][0]["answer"] = 42
            payload["questions"][3]["lawRevisionFacts"]["differenceFacts"] = [42]
            payload["questions"][0]["originalQuestionId"] = "another-original"
            payload["questions"][0]["questionId"] = "outside"
            payload["writeFieldsByDocument"][self.ids[0]].append("questionText")
            payload["expectedUpdateTimes"][self.ids[0]] = self.after_stamp
            payload["expectedLiveFingerprint"] = "0" * 64
            payload["originalQuestionId"] = "another-original"
            return values

        db = self.Database(live, self.before_stamp, snapshot_transform=mutate_caller)
        with patch.object(module, "init_firestore", return_value=db):
            result = module.upload_atomic_maintenance_questions(payload, "fixture")
        self.assertEqual((result["status"], db.commits), ("committed", 1))
        self.assertEqual(result["originalQuestionId"], approved["originalQuestionId"])
        self.assertEqual(result["documentIds"], self.ids)
        for key in ("writeFieldsByDocument", "expectedUpdateTimes", "expectedLiveFingerprint"):
            self.assertEqual(result[key], approved[key])
        for identity, fields, _ in db.pending:
            question = next(item for item in approved["questions"] if item["questionId"] == identity)
            for field in approved["writeFieldsByDocument"][identity]:
                self.assertEqual(fields[field], question[field])
        # The result also owns its masks; it cannot leak references back to caller.
        result["writeFieldsByDocument"][self.ids[2]].append("outside")
        self.assertEqual(payload["writeFieldsByDocument"][self.ids[2]],
                         approved["writeFieldsByDocument"][self.ids[2]])

    def test_auditor_numeric_answer_mutation_writes_only_validated_string(self):
        payload, live = self.fixture()
        approved_answer = payload["questions"][0]["suggestedQuestionDetails"][0]["answer"]

        def mutate_answer(values):
            payload["questions"][0]["suggestedQuestionDetails"][0]["answer"] = 42
            return values

        db = self.Database(live, self.before_stamp, snapshot_transform=mutate_answer)
        with patch.object(module, "init_firestore", return_value=db):
            result = module.upload_atomic_maintenance_questions(payload, "fixture")
        self.assertEqual((result["status"], db.commits), ("committed", 1))
        self.assertEqual(payload["questions"][0]["suggestedQuestionDetails"][0]["answer"], 42)
        self.assertEqual(db.pending[0][1]["suggestedQuestionDetails"][0]["answer"], approved_answer)

    def test_request_copy_preserves_nested_duplicate_key_evidence(self):
        payload, _ = self.fixture()
        raw = json.dumps(payload).replace('"answer":', '"answer": "duplicate", "answer":', 1)
        request = json.loads(raw, object_pairs_hook=module._UploadJSONObject)
        with patch.object(module, "init_firestore") as init:
            with self.assertRaisesRegex(ValueError, "duplicate keys"):
                module.upload_atomic_maintenance_questions(request, "fixture", dry_run=True)
            init.assert_not_called()
        self.assertEqual(request["questions"][0]["suggestedQuestionDetails"][0].duplicate_keys, {"answer"})

    def test_missing_or_extra_root_keys_and_legacy_contract_mixing_are_refused(self):
        payload, _ = self.fixture()
        invalid = []
        for key in payload:
            changed = deepcopy(payload)
            del changed[key]
            invalid.append(changed)
        invalid.extend([{**payload, "writeFields": ["explanationText"]},
                        {**payload, "allowLegacySuggestionRollback": True},
                        {**payload, "unrecognized": True},
                        {**payload, "schemaVersion": "question-maintenance-atomic-upload/v2"}])
        for value in invalid:
            with self.subTest(keys=list(value)), patch.object(module, "init_firestore") as init:
                with self.assertRaises(ValueError):
                    module.upload_atomic_maintenance_questions(value, "fixture", dry_run=True)
                init.assert_not_called()
        with self.assertRaisesRegex(ValueError, "explicit schema resolver"):
            module.resolve_write_fields(payload)
        # Even a schema typo must not fall through to the legacy full uploader.
        for key in ("schemaVersion",):
            changed = deepcopy(payload)
            changed[key] = "wrong-schema"
            with self.assertRaises(ValueError):
                self.upload(changed, dry_run=True)

    def test_invalid_ids_masks_versions_fingerprint_and_identity_are_refused_pre_sdk(self):
        payload, _ = self.fixture()
        mutations = [
            ("questions", []), ("questions", {}),
            ("questions", payload["questions"] * 2),
            ("writeFieldsByDocument", {}), ("writeFieldsByDocument", []),
            ("writeFieldsByDocument", {self.ids[0]: ["suggestedQuestionDetails"]}),
            ("writeFieldsByDocument", {**payload["writeFieldsByDocument"], "outside": ["suggestedQuestionDetails"]}),
            ("expectedUpdateTimes", None), ("expectedUpdateTimes", {}),
            ("expectedUpdateTimes", {self.ids[0]: self.before_stamp}),
            ("expectedUpdateTimes", {**payload["expectedUpdateTimes"], "outside": self.before_stamp}),
            ("originalQuestionId", ""), ("originalQuestionId", "other-original"),
            ("expectedLiveFingerprint", ""), ("expectedLiveFingerprint", "x" * 64),
            ("expectedLiveFingerprint", "A" * 64), ("expectedLiveFingerprint", None),
        ]
        invalid = [{**deepcopy(payload), key: value} for key, value in mutations]
        for mask in ([], None, ["explanationText"], ["suggestedQuestionDetails"] * 2,
                     ["suggestedQuestions", "suggestedQuestionDetails"], ["unknown"]):
            changed = deepcopy(payload)
            changed["writeFieldsByDocument"][self.ids[0]] = mask
            invalid.append(changed)
        for stamp in (True, "invalid", "2026-02-30T00:00:00Z", "2026-10-16T00:00:00",
                      "2026-10-16T00:00:00.1234567890Z"):
            changed = deepcopy(payload)
            changed["expectedUpdateTimes"][self.ids[-1]] = stamp
            invalid.append(changed)
        for field, value in (("questionId", "parent/child"), ("questionId", " trailing "),
                             ("questionId", ""), ("originalQuestionId", "another"),
                             ("qualificationId", "other"), ("qualificationId", ""),
                             ("writeFields", ["explanationText"]), ("isDeleted", True),
                             ("isChoiceOnly", True), ("suggestedQuestions", ["different"]),
                             ("explanationText", ""),
                             ("suggestedQuestionDetails", [{"question": "補足", "answer": ""}])):
            changed = deepcopy(payload)
            changed["questions"][0][field] = value
            invalid.append(changed)
        for changed in invalid:
            with self.subTest(payload=changed), patch.object(module, "init_firestore") as init:
                with self.assertRaises(ValueError):
                    module.upload_atomic_maintenance_questions(changed, "fixture", dry_run=True)
                init.assert_not_called()
        with patch.object(module, "BATCH_SIZE", 4), self.assertRaisesRegex(ValueError, "one nonempty batch"):
            self.upload(payload, dry_run=True)

    def test_duplicate_json_keys_at_every_depth_are_rejected(self):
        payload, _ = self.fixture()
        raw = json.dumps(payload)
        variants = [
            raw.replace('"schemaVersion":', '"schemaVersion": "duplicate", "schemaVersion":', 1),
            raw.replace('"expectedUpdateTimes": {', '"expectedUpdateTimes": {}, "expectedUpdateTimes": {', 1),
            raw.replace('"writeFieldsByDocument": {', '"writeFieldsByDocument": {}, "writeFieldsByDocument": {', 1),
            raw.replace('"questionId":', '"questionId": "duplicate", "questionId":', 1),
            raw.replace('"answer":', '"answer": "duplicate", "answer":', 1),
        ]
        for section in ("expectedUpdateTimes", "writeFieldsByDocument"):
            changed = raw
            offset = changed.index(f'"{section}": {{') + len(f'"{section}": {{')
            changed = changed[:offset] + f'"{self.ids[0]}": "duplicate", ' + changed[offset:]
            variants.append(changed)
        for value in variants:
            with self.subTest(raw=value), self.assertRaisesRegex(ValueError, "duplicate keys"):
                self.upload(None, dry_run=True, raw=value)

    def test_payload_law_metadata_shape_and_missing_masked_field_fail_before_sdk(self):
        payload, _ = self.fixture()
        for facts in ({}, None, {"auditStatus": "same_as_current", "differenceFacts": []},
                      {"auditStatus": "same_as_current", "differenceFacts": [" "]}):
            changed = deepcopy(payload)
            changed["questions"][3]["lawRevisionFacts"] = facts
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                self.upload(changed, dry_run=True)
        changed = deepcopy(payload)
        del changed["questions"][3]["lawRevisionFacts"]
        with self.assertRaises(ValueError):
            self.upload(changed, dry_run=True)

    def test_all_three_masks_use_the_existing_strict_suggestion_validation(self):
        payload, _ = self.fixture()
        for index in (0, 2, 3):
            for invalid_details in ([{"question": "補足", "answer": "回答"}] * 4,
                                    [{"question": "補足", "answer": 42}],
                                    [{"question": 42, "answer": "回答"}]):
                changed = deepcopy(payload)
                changed["questions"][index]["suggestedQuestionDetails"] = invalid_details
                changed["questions"][index]["suggestedQuestions"] = [
                    item["question"] for item in invalid_details]
                with self.subTest(index=index, details=invalid_details), self.assertRaises(ValueError):
                    self.upload(changed, dry_run=True)

    def test_non_object_upload_roots_and_malformed_questions_never_initialize_sdk(self):
        for value in (None, [], "invalid"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "root must be an object"):
                self.upload(value, dry_run=True)
        payload, _ = self.fixture()
        for value in (None, [], "invalid", 42):
            changed = deepcopy(payload)
            changed["questions"][-1] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.upload(changed, dry_run=True)

    def test_live_scope_time_missing_duplicate_extra_and_read_failure_prevent_batch(self):
        payload, live = self.fixture()

        def wrong_time(values):
            values[-1].update_time = DatetimeWithNanoseconds.from_rfc3339(self.after_stamp)
            return values

        def missing(values):
            values[-1].exists = False
            return values

        def read_failure(values):
            raise RuntimeError("read failed")

        transforms = [wrong_time, missing, read_failure, lambda values: values[:-1],
                      lambda values: values + values[:1],
                      lambda values: values + [SimpleNamespace(id="outside", exists=True)]]
        for transform in transforms:
            db = self.Database(live, self.before_stamp, snapshot_transform=transform)
            with self.subTest(transform=transform), self.assertRaises(RuntimeError):
                self.upload(payload, db)
            self.assertEqual((db.batches, db.commits), (0, 0))
            self.assertEqual(db.pending, [])

    def test_live_hash_mirror_identity_and_legal_provenance_are_all_checked_before_batch(self):
        payload, live = self.fixture()
        for key, value in (("suggestedQuestions", ["different"]), ("isDeleted", True),
                           ("isChoiceOnly", True), ("originalQuestionId", "different"),
                           ("qualificationId", "different"),
                           ("lawRevisionFacts", {"auditStatus": "hold", "differenceFacts": ["元の補足。"]})):
            changed_live = deepcopy(live)
            changed_live[self.ids[3]][key] = value
            # Bind a matching fingerprint so each semantic live validator is exercised.
            changed_payload = {**payload, "expectedLiveFingerprint": module.firestore_live_fingerprint(self.ids, changed_live)}
            db = self.Database(changed_live, self.before_stamp)
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.upload(changed_payload, db)
            self.assertEqual((db.batches, db.commits), (0, 0))
        db = self.Database(live, self.before_stamp)
        with self.assertRaises(RuntimeError):
            self.upload({**payload, "expectedLiveFingerprint": "0" * 64}, db)
        self.assertEqual(db.batches, 0)

    def test_partial_unchanged_refused_and_all_unchanged_no_commit_not_publication_proof(self):
        payload, live = self.fixture()
        for complete in (False, True):
            changed_live = deepcopy(live)
            for question in payload["questions"] if complete else payload["questions"][:1]:
                identity = question["questionId"]
                for field in payload["writeFieldsByDocument"][identity]:
                    changed_live[identity][field] = deepcopy(question[field])
            changed = {**payload, "expectedLiveFingerprint": module.firestore_live_fingerprint(self.ids, changed_live)}
            db = self.Database(changed_live, self.before_stamp)
            if complete:
                result, _ = self.upload(changed, db)
                self.assertEqual(result["status"], "no_changes")
                self.assertEqual(result["unchangedDocumentIds"], self.ids)
                self.assertFalse(result["publicationConfirmed"])
            else:
                with self.assertRaisesRegex(RuntimeError, "partially unchanged"):
                    self.upload(changed, db)
            self.assertEqual((db.batches, db.commits), (0, 0))

    def test_commit_conflict_error_lost_or_invalid_response_never_becomes_success(self):
        payload, live = self.fixture()
        for mode in ("conflict", "raise_before_commit", "raise_after_commit", "wrong_count", "no_time", "bad_time"):
            db = self.Database(live, self.before_stamp, mode=mode)
            with self.subTest(mode=mode):
                with self.assertRaises(module.AtomicMaintenanceCommitUnconfirmed) as raised:
                    self.upload(payload, db)
                self.assertEqual(db.commits, 1)
                self.assertEqual(raised.exception.prepared_result["status"], "prepared")
                self.assertEqual(raised.exception.prepared_result["writeResults"], [])
                self.assertFalse(raised.exception.prepared_result["publicationConfirmed"])
                if mode in ("conflict", "raise_before_commit"):
                    self.assertEqual(db.applied, [])
                else:
                    self.assertEqual(db.applied, self.ids)

    def test_old_versions_fail_on_resend_new_bound_versions_noop_remains_unconfirmed(self):
        payload, live = self.fixture()
        db = self.Database(live, self.before_stamp)
        self.upload(payload, db)
        with self.assertRaisesRegex(RuntimeError, "update_time changed"):
            self.upload(payload, db)
        self.assertEqual(db.commits, 1)
        rebound = {**payload, "expectedUpdateTimes": {identity: db.times[identity].rfc3339() for identity in self.ids},
                   "expectedLiveFingerprint": module.firestore_live_fingerprint(self.ids, db.live)}
        result, _ = self.upload(rebound, db)
        self.assertEqual(db.commits, 1)
        self.assertEqual(result["status"], "no_changes")
        self.assertFalse(result["publicationConfirmed"])

    def test_legacy_mask_route_keeps_none_return_contract(self):
        payload, live = self.fixture()
        legacy = {"questions": [payload["questions"][0]], "writeFields": ["suggestedQuestionDetails"],
                  "expectedUpdateTimes": {self.ids[0]: self.before_stamp}}
        db = self.Database({self.ids[0]: live[self.ids[0]]}, self.before_stamp)
        with contextlib.redirect_stdout(io.StringIO()):
            result, _ = self.upload(legacy, db)
        self.assertIsNone(result)
        self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
