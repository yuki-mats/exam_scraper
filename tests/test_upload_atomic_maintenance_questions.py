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
import pytest

from google.api_core.datetime_helpers import DatetimeWithNanoseconds

from scripts.upload import upload_questions_to_firestore as module
from tools.question_review_console.firestore_readback import FirestoreReadback


@pytest.fixture(autouse=True)
def prohibit_live_firestore(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("live Firestore SDK is forbidden in uploader contract tests")
    monkeypatch.setattr(module, "init_firestore", forbidden)
    monkeypatch.setattr(FirestoreReadback, "_database", forbidden)
    monkeypatch.setattr(module.firestore, "client", forbidden)
    monkeypatch.setattr(module.firebase_admin, "initialize_app", forbidden)


def test_default_fixture_denies_live_read_and_write_initialization():
    for initialize in (module.init_firestore, FirestoreReadback()._database,
                       module.firestore.client, module.firebase_admin.initialize_app):
        with pytest.raises(AssertionError, match='live Firestore SDK is forbidden'):
            initialize()


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

    def replacement_fixture(self):
        payload, live = self.fixture()
        masks = module.ATOMIC_SUGGESTION_REPLACEMENT_MASKS
        for index, question in enumerate(payload["questions"]):
            question["suggestedQuestions"] = []
            question["suggestedQuestionDetails"] = []
            payload["writeFieldsByDocument"][question["questionId"]] = list(masks[index % len(masks)])
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

    def test_paired_replacement_can_remove_all_suggestions_in_one_guarded_batch(self):
        payload, live = self.replacement_fixture()
        db = self.Database(live, self.before_stamp)
        result, initialized = self.upload(payload, db)
        self.assertEqual((initialized, db.batches, db.commits), (1, 1, 1))
        self.assertEqual(result["status"], "committed")
        self.assertFalse(result["publicationConfirmed"])
        for identity, values, option in db.pending:
            self.assertEqual(values["suggestedQuestions"], [])
            self.assertEqual(values["suggestedQuestionDetails"], [])
            self.assertEqual(set(values), set(payload["writeFieldsByDocument"][identity]) | {"updatedAt", "updatedById"})
            self.assertEqual(option._last_update_time.rfc3339(), self.before_stamp)
            for field, value in live[identity].items():
                if field not in values:
                    self.assertEqual(db.live[identity][field], value)

    def test_new_and_preserved_masks_can_share_the_same_atomic_batch(self):
        payload, live = self.replacement_fixture()
        original, _ = self.fixture()
        payload["questions"][-1] = original["questions"][-1]
        payload["writeFieldsByDocument"][self.ids[-1]] = list(module.DETAILS_ONLY_WRITE_FIELDS)
        db = self.Database(live, self.before_stamp)
        result, _ = self.upload(payload, db)
        self.assertEqual((result["status"], db.commits), ("committed", 1))
        self.assertEqual(db.live[self.ids[-1]]["suggestedQuestions"], ["補足"])
        self.assertEqual(db.live[self.ids[0]]["suggestedQuestions"], [])

    def test_replacement_mirrors_and_strict_strings_are_checked_before_sdk(self):
        payload, _ = self.replacement_fixture()
        invalid = [([], [{"question": "補足", "answer": "回答"}]),
                   (["補足"], []),
                   ([42], [{"question": 42, "answer": "回答"}]),
                   (["補足"], [{"question": "補足", "answer": 42}]),
                   (["補足"] * 4, [{"question": "補足", "answer": "回答"}] * 4)]
        for questions, details in invalid:
            changed = deepcopy(payload)
            changed["questions"][0].update(suggestedQuestions=questions, suggestedQuestionDetails=details)
            with self.subTest(questions=questions), patch.object(module, "init_firestore") as init:
                with self.assertRaises(ValueError):
                    module.upload_atomic_maintenance_questions(changed, "fixture", dry_run=True)
                init.assert_not_called()

    def test_zero_suggestions_do_not_relax_old_atomic_or_legacy_masks(self):
        payload, _ = self.fixture()
        for index in (0, 2, 3):
            changed = deepcopy(payload)
            changed["questions"][index].update(suggestedQuestions=[], suggestedQuestionDetails=[])
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.upload(changed, dry_run=True)
        legacy = {"questions": payload["questions"], "writeFields": ["suggestedQuestions", "suggestedQuestionDetails"]}
        legacy["questions"][0].update(suggestedQuestions=[], suggestedQuestionDetails=[])
        with self.assertRaises(ValueError):
            self.upload(legacy, dry_run=True)

    def test_replacement_cannot_change_legal_provenance_or_inactive_flags(self):
        payload, live = self.replacement_fixture()
        for field, value in (("auditedAt", "changed"), ("auditStatus", "updated_to_current_law")):
            changed = deepcopy(payload)
            changed["questions"][2]["lawRevisionFacts"][field] = value
            db = self.Database(live, self.before_stamp)
            with self.subTest(field=field), self.assertRaises((ValueError, RuntimeError)):
                self.upload(changed, db)
            self.assertEqual((db.batches, db.commits, db.applied), (0, 0, []))
        for field in ("isDeleted", "isChoiceOnly"):
            changed_live = deepcopy(live)
            changed_live[self.ids[0]][field] = True
            changed = deepcopy(payload)
            changed["expectedLiveFingerprint"] = module.firestore_live_fingerprint(self.ids, changed_live)
            db = self.Database(changed_live, self.before_stamp)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "live flags"):
                self.upload(changed, db)
            self.assertEqual((db.batches, db.commits), (0, 0))

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
                     ["suggestedQuestionDetails", "suggestedQuestions"], ["unknown"]):
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


