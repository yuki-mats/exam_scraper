import copy
import unittest

from tools.question_review_console.run_snapshot import RunSnapshot


class RunSnapshotTests(unittest.TestCase):
    def test_handoff_does_not_copy_unread_global_plan(self):
        class UnreadPlan:
            def __deepcopy__(self, memo):
                raise AssertionError("unrelated whole-run plan was copied")

        plan = {"stagePlans": UnreadPlan(), "mode": "remaining"}
        for index in range(100):
            snapshot = RunSnapshot(plan, {"questionExecutions": [index]})
            self.assertEqual(snapshot["mode"], "remaining")
            self.assertEqual(snapshot["questionExecutions"], [index])

    def test_nested_plan_values_are_detached_per_consumer(self):
        plan = {"progressTargets": [{"aliases": ["q1"]}], "mode": "old"}
        baseline = copy.deepcopy(plan)
        first = RunSnapshot(plan, {"mode": "remaining"})
        second = RunSnapshot(plan, {})
        first["progressTargets"][0]["aliases"].append("local")
        self.assertEqual(plan, baseline)
        self.assertEqual(second["progressTargets"], baseline["progressTargets"])
        self.assertIs(first["progressTargets"], first["progressTargets"])
        self.assertEqual(first["mode"], "remaining")

    def test_scoped_target_override_does_not_read_all_sibling_targets(self):
        class SiblingTargets:
            def __deepcopy__(self, memo):
                raise AssertionError("all question targets were copied")

        snapshot = RunSnapshot(
            {"progressTargets": SiblingTargets()},
            {"progressTargets": [{"id": "q1"}]},
        )
        self.assertEqual(snapshot["progressTargets"], [{"id": "q1"}])

    def test_full_contract_and_missing_fields(self):
        snapshot = RunSnapshot({"a": [1], "b": 2}, {"b": 3, "c": 4})
        self.assertEqual(dict(snapshot), {"a": [1], "b": 3, "c": 4})
        self.assertEqual(len(snapshot), 3)
        self.assertIsNone(snapshot.get("missing"))
        with self.assertRaises(KeyError):
            snapshot["missing"]
