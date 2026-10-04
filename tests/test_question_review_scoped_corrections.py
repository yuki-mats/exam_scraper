from copy import deepcopy
import unittest

from tools.question_review_console.scoped_corrections import CorrectionContract
from tools.question_review_console.projection import sha256_json


class Gateway:
    def __init__(self):
        self.documents = {"selected": {"questionText": "before", "unknown": [None, {"x": 2}]}}
        self.version = "v1"
        self.calls = 0

    def read_selected_snapshots(self, ids, *, fields):
        return {key: {"questionId": key, "exists": key in self.documents,
            "updateTime": self.version, "fields": {f: {"present": f in self.documents.get(key, {}),
                "value": deepcopy(self.documents.get(key, {}).get(f))} for f in fields}} for key in ids}

    def update_fields_atomically(self, updates):
        for key, fields, version in updates:
            if version != self.version:
                raise ValueError("atomic precondition conflict")
        for key, fields, version in updates:
            self.documents[key].update(deepcopy(fields))
        self.version = "v2"
        self.calls += 1


class CorrectionTests(unittest.TestCase):
    def test_allowlist_is_permission_universe_not_required_updates(self):
        self.delta['allowlist'] = ['questionText', 'explanationText']
        contract = CorrectionContract([self.delta], self.binding)
        receipt = contract.apply(self.gateway, token=contract.token, formal_approval=contract.token,
            production_approval=contract.token, machine_ready=True)
        self.assertEqual(self.gateway.documents['selected']['questionText'], 'after')
        self.assertNotIn('explanationText', self.gateway.documents['selected'])
        self.assertEqual(self.gateway.calls, 1)
        self.assertEqual(receipt['token'], contract.token)

    def test_empty_ungranted_and_invalid_contracts_rejected(self):
        cases = []
        empty = deepcopy(self.delta); empty['fields'] = {}; cases.append(empty)
        ungranted = deepcopy(self.delta); ungranted['allowlist'] = ['explanationText']; cases.append(ungranted)
        unknown_permission = deepcopy(self.delta); unknown_permission['allowlist'].append('unknown'); cases.append(unknown_permission)
        duplicate = deepcopy(self.delta); duplicate['allowlist'] *= 2; cases.append(duplicate)
        for question_id in (None, '', 1, 'collection/doc'):
            bad = deepcopy(self.delta); bad['questionId'] = question_id; cases.append(bad)
        for presence in (None, 0, 'true'):
            bad = deepcopy(self.delta); bad['fields']['questionText']['beforePresent'] = presence; cases.append(bad)
        missing_before = deepcopy(self.delta); missing_before['fields']['questionText'].pop('before'); cases.append(missing_before)
        false_before = deepcopy(self.delta); false_before['fields']['questionText']['beforePresent'] = False; cases.append(false_before)
        for index, delta in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                CorrectionContract([delta], self.binding)

    def setUp(self):
        self.delta = {"questionId": "selected", "updateTime": "v1", "allowlist": ["questionText"],
            "fullDocumentUploadAllowed": False, "fields": {"questionText": {
                "beforePresent": True, "before": "before", "after": "after"}}}
        self.binding = {key: key for key in ("sourceHash", "candidateHash", "policyHash", "workVersionHash", "manifestHash", "liveSnapshotHash")}
        self.contract = CorrectionContract([self.delta], self.binding)
        self.gateway = Gateway()

    def apply(self, **changes):
        args = dict(token=self.contract.token, formal_approval=self.contract.token,
            production_approval=self.contract.token, machine_ready=True)
        args.update(changes)
        return self.contract.apply(self.gateway, **args)

    def test_approvals_and_machine_gate_are_independent(self):
        for key, value in (("token", "drift"), ("formal_approval", None),
                ("production_approval", None), ("machine_ready", False)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply(**{key: value})
        self.assertEqual(self.gateway.calls, 0)

    def test_unknown_fields_preserved_retry_is_receipt_bound(self):
        unknown = deepcopy(self.gateway.documents["selected"]["unknown"])
        receipt = self.apply()
        self.assertEqual(self.gateway.documents["selected"]["unknown"], unknown)
        self.assertEqual(self.apply(operation_receipt=receipt), receipt)
        self.assertEqual(self.gateway.calls, 1)
        self.gateway.version = "v3"
        with self.assertRaises(ValueError):
            self.apply(operation_receipt=receipt)

    def test_before_presence_null_and_version_conflicts(self):
        for change in ("missing", "null", "version", "missing_doc"):
            self.gateway = Gateway()
            if change == "missing": del self.gateway.documents["selected"]["questionText"]
            if change == "null": self.gateway.documents["selected"]["questionText"] = None
            if change == "version": self.gateway.version = "v9"
            if change == "missing_doc": self.gateway.documents.clear()
            with self.subTest(change=change), self.assertRaises(ValueError): self.apply()
            self.assertEqual(self.gateway.calls, 0)

    def test_duplicates_and_unknown_update_fields_refused(self):
        with self.assertRaises(ValueError): CorrectionContract([self.delta, self.delta], self.binding)
        delta = deepcopy(self.delta)
        delta["fields"]["unknown"] = delta["fields"].pop("questionText")
        delta["allowlist"] = ["unknown"]
        with self.assertRaises(ValueError): CorrectionContract([delta], self.binding)

    def test_separately_approved_compensation_uses_after_version(self):
        receipt = self.apply()
        with self.assertRaises(ValueError): self.contract.compensation(receipt, approval=None)
        approval = sha256_json({"rollback": receipt, "token": self.contract.token})
        rollback = self.contract.compensation(receipt, approval=approval)
        self.gateway.version = "intervening"
        with self.assertRaises(ValueError):
            rollback.apply(self.gateway, token=rollback.token, formal_approval=rollback.token,
                production_approval=rollback.token, machine_ready=True)
        self.assertEqual(self.gateway.documents["selected"]["questionText"], "after")

    def test_missing_field_compensation_cannot_delete(self):
        delta = deepcopy(self.delta)
        delta["fields"]["questionText"].update(beforePresent=False, before=None)
        contract = CorrectionContract([delta], self.binding)
        receipt = {"token": contract.token, "afterVersions": {"selected": "v2"}}
        with self.assertRaises(ValueError):
            contract.compensation(receipt, approval=sha256_json({"rollback": receipt, "token": contract.token}))

    def test_five_document_commit_conflict_is_atomic(self):
        class BatchGateway(Gateway):
            def __init__(self):
                super().__init__()
                self.documents = {f"choice{i}": {"questionText": "before", "unknown": i} for i in range(5)}

            def update_fields_atomically(self, updates):
                self.version = "concurrent-write"
                # Last update conflicts before any mutation: transaction semantics.
                if any(version != self.version for key, fields, version in updates):
                    raise ValueError("batch precondition conflict")
                super().update_fields_atomically(updates)
        gateway = BatchGateway()
        deltas = []
        for i in range(5):
            delta = deepcopy(self.delta)
            delta['questionId'] = f'choice{i}'
            deltas.append(delta)
        contract = CorrectionContract(deltas, self.binding)
        original = deepcopy(gateway.documents)
        with self.assertRaises(ValueError):
            contract.apply(gateway, token=contract.token, formal_approval=contract.token,
                production_approval=contract.token, machine_ready=True)
        self.assertEqual(gateway.documents, original)
        self.assertEqual(gateway.calls, 0)

    def test_gateway_returning_other_id_is_refused(self):
        gateway = self.gateway
        read = gateway.read_selected_snapshots
        gateway.read_selected_snapshots = lambda ids, fields: {'other': read(ids, fields=fields)['selected']}
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(gateway.calls, 0)