class ReadSetMaintenanceUploadTests(unittest.TestCase):
    """Public transaction API fixture: every read participates in commit CAS."""
    def fixture(self):
        old, live = AtomicMaintenanceUploadTests().fixture()
        qid = old['questions'][3]['questionId']
        for question in old['questions']: question['examYear'] = 2024
        for values in live.values(): values['examYear'] = 2024
        payload = {'schemaVersion': module.READ_SET_MAINTENANCE_SCHEMA,
            'originalQuestionId': old['originalQuestionId'], 'qualificationId': 'gas-fixture', 'examYear': 2024,
            'readDocumentIds': list(AtomicMaintenanceUploadTests.ids),
            'questions': [deepcopy(old['questions'][3])], 'writeFieldsByDocument': {qid: ['explanationText']},
            'expectedReadUpdateTimes': old['expectedUpdateTimes'], 'expectedLiveFingerprint': module.firestore_live_fingerprint(old['expectedUpdateTimes'].keys(), live)}
        return payload, live

    class Database:
        def __init__(self, live, *, mode=None):
            self.live = deepcopy(live)
            self.times = {i: DatetimeWithNanoseconds.from_rfc3339(AtomicMaintenanceUploadTests.before_stamp) for i in live}
            self.mode = mode; self.reads = []; self.updates = []; self.applied = []; self.commits = 0
            self.transactions = []; self.read_transform = None; self.on_read = None; self.filters = {}

        def collection(self, name):
            if name != 'questions': raise AssertionError('unexpected collection')
            return self

        def document(self, identity): return identity
        def where(self, *, filter):
            self.filters[filter.field_path] = filter.value
            return self
        def select(self, fields): return ('membership', fields, deepcopy(self.filters))
        def snapshot(self, identity, fields=None):
            values = deepcopy(self.live.get(identity))
            if values is not None and fields is not None: values = {k: v for k, v in values.items() if k in fields}
            return SimpleNamespace(id=identity, exists=identity in self.live, update_time=self.times.get(identity),
                                   to_dict=lambda: deepcopy(values))

        def get_all(self, refs, field_paths, *, transaction):
            self.reads.append((list(refs), tuple(field_paths), transaction))
            transaction.read_versions.update({i: self.times.get(i) for i in refs})
            values = [self.snapshot(i, field_paths) for i in reversed(refs)]
            if self.on_read: self.on_read()
            return self.read_transform(values) if self.read_transform else values

        def transaction(self, *, max_attempts):
            if max_attempts != 1: raise AssertionError('automatic retries forbidden')
            database = self
            class Transaction:
                def __init__(self):
                    self.read_versions = {}; self.members = set(); self.write_results = []
                def get(self, query):
                    if query[0] != 'membership': raise AssertionError('unexpected transaction read')
                    self.query_filters = query[2]
                    self.members = {i for i, values in database.live.items() if all(values.get(k) == v for k, v in self.query_filters.items())}
                    self.read_versions.update({i: database.times[i] for i in self.members})
                    return [database.snapshot(i, query[1]) for i in self.members]
                def update(self, ref, data, *, option):
                    database.updates.append((ref, deepcopy(data), option))
                def create(self, *args, **kwargs): raise AssertionError('creates forbidden')
                def set(self, *args, **kwargs): raise AssertionError('full writes forbidden')
                def finish(self):
                    database.commits += 1
                    if database.mode == 'readonly-conflict':
                        readonly = next(i for i in self.members if i not in {r for r, _, _ in database.updates})
                        database.times[readonly] = DatetimeWithNanoseconds.from_rfc3339('2026-10-16T00:00:00.123456790Z')
                    if database.mode == 'new-sibling':
                        database.live['new'] = deepcopy(next(iter(database.live.values())))
                        database.times['new'] = next(iter(database.times.values()))
                    if (any(database.times.get(i) is None or database.times[i].rfc3339() != stamp.rfc3339() for i, stamp in self.read_versions.items())
                            or self.members != {i for i, values in database.live.items() if all(values.get(k) == v for k, v in self.query_filters.items())}):
                        raise RuntimeError('transaction read-set conflict')
                    if database.mode == 'before-commit': raise RuntimeError('unavailable before commit')
                    for ref, data, option in database.updates:
                        if option._last_update_time.rfc3339() != database.times[ref].rfc3339(): raise RuntimeError('write CAS conflict')
                        database.live[ref].update(data)
                        database.times[ref] = DatetimeWithNanoseconds.from_rfc3339(AtomicMaintenanceUploadTests.after_stamp)
                        database.applied.append(ref)
                        self.write_results.append(SimpleNamespace(update_time=database.times[ref]))
                    if database.mode == 'after-commit': raise RuntimeError('response lost')
                    if database.mode == 'wrong-count': self.write_results = []
                    if database.mode == 'no-time': self.write_results = [SimpleNamespace(update_time=None)]
                    if database.mode == 'bad-time': self.write_results = [SimpleNamespace(update_time=SimpleNamespace(rfc3339=lambda: 'bad'))]
            txn = Transaction(); self.transactions.append(txn); return txn

    @staticmethod
    def transactional(function):
        def wrapped(transaction):
            result = function(transaction)
            transaction.finish()
            return result
        return wrapped

    def upload(self, payload, database=None, *, dry_run=False):
        with patch.object(module, 'init_firestore', return_value=database), patch.object(module.firestore, 'transactional', self.transactional):
            return module.upload_read_set_maintenance_questions(payload, 'read-set fixture', dry_run=dry_run)

    def test_one_explanation_write_five_transactional_reads_preserves_every_readonly_value_and_version(self):
        payload, live = self.fixture(); db = self.Database(live)
        result = self.upload(payload, db)
        self.assertEqual(result['schemaVersion'], module.READ_SET_MAINTENANCE_RESULT_SCHEMA)
        self.assertEqual((len(db.transactions), db.commits, len(db.updates)), (1, 1, 1))
        self.assertEqual(result['readDocumentIds'], payload['readDocumentIds'])
        self.assertEqual(result['writeDocumentIds'], [payload['questions'][0]['questionId']])
        self.assertEqual(result['status'], 'committed'); self.assertFalse(result['publicationConfirmed'])
        qid = result['writeDocumentIds'][0]
        self.assertEqual(result['writeResults'], [{'questionId': qid, 'updateTime': AtomicMaintenanceUploadTests.after_stamp}])
        for identity in payload['readDocumentIds']:
            if identity != qid:
                self.assertEqual(db.live[identity], live[identity])
                self.assertEqual(db.times[identity].rfc3339(), AtomicMaintenanceUploadTests.before_stamp)
            else:
                self.assertEqual(set(db.updates[0][1]), {'explanationText', 'updatedAt', 'updatedById'})
                self.assertEqual({k: v for k, v in db.live[qid].items() if k not in db.updates[0][1]},
                                 {k: v for k, v in live[qid].items() if k not in db.updates[0][1]})
        self.assertEqual(set(db.reads[0][0]), set(payload['readDocumentIds']))
        self.assertEqual(db.reads[0][2], db.transactions[0])
        self.assertEqual(db.reads[0][1], module.EXISTING_DOC_FIELD_PATHS)

    def test_dry_run_and_distinct_schema_never_initialize_firestore(self):
        payload, _ = self.fixture()
        with patch.object(module, 'init_firestore') as init:
            result = module.upload_read_set_maintenance_questions(payload, 'fixture', dry_run=True)
            init.assert_not_called()
        self.assertEqual(result['status'], 'dry_run')
        with self.assertRaises(ValueError): module.resolve_atomic_maintenance_contract(payload, 'fixture')

    def test_unknown_missing_duplicate_and_unsupported_masks_are_rejected(self):
        payload, live = self.fixture(); qid = payload['questions'][0]['questionId']
        for mode in ('unknown', 'missing-time', 'duplicate-read', 'duplicate-write', 'outside-write', 'old-mask', 'extra-mask', 'empty-writes', 'bad-time', 'missing-year', 'wrong-year'):
            changed = deepcopy(payload)
            if mode == 'unknown': changed['writeFields'] = ['explanationText']
            elif mode == 'missing-time': del changed['expectedReadUpdateTimes'][changed['readDocumentIds'][0]]
            elif mode == 'duplicate-read': changed['readDocumentIds'].append(changed['readDocumentIds'][0])
            elif mode == 'duplicate-write': changed['questions'].append(deepcopy(changed['questions'][0]))
            elif mode == 'outside-write': changed['questions'][0]['questionId'] = 'outside'
            elif mode == 'old-mask': changed['writeFieldsByDocument'][qid] = ['explanationText', 'suggestedQuestionDetails']
            elif mode == 'extra-mask': changed['writeFieldsByDocument']['outside'] = ['explanationText']
            elif mode == 'empty-writes': changed['questions'] = []
            elif mode == 'missing-year': del changed['examYear']
            elif mode == 'wrong-year': changed['questions'][0]['examYear'] = 2023
            else: changed['expectedReadUpdateTimes'][changed['readDocumentIds'][0]] = 'invalid'
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.upload(changed, self.Database(live))

    def test_noop_presence_snapshot_scope_and_nanosecond_conflicts_prevent_any_update(self):
        payload, live = self.fixture(); qid = payload['questions'][0]['questionId']
        for mode in ('noop', 'absent', 'missing', 'extra', 'duplicate', 'nanosecond', 'flags', 'qualification', 'outside-value'):
            changed = deepcopy(payload); db = self.Database(live)
            if mode == 'noop': changed['questions'][0]['explanationText'] = live[qid]['explanationText']
            elif mode == 'absent':
                del db.live[qid]['explanationText']; changed['expectedLiveFingerprint'] = module.firestore_live_fingerprint(changed['readDocumentIds'], db.live)
            elif mode == 'missing': db.read_transform = lambda values: values[:-1]
            elif mode == 'extra': db.read_transform = lambda values: values + [db.snapshot('unknown')]
            elif mode == 'duplicate': db.read_transform = lambda values: values + [values[0]]
            elif mode == 'nanosecond': db.times[changed['readDocumentIds'][0]] = DatetimeWithNanoseconds.from_rfc3339('2026-10-16T00:00:00.123456788Z')
            elif mode == 'flags': db.live[qid]['isDeleted'] = True
            elif mode == 'qualification': db.live[qid]['qualificationId'] = 'other'
            else: db.live[changed['readDocumentIds'][0]]['questionText'] = 'third value'
            with self.subTest(mode=mode), self.assertRaises((RuntimeError, ValueError)): self.upload(changed, db)
            self.assertEqual((db.commits, db.updates, db.applied), (0, [], []))

    def test_active_membership_subset_and_new_sibling_conflicts_fail(self):
        payload, live = self.fixture()
        changed = deepcopy(payload); omitted = changed['readDocumentIds'].pop()
        changed['expectedReadUpdateTimes'].pop(omitted)
        changed['expectedLiveFingerprint'] = module.firestore_live_fingerprint(changed['readDocumentIds'], live)
        db = self.Database(live)
        with self.assertRaisesRegex(RuntimeError, 'membership'): self.upload(changed, db)
        self.assertEqual((db.commits, db.updates), (0, []))
        for mode in ('readonly-conflict', 'new-sibling'):
            db = self.Database(live, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as error:
                self.upload(payload, db)
            self.assertEqual(db.applied, [])
            self.assertEqual(error.exception.prepared_result['status'], 'commit_unconfirmed')

    def test_uncertain_commit_response_is_never_success(self):
        payload, live = self.fixture()
        for mode in ('before-commit', 'after-commit', 'wrong-count', 'no-time', 'bad-time'):
            db = self.Database(live, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as error:
                self.upload(payload, db)
            self.assertFalse(error.exception.prepared_result['publicationConfirmed'])
            self.assertEqual(error.exception.prepared_result['writeResults'], [])

    def test_owned_request_is_immune_to_mutation_during_read(self):
        payload, live = self.fixture(); before = deepcopy(payload); db = self.Database(live)
        db.on_read = lambda: payload['questions'][0].update(explanationText='unapproved')
        self.upload(payload, db)
        qid = before['questions'][0]['questionId']
        self.assertEqual(db.live[qid]['explanationText'], before['questions'][0]['explanationText'])

    def test_subset_three_field_replacement_reuses_exact_mirror_contract(self):
        payload, live = self.fixture(); question = payload['questions'][0]
        question['suggestedQuestions'] = ['比較の観点1', '比較の観点2', '比較の観点3']
        question['suggestedQuestionDetails'] = [{'question': q, 'answer': '根拠と対象の違いを確認する。'} for q in question['suggestedQuestions']]
        payload['writeFieldsByDocument'][question['questionId']] = ['explanationText', 'suggestedQuestions', 'suggestedQuestionDetails']
        db = self.Database(live)
        result = self.upload(payload, db)
        self.assertEqual(result['writeDocumentIds'], [question['questionId']])
        self.assertEqual(len(db.updates), 1)
        self.assertEqual(set(db.updates[0][1]), {'explanationText', 'suggestedQuestions', 'suggestedQuestionDetails', 'updatedAt', 'updatedById'})
        for identity in result['readOnlyDocumentIds']:
            self.assertEqual(db.live[identity], live[identity])
            self.assertEqual(db.times[identity].rfc3339(), AtomicMaintenanceUploadTests.before_stamp)

    def test_three_field_replacement_rejects_padding_wrong_mirror_and_unsupported_extra_fields(self):
        payload, live = self.fixture(); question = payload['questions'][0]
        question['suggestedQuestions'] = ['新補足']; question['suggestedQuestionDetails'] = [{'question': '新補足', 'answer': '新しい回答。'}]
        qid = question['questionId']; payload['writeFieldsByDocument'][qid] = ['explanationText', 'suggestedQuestions', 'suggestedQuestionDetails']
        for mode in ('mirror', 'count', 'nonstrings', 'padding', 'extra-mask'):
            changed = deepcopy(payload); q = changed['questions'][0]; db = self.Database(live)
            if mode == 'mirror': q['suggestedQuestions'] = ['別の補足']
            elif mode == 'count':
                q['suggestedQuestions'] *= 4; q['suggestedQuestionDetails'] *= 4
            elif mode == 'nonstrings': q['suggestedQuestionDetails'][0]['answer'] = 42
            elif mode == 'padding':
                q['suggestedQuestions'] = live[qid]['suggestedQuestions']
                q['suggestedQuestionDetails'] = [{'question': item, 'answer': 'new answer'} for item in q['suggestedQuestions']]
            else: changed['writeFieldsByDocument'][qid].append('lawRevisionFacts')
            with self.subTest(mode=mode), self.assertRaises((ValueError, RuntimeError)): self.upload(changed, db)
            self.assertEqual((db.commits, db.applied), (0, []))

    def test_same_original_in_other_qualification_or_year_is_not_added_to_the_read_set(self):
        payload, live = self.fixture()
        other = deepcopy(next(iter(live.values())))
        live['other-qualification'] = {**other, 'qualificationId': 'another-qualification'}
        live['other-year'] = {**other, 'examYear': 2023}
        db = self.Database(live)
        result = self.upload(payload, db)
        self.assertEqual(result['readDocumentIds'], payload['readDocumentIds'])
        self.assertNotIn('other-qualification', db.reads[0][0])
        self.assertNotIn('other-year', db.reads[0][0])
        self.assertEqual(db.filters, {'originalQuestionId': payload['originalQuestionId']})
        self.assertEqual(db.live['other-qualification'], live['other-qualification'])
        self.assertEqual(db.live['other-year'], live['other-year'])

    def test_partially_noop_write_subset_is_rejected_before_queueing_any_update(self):
        payload, live = self.fixture()
        changed = deepcopy(payload['questions'][0])
        second = next(i for i in payload['readDocumentIds'] if i != changed['questionId'])
        payload['questions'].append({**deepcopy(live[second]), 'questionId': second})
        payload['writeFieldsByDocument'][second] = ['explanationText']
        db = self.Database(live)
        with self.assertRaisesRegex(RuntimeError, 'unchanged/no-op'):
            self.upload(payload, db)
        self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_ambiguous_active_membership_unit_is_rejected_instead_of_silently_filtered(self):
        payload, live = self.fixture()
        live['ambiguous'] = deepcopy(next(iter(live.values())))
        del live['ambiguous']['examYear']
        db = self.Database(live)
        with self.assertRaises(ValueError): self.upload(payload, db)
        self.assertEqual((db.updates, db.commits), ([], 0))


class ReadSetDisplayUploadTests(unittest.TestCase):
    Database = ReadSetMaintenanceUploadTests.Database
    transactional = staticmethod(ReadSetMaintenanceUploadTests.transactional)
    upload = ReadSetMaintenanceUploadTests.upload

    def fixture(self, mask=None):
        mask = mask or module.DISPLAY_WRITE_FIELDS
        payload, raw = ReadSetMaintenanceUploadTests().fixture()
        live = {}
        for identity, values in raw.items():
            values.update(questionType='true_false', originalQuestionBodyText='誤っているものはいくつあるか。',
                          originalQuestionChoiceText=identity + 'の原記述。', questionBodyText='誤っているものはいくつあるか。')
            values['questionText'] = values['questionBodyText'] + '[quote]' + values['originalQuestionChoiceText'] + '[/quote]'
            live[identity] = module.build_doc_data_base(values)
        payload['schemaVersion'] = module.READ_SET_DISPLAY_SCHEMA
        payload['questions'] = []
        payload['writeFieldsByDocument'] = {}
        for identity in payload['readDocumentIds']:
            row = {'questionId': identity, **deepcopy(live[identity])}
            row['questionBodyText'] = '次の記述の正誤を判断してください。'
            row['questionText'] = row['questionBodyText'] + '[quote]' + row['originalQuestionChoiceText'] + '[/quote]'
            if 'explanationText' in mask: row['explanationText'] = '正しい。新しい根拠。'
            if 'suggestedQuestions' in mask:
                row['suggestedQuestions'] = ['新しい補足']
                row['suggestedQuestionDetails'] = [{'question': '新しい補足', 'answer': '新しい回答'}]
            payload['questions'].append(row)
            payload['writeFieldsByDocument'][identity] = list(mask)
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        return payload, live

    def test_four_exact_display_masks_preserve_originals_ids_truth_and_every_outside_field(self):
        for mask in module.READ_SET_DISPLAY_WRITE_FIELD_SETS:
            with self.subTest(mask=mask):
                payload, live = self.fixture(mask); db = self.Database(live)
                result = self.upload(payload, db)
                self.assertEqual(result['schemaVersion'], module.READ_SET_DISPLAY_RESULT_SCHEMA)
                self.assertEqual(result['writeDocumentIds'], payload['readDocumentIds'])
                self.assertEqual(len(result['writeResults']), 5)
                for identity, written, _ in db.updates:
                    self.assertEqual(set(written), {*mask, 'updatedAt', 'updatedById'})
                    self.assertEqual({f: v for f, v in db.live[identity].items() if f not in written},
                                     {f: v for f, v in live[identity].items() if f not in written})

    def test_bad_display_shape_profile_noop_and_non_owned_changes_never_queue_writes(self):
        mutations = ('v1', 'unknown', 'one-field', 'extra-mask', 'no-body', 'no-original', 'no-quote',
                     'other-quote', 'different-body', 'type', 'truth', 'original', 'choice', 'category',
                     'set', 'law', 'id', 'noop', 'partial-noop', 'missing-live-body', 'current-quote')
        for mutation in mutations:
            payload, live = self.fixture(); first, other = payload['questions'][:2]; qid = first['questionId']
            if mutation == 'v1': payload['schemaVersion'] = module.READ_SET_MAINTENANCE_SCHEMA
            elif mutation == 'unknown': payload['schemaVersion'] = 'question-maintenance-read-set-upload/v999'
            elif mutation == 'one-field': payload['writeFieldsByDocument'][qid] = ['questionBodyText']
            elif mutation == 'extra-mask': payload['writeFieldsByDocument'][qid].append('correctChoiceText')
            elif mutation == 'no-body': del first['questionBodyText']
            elif mutation == 'no-original': del first['originalQuestionBodyText']
            elif mutation == 'no-quote': first['questionText'] = first['questionBodyText']
            elif mutation == 'other-quote': first['questionText'] = other['questionText']
            elif mutation == 'different-body':
                first['questionBodyText'] = '異なる本文'
                first['questionText'] = '異なる本文[quote]' + first['originalQuestionChoiceText'] + '[/quote]'
            elif mutation == 'type': first['questionType'] = 'group_choice'
            elif mutation == 'truth': first['correctChoiceText'] = '間違い'
            elif mutation == 'original': first['originalQuestionBodyText'] = '偽造した原題幹'
            elif mutation == 'choice':
                first['originalQuestionChoiceText'] = '偽造原記述'
                first['questionText'] = first['questionBodyText'] + '[quote]偽造原記述[/quote]'
            elif mutation == 'category': first['questionTags'] = ['new-category']
            elif mutation == 'set': first['questionSetId'] = 'new-set'
            elif mutation == 'law': first['lawReferences'] = [{'law': 'new'}]
            elif mutation == 'id': first['questionId'] = 'foreign'
            elif mutation in ('noop', 'partial-noop'):
                for row in payload['questions'][:5 if mutation == 'noop' else 1]:
                    row.update({f: live[row['questionId']][f] for f in module.DISPLAY_WRITE_FIELDS})
            elif mutation == 'missing-live-body': del live[qid]['questionBodyText']
            elif mutation == 'current-quote': live[qid]['questionText'] = 'broken current quote'
            payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
            db = self.Database(live)
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                self.upload(payload, db)
            self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_readonly_sibling_nanosecond_conflict_and_unconfirmed_commit_keep_distinct_v2_evidence(self):
        payload, live = self.fixture()
        # All five body changes are real; omit one only after its exact proposed
        # display is already present, so the read-only sibling is still checked.
        readonly = payload['questions'].pop()
        identity = readonly['questionId']; payload['writeFieldsByDocument'].pop(identity)
        live[identity].update({f: readonly[f] for f in module.DISPLAY_WRITE_FIELDS})
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        # A mixed before-body set is itself inconsistent and must be held.
        with self.assertRaises(ValueError): self.upload(payload, self.Database(live))
        for mode in ('before-commit', 'after-commit', 'wrong-count', 'bad-time', 'new-sibling'):
            payload, live = self.fixture(); db = self.Database(live, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as caught:
                self.upload(payload, db)
            self.assertEqual(caught.exception.prepared_result['schemaVersion'], module.READ_SET_DISPLAY_RESULT_SCHEMA)
            self.assertEqual(caught.exception.prepared_result['status'], 'commit_unconfirmed')
            self.assertEqual(caught.exception.prepared_result['writeResults'], [])
        payload, live = self.fixture(); db = self.Database(live)
        db.times[payload['readDocumentIds'][-1]] = DatetimeWithNanoseconds.from_rfc3339('2026-10-16T00:00:00.123456790Z')
        with self.assertRaises(RuntimeError): self.upload(payload, db)
        self.assertEqual(db.updates, [])


class ReadSetTFTruthUploadTests(unittest.TestCase):
    """The v3 correction joins display and proposition truth under sibling CAS."""
    Database = ReadSetMaintenanceUploadTests.Database
    transactional = staticmethod(ReadSetMaintenanceUploadTests.transactional)
    upload = ReadSetMaintenanceUploadTests.upload

    def fixture(self, mask=None):
        mask = mask or module.TF_TRUTH_WRITE_FIELDS
        payload, live = ReadSetDisplayUploadTests().fixture()
        payload['schemaVersion'] = module.READ_SET_TF_TRUTH_SCHEMA
        for index, row in enumerate(payload['questions']):
            identity = row['questionId']
            live[identity]['correctChoiceText'] = '正しい'
            row['correctChoiceText'] = '間違い' if index == 0 and 'explanationText' in mask else '正しい'
            row['explanationText'] = row['correctChoiceText'] + '。' + (
                '新しい根拠。' if 'explanationText' in mask else '元の説明。')
            if 'suggestedQuestions' in mask:
                row['suggestedQuestions'] = []
                row['suggestedQuestionDetails'] = []
            payload['writeFieldsByDocument'][identity] = list(mask)
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        return payload, live

    def test_all_v3_masks_couple_display_truth_and_preserve_every_outside_value_and_presence(self):
        for mask in module.READ_SET_TF_TRUTH_WRITE_FIELD_SETS:
            with self.subTest(mask=mask):
                payload, live = self.fixture(mask)
                # Private fields are outside transport projection, but update()
                # must retain them, original fields, absent optional fields and IDs.
                for values in live.values():
                    values['privateUnchangedField'] = {'kept': True}
                db = self.Database(live)
                result = self.upload(payload, db)
                self.assertEqual(result['schemaVersion'], module.READ_SET_TF_TRUTH_RESULT_SCHEMA)
                self.assertEqual((db.commits, len(db.updates)), (1, 5))
                self.assertFalse(result['publicationConfirmed'])
                self.assertEqual(len(result['writeResults']), 5)
                for identity, values, option in db.updates:
                    self.assertEqual(set(values), {*mask, 'updatedAt', 'updatedById'})
                    self.assertEqual(option._last_update_time.rfc3339(), AtomicMaintenanceUploadTests.before_stamp)
                    self.assertEqual({k: v for k, v in db.live[identity].items() if k not in values},
                                     {k: v for k, v in live[identity].items() if k not in values})

    def test_display_profiles_preserve_absent_unowned_legacy_list_group(self):
        for factory in (ReadSetDisplayUploadTests, ReadSetTFTruthUploadTests, ReadSetTFTruthInitializeUploadTests):
            with self.subTest(profile=factory.__name__):
                payload, live = factory().fixture()
                for values in live.values(): values.pop('listGroupId', None)
                for row in payload['questions']: row.pop('listGroupId', None)
                payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
                db = self.Database(live)
                self.assertEqual(self.upload(payload, db)['status'], 'committed')
                self.assertEqual(db.commits, 1)
                for identity, update, _ in db.updates:
                    self.assertNotIn('listGroupId', update)
                    self.assertNotIn('listGroupId', db.live[identity])

    def test_explicit_nonowned_legacy_presence_changes_are_still_rejected_before_queue(self):
        for mutation in ('add-empty', 'add-value', 'omit-existing', 'change-null'):
            with self.subTest(mutation=mutation):
                payload, live = self.fixture(); row = payload['questions'][0]; qid = row['questionId']
                if mutation.startswith('add'):
                    live[qid].pop('listGroupId', None)
                    row['listGroupId'] = '' if mutation == 'add-empty' else 'new-group'
                elif mutation == 'omit-existing': row.pop('listGroupId')
                else:
                    live[qid]['listGroupId'] = None
                    row['listGroupId'] = ''
                payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
                db = self.Database(live)
                with self.assertRaisesRegex(RuntimeError, 'non-owned live field/presence'):
                    self.upload(payload, db)
                self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_v3_explicit_dry_run_never_initializes_sdk_and_old_profiles_reject_truth(self):
        payload, live = self.fixture()
        with patch.object(module, 'init_firestore') as init:
            result = module.upload_read_set_maintenance_questions(payload, 'fixture', dry_run=True)
            init.assert_not_called()
        self.assertEqual(result['schemaVersion'], module.READ_SET_TF_TRUTH_RESULT_SCHEMA)
        self.assertEqual(result['status'], 'dry_run')
        self.assertFalse(result['publicationConfirmed'])
        for schema in (module.READ_SET_MAINTENANCE_SCHEMA, module.READ_SET_DISPLAY_SCHEMA):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.upload({**payload, 'schemaVersion': schema}, self.Database(live))

    def test_strict_canonical_truth_masks_and_candidate_identity_rejected_before_writes(self):
        mutations = ('true', 'space', 'bool', 'integer', 'missing-truth', 'truth-only', 'display-only',
                     'one-suggestion-mirror', 'extra-original-mask', 'original-body', 'original-choice',
                     'category', 'missing-optional-presence', 'explanation-mismatch', 'candidate-type',
                     'duplicate-write', 'unknown-id', 'duplicate-read', 'missing-read-time')
        for mutation in mutations:
            payload, live = self.fixture(); first = payload['questions'][0]; identity = first['questionId']
            if mutation in ('true', 'space', 'bool', 'integer'):
                first['correctChoiceText'] = {'true': 'true', 'space': ' 正しい', 'bool': True, 'integer': 1}[mutation]
            elif mutation == 'missing-truth': del first['correctChoiceText']
            elif mutation == 'truth-only': payload['writeFieldsByDocument'][identity] = ['correctChoiceText']
            elif mutation == 'display-only': payload['writeFieldsByDocument'][identity] = list(module.DISPLAY_WRITE_FIELDS)
            elif mutation == 'one-suggestion-mirror': payload['writeFieldsByDocument'][identity].append('suggestedQuestions')
            elif mutation == 'extra-original-mask': payload['writeFieldsByDocument'][identity].append('originalQuestionBodyText')
            elif mutation == 'original-body': first['originalQuestionBodyText'] = '別の原文。'
            elif mutation == 'original-choice':
                first['originalQuestionChoiceText'] = '別の原記述。'
                first['questionText'] = first['questionBodyText'] + '[quote]別の原記述。[/quote]'
            elif mutation == 'category': first['questionTags'] = ['other']
            elif mutation == 'missing-optional-presence': del first['suggestedQuestionDetails']
            elif mutation == 'explanation-mismatch': first['correctChoiceText'] = '間違い'
            elif mutation == 'candidate-type': first['questionType'] = 'group_choice'
            elif mutation == 'duplicate-write': payload['questions'].append(deepcopy(first))
            elif mutation == 'unknown-id': first['questionId'] = 'outside'
            elif mutation == 'duplicate-read': payload['readDocumentIds'].append(identity)
            elif mutation == 'missing-read-time': del payload['expectedReadUpdateTimes'][identity]
            db = self.Database(live)
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                self.upload(payload, db)
            self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_changed_write_subset_reads_all_siblings_and_checks_readonly_tf_and_originals(self):
        payload, live = self.fixture()
        # Repair only truth on one document whose neutral display is already
        # correct; every other sibling participates as a read-only target.
        for row in payload['questions']:
            identity = row['questionId']
            live[identity].update({f: row[f] for f in module.DISPLAY_WRITE_FIELDS})
        first = payload['questions'][0]
        first['correctChoiceText'] = '間違い'; first['explanationText'] = '間違い。新しい根拠。'
        payload['questions'] = [first]
        payload['writeFieldsByDocument'] = {first['questionId']: [*module.TF_TRUTH_WRITE_FIELDS, 'explanationText']}
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        db = self.Database(live)
        result = self.upload(payload, db)
        self.assertEqual(result['readOnlyDocumentIds'], payload['readDocumentIds'][1:])
        self.assertEqual((db.commits, len(db.updates)), (1, 1))
        self.assertEqual(set(db.reads[0][0]), set(payload['readDocumentIds']))
        for identity in result['readOnlyDocumentIds']:
            self.assertEqual(db.live[identity], live[identity])
            self.assertEqual(db.times[identity].rfc3339(), AtomicMaintenanceUploadTests.before_stamp)
        db = self.Database(live, mode='readonly-conflict')
        with self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as caught:
            self.upload(payload, db)
        self.assertEqual(caught.exception.prepared_result['schemaVersion'], module.READ_SET_TF_TRUTH_RESULT_SCHEMA)
        self.assertFalse(caught.exception.prepared_result['publicationConfirmed'])
        self.assertEqual((db.commits, db.applied), (1, []))
        for mutation in ('type', 'original', 'absent-original', 'truth', 'explanation'):
            changed_live = deepcopy(live); readonly = payload['readDocumentIds'][-1]
            if mutation == 'type': changed_live[readonly]['questionType'] = 'group_choice'
            elif mutation == 'original': changed_live[readonly]['originalQuestionBodyText'] = '異なる原文'
            elif mutation == 'absent-original': del changed_live[readonly]['originalQuestionChoiceText']
            elif mutation == 'truth': changed_live[readonly]['correctChoiceText'] = 'true'
            else: changed_live[readonly]['explanationText'] = '間違い。矛盾。'
            changed = {**payload, 'expectedLiveFingerprint': module.firestore_live_fingerprint(payload['readDocumentIds'], changed_live)}
            db = self.Database(changed_live)
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                self.upload(changed, db)
            self.assertEqual(db.updates, [])

    def test_missing_extra_membership_fingerprint_and_noop_are_held_without_writes(self):
        for mutation in ('missing', 'extra', 'duplicate', 'fingerprint', 'noop'):
            payload, live = self.fixture(); db = self.Database(live)
            if mutation == 'missing': db.read_transform = lambda rows: rows[:-1]
            elif mutation == 'extra':
                db.live['outside'] = deepcopy(next(iter(live.values())))
                db.times['outside'] = next(iter(db.times.values()))
            elif mutation == 'duplicate': db.read_transform = lambda rows: rows + [rows[0]]
            elif mutation == 'fingerprint': payload['expectedLiveFingerprint'] = '0' * 64
            else:
                row = payload['questions'][0]
                row.update({f: live[row['questionId']][f] for f in module.TF_TRUTH_WRITE_FIELDS})
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                self.upload(payload, db)
            self.assertEqual((db.updates, db.commits), ([], 0))

    def test_commit_conflict_interruption_unknown_commit_and_stale_replay_do_not_confirm_publication(self):
        for mode in ('before-commit', 'after-commit', 'wrong-count', 'no-time', 'bad-time', 'new-sibling'):
            payload, live = self.fixture(); db = self.Database(live, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as caught:
                self.upload(payload, db)
            receipt = caught.exception.prepared_result
            self.assertEqual(receipt['schemaVersion'], module.READ_SET_TF_TRUTH_RESULT_SCHEMA)
            self.assertEqual(receipt['status'], 'commit_unconfirmed')
            self.assertEqual(receipt['writeResults'], [])
            self.assertFalse(receipt['publicationConfirmed'])
            self.assertEqual((len(db.transactions), db.commits), (1, 1))
            if mode == 'after-commit':
                self.assertEqual(len(db.applied), 5)
                db.mode = None
                with self.assertRaisesRegex(RuntimeError, 'update_time changed'):
                    self.upload(payload, db)
                self.assertEqual(db.commits, 1)
        payload, live = self.fixture(); db = self.Database(live)
        self.upload(payload, db)
        with self.assertRaisesRegex(RuntimeError, 'update_time changed'):
            self.upload(payload, db)
        self.assertEqual(db.commits, 1)
        rebound = {**payload,
                   'expectedReadUpdateTimes': {identity: db.times[identity].rfc3339() for identity in payload['readDocumentIds']},
                   'expectedLiveFingerprint': module.firestore_live_fingerprint(payload['readDocumentIds'], db.live)}
        with self.assertRaisesRegex(RuntimeError, 'unchanged/no-op'):
            self.upload(rebound, db)
        self.assertEqual(db.commits, 1)


class ReadSetTFTruthInitializeUploadTests(unittest.TestCase):
    """v4 grants only explicit absent questionTags -> [] in the v3 transaction."""
    Database = ReadSetMaintenanceUploadTests.Database
    transactional = staticmethod(ReadSetMaintenanceUploadTests.transactional)
    upload = ReadSetMaintenanceUploadTests.upload

    def fixture(self, mask=None, *, initialize_count=5):
        payload, live = ReadSetTFTruthUploadTests().fixture(mask)
        payload['schemaVersion'] = module.READ_SET_TF_TRUTH_INITIALIZE_SCHEMA
        initialize_ids = payload['readDocumentIds'][:initialize_count]
        payload['initializeMissingFieldsByDocument'] = {i: ['questionTags'] for i in initialize_ids}
        for row in payload['questions']:
            identity = row['questionId']
            if identity in initialize_ids:
                del live[identity]['questionTags']
                row['questionTags'] = []
                payload['writeFieldsByDocument'][identity].append('questionTags')
            else:
                live[identity]['questionTags'] = ['既存の分類']
                row['questionTags'] = ['既存の分類']
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        return payload, live

    def test_v4_all_masks_initialize_only_absent_tags_through_same_guarded_transaction(self):
        for mask in module.READ_SET_TF_TRUTH_WRITE_FIELD_SETS:
            with self.subTest(mask=mask):
                payload, live = self.fixture(mask); db = self.Database(live)
                before = deepcopy(payload)
                with patch.object(module, 'add_guarded_question_write', wraps=module.add_guarded_question_write) as guarded:
                    result = self.upload(payload, db)
                self.assertEqual(payload, before)
                self.assertEqual(guarded.call_count, 5)
                self.assertEqual(result['schemaVersion'], module.READ_SET_TF_TRUTH_INITIALIZE_RESULT_SCHEMA)
                self.assertEqual(result['initializeMissingFieldsByDocument'], payload['initializeMissingFieldsByDocument'])
                self.assertEqual(result['status'], 'committed'); self.assertFalse(result['publicationConfirmed'])
                self.assertEqual((len(db.transactions), db.commits, len(db.updates), len(result['writeResults'])), (1, 1, 5, 5))
                self.assertEqual(set(db.reads[0][0]), set(payload['readDocumentIds']))
                for identity, update, option in db.updates:
                    self.assertEqual(db.live[identity]['questionTags'], [])
                    self.assertEqual(set(update), {*mask, 'questionTags', 'updatedAt', 'updatedById'})
                    self.assertEqual(option._last_update_time.rfc3339(), AtomicMaintenanceUploadTests.before_stamp)
                    self.assertEqual({k: v for k, v in db.live[identity].items() if k not in update},
                                     {k: v for k, v in live[identity].items() if k not in update})

    def test_v4_dry_run_is_explicit_and_mixed_existing_tags_are_preserved(self):
        payload, live = self.fixture(initialize_count=2)
        with patch.object(module, 'init_firestore') as initialize:
            dry = module.upload_read_set_maintenance_questions(payload, 'fixture', dry_run=True)
            initialize.assert_not_called()
        self.assertEqual(dry['schemaVersion'], module.READ_SET_TF_TRUTH_INITIALIZE_RESULT_SCHEMA)
        self.assertEqual(dry['status'], 'dry_run')
        self.assertEqual(dry['initializeMissingFieldsByDocument'], payload['initializeMissingFieldsByDocument'])
        db = self.Database(live); result = self.upload(payload, db)
        for identity in payload['readDocumentIds'][2:]:
            self.assertEqual(db.live[identity]['questionTags'], ['既存の分類'])
            self.assertNotIn('questionTags', result['writeFieldsByDocument'][identity])
        for schema in (module.READ_SET_MAINTENANCE_SCHEMA, module.READ_SET_DISPLAY_SCHEMA, module.READ_SET_TF_TRUTH_SCHEMA):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.upload({**payload, 'schemaVersion': schema}, self.Database(live))
        old = deepcopy(payload); old['schemaVersion'] = module.READ_SET_TF_TRUTH_SCHEMA
        old.pop('initializeMissingFieldsByDocument')
        with self.assertRaises(ValueError): self.upload(old, self.Database(live))
        legacy, legacy_live = ReadSetTFTruthUploadTests().fixture()
        legacy_live[legacy['readDocumentIds'][0]].pop('questionTags')
        legacy['expectedLiveFingerprint'] = module.firestore_live_fingerprint(legacy['readDocumentIds'], legacy_live)
        with self.assertRaisesRegex(RuntimeError, 'non-owned live field/presence'):
            self.upload(legacy, self.Database(legacy_live))
        for fixture in (ReadSetMaintenanceUploadTests(), ReadSetDisplayUploadTests(), ReadSetTFTruthUploadTests()):
            legacy, _ = fixture.fixture()
            self.assertNotIn('initializeMissingFieldsByDocument', self.upload(legacy, dry_run=True))

    def test_v4_exact_initialization_authority_rejects_unknown_missing_foreign_and_nonempty_inputs(self):
        modes = ('missing-map', 'empty-map', 'foreign-id', 'readonly-id', 'empty-fields', 'other-field', 'extra-field',
            'duplicate-field', 'missing-mask', 'unauthorized-mask', 'wrong-order', 'missing-value', 'null-value',
            'nonempty-value', 'non-list-value', 'unknown-top', 'unknown-row-metadata', 'unknown-schema',
            'protected-original', 'other-created-field', 'duplicate-map-key')
        for mode in modes:
            payload, live = self.fixture(initialize_count=2); first = payload['questions'][0]; identity = first['questionId']
            init = payload['initializeMissingFieldsByDocument']
            if mode == 'missing-map': payload.pop('initializeMissingFieldsByDocument')
            elif mode == 'empty-map': payload['initializeMissingFieldsByDocument'] = {}
            elif mode == 'foreign-id': init['outside'] = ['questionTags']
            elif mode == 'readonly-id':
                payload['questions'] = [first]; payload['writeFieldsByDocument'] = {identity: payload['writeFieldsByDocument'][identity]}
            elif mode in ('empty-fields', 'other-field', 'extra-field', 'duplicate-field'):
                init[identity] = {'empty-fields': [], 'other-field': ['isOfficial'],
                    'extra-field': ['questionTags', 'knowledgeText'], 'duplicate-field': ['questionTags', 'questionTags']}[mode]
            elif mode == 'missing-mask': payload['writeFieldsByDocument'][identity].remove('questionTags')
            elif mode == 'unauthorized-mask': payload['writeFieldsByDocument'][payload['readDocumentIds'][-1]].append('questionTags')
            elif mode == 'wrong-order': payload['writeFieldsByDocument'][identity].reverse()
            elif mode == 'missing-value': first.pop('questionTags')
            elif mode == 'null-value': first['questionTags'] = None
            elif mode == 'nonempty-value': first['questionTags'] = ['追加は許されない']
            elif mode == 'non-list-value': first['questionTags'] = ()
            elif mode == 'unknown-top': payload['allowInitialize'] = True
            elif mode == 'unknown-row-metadata': first['initializeMissingFieldsByDocument'] = init
            elif mode == 'unknown-schema': payload['schemaVersion'] = 'question-maintenance-read-set-upload/v5'
            elif mode == 'protected-original': first['originalQuestionId'] = 'different-original'
            elif mode == 'other-created-field': payload['writeFieldsByDocument'][identity].append('knowledgeText')
            else:
                duplicate = module._UploadJSONObject([(identity, ['questionTags']), (identity, ['questionTags'])])
                payload['initializeMissingFieldsByDocument'] = duplicate
            db = self.Database(live)
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.upload(payload, db)
            self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_v4_rechecks_actual_absence_other_presence_and_noop_without_staging_writes(self):
        for value in (None, [], ['既存タグ'], False):
            payload, live = self.fixture(); identity = payload['questions'][0]['questionId']
            live[identity]['questionTags'] = value
            payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
            db = self.Database(live)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'actually absent'):
                self.upload(payload, db)
            self.assertEqual((db.updates, db.commits), ([], 0))
        for mode in ('masked-presence', 'outside-presence', 'noop-other-write', 'nanosecond', 'fingerprint'):
            payload, live = self.fixture(initialize_count=1); identity = payload['readDocumentIds'][0]
            if mode == 'masked-presence': live[identity].pop('questionBodyText')
            elif mode == 'outside-presence': live[identity].pop('questionSetId')
            elif mode == 'noop-other-write':
                row = payload['questions'][1]
                row.update({f: live[row['questionId']][f] for f in module.TF_TRUTH_WRITE_FIELDS})
            payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
            db = self.Database(live)
            if mode == 'nanosecond': db.times[identity] = DatetimeWithNanoseconds.from_rfc3339('2026-10-16T00:00:00.123456788Z')
            elif mode == 'fingerprint': payload['expectedLiveFingerprint'] = '0' * 64
            with self.subTest(mode=mode), self.assertRaises((RuntimeError, ValueError)): self.upload(payload, db)
            self.assertEqual((db.updates, db.commits, db.applied), ([], 0, []))

    def test_v4_unconfirmed_commit_keeps_exact_profile_map_and_stale_replay_is_rejected(self):
        for mode in ('before-commit', 'after-commit', 'wrong-count', 'no-time', 'bad-time', 'new-sibling'):
            payload, live = self.fixture(); db = self.Database(live, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as caught:
                self.upload(payload, db)
            result = caught.exception.prepared_result
            self.assertEqual(result['schemaVersion'], module.READ_SET_TF_TRUTH_INITIALIZE_RESULT_SCHEMA)
            self.assertEqual(result['initializeMissingFieldsByDocument'], payload['initializeMissingFieldsByDocument'])
            self.assertEqual(result['writeFieldsByDocument'], payload['writeFieldsByDocument'])
            self.assertEqual(result['status'], 'commit_unconfirmed'); self.assertFalse(result['publicationConfirmed'])
            self.assertEqual(result['writeResults'], [])
            self.assertEqual((len(db.transactions), db.commits), (1, 1))
            if mode == 'after-commit':
                self.assertEqual(len(db.applied), 5); db.mode = None
                with self.assertRaisesRegex(RuntimeError, 'update_time changed'): self.upload(payload, db)
                rebound = {**payload, 'expectedReadUpdateTimes': {i: db.times[i].rfc3339() for i in payload['readDocumentIds']},
                    'expectedLiveFingerprint': module.firestore_live_fingerprint(payload['readDocumentIds'], db.live)}
                with self.assertRaisesRegex(RuntimeError, 'actually absent'): self.upload(rebound, db)
                self.assertEqual(db.commits, 1)

    def test_v4_readonly_sibling_presence_values_and_versions_remain_guarded(self):
        payload, live = self.fixture([*module.TF_TRUTH_WRITE_FIELDS, 'explanationText'], initialize_count=1)
        for row in payload['questions']:
            live[row['questionId']].update({f: row[f] for f in module.DISPLAY_WRITE_FIELDS})
        first = payload['questions'][0]; payload['questions'] = [first]
        payload['writeFieldsByDocument'] = {first['questionId']: payload['writeFieldsByDocument'][first['questionId']]}
        payload['expectedLiveFingerprint'] = module.firestore_live_fingerprint(payload['readDocumentIds'], live)
        db = self.Database(live); result = self.upload(payload, db)
        self.assertEqual(result['readOnlyDocumentIds'], payload['readDocumentIds'][1:])
        for identity in result['readOnlyDocumentIds']:
            self.assertEqual(db.live[identity], live[identity])
            self.assertEqual(db.times[identity].rfc3339(), AtomicMaintenanceUploadTests.before_stamp)
        readonly = result['readOnlyDocumentIds'][-1]
        changed = self.Database(live); changed.live[readonly].pop('questionTags')
        with self.assertRaises(RuntimeError): self.upload(payload, changed)
        self.assertEqual(changed.updates, [])
        conflict = self.Database(live, mode='readonly-conflict')
        with self.assertRaises(module.ReadSetMaintenanceCommitUnconfirmed) as caught: self.upload(payload, conflict)
        self.assertEqual(caught.exception.prepared_result['initializeMissingFieldsByDocument'], payload['initializeMissingFieldsByDocument'])
        self.assertEqual(conflict.applied, [])
