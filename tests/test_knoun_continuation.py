from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.common.question_identity import SourceIdentityBinding
from scripts.pipeline.continue_knoun_maintenance import Console, current_bindings


class KnounContinuationTests(unittest.TestCase):
    def test_bindings_use_exact_source_identity_and_native_current_state(self):
        binding = SourceIdentityBinding.from_values("source-A", "public-A", "question_A.json#0")
        entry = SimpleNamespace(identity=SimpleNamespace(binding=binding))
        question = {"sourceQuestionKey": "source-A", "sourceRecordRef": "question_A.json#0", "workVersions": {"allCurrent": True}}
        self.assertEqual(current_bindings([question], [entry]), [binding.as_mapping()])
        self.assertEqual(current_bindings([question | {"workVersions": {"allCurrent": False}}], [entry]), [])
        with self.assertRaises(ValueError):
            current_bindings([question | {"sourceQuestionKey": "foreign-source"}], [entry])

    def test_native_start_never_uses_parallel_or_production_plan(self):
        for invalid in ({"questionConcurrency": 2}, {"isProductionWrite": True}):
            console = Console("http://127.0.0.1:12345")
            console.post = Mock(return_value={"canStart": True, "questionConcurrency": 1, "isProductionWrite": False, **invalid})
            with self.assertRaises(ValueError):
                console.start_run({})
            self.assertEqual(console.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
