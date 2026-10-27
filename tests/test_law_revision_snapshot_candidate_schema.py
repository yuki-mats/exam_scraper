import copy
import json
import unittest

from scripts.common.repaso_firestore_schema import (
    LAW_REVISION_SNAPSHOT_KEYS,
    is_law_revision_facts_shape,
)
from tools.question_review_console.question_candidate import (
    CandidateTarget,
    QuestionCandidateError,
    _matches_rule,
    output_schema,
    parse_model_candidate_v3,
)


class LawRevisionSnapshotCandidateSchemaTest(unittest.TestCase):
    def setUp(self):
        self.targets = {
            "q1": (CandidateTarget(
                "q1:law_audit", "law_audit", "audit.jsonl", ("lawRevisionFacts",),
            ),),
        }

    def fact(self):
        return {
            "auditStatus": "same_as_current",
            "reviewState": "secondary_verified",
            "reconciliationStatus": "matched",
            "examTime": {"correctChoiceText": "正しい"},
            "current": {"correctChoiceText": "正しい"},
            "differenceFacts": [], "answerImpactFacts": [], "notes": [],
            "evidenceSummary": {
                "verdict": "same_as_current", "explanationText": "確認済み。",
                "differenceSummary": "差分なし。", "promptContext": "定義を照合。",
                "displayRefIds": [], "refs": [],
            },
        }

    def parse(self, facts):
        payload = {
            "decision": "candidate", "summary": "根拠を保存する。",
            "update": {"setFields": [{"field": "lawRevisionFacts", "value": facts}],
                       "unsetFields": []},
        }
        result, = parse_model_candidate_v3(json.dumps(payload), ["q1"], self.targets)
        return result.updates[0].set_fields["lawRevisionFacts"]

    def test_authored_and_current_provenance_survives_model_parse(self):
        fact = self.fact()
        for name, date in (("examTime", "2026-10-27"), ("current", "2026-10-28")):
            fact[name].update(
                referenceDate=date, lawId="415AC0000000044", article="第3条",
                paragraph="第2項", articleTextHash="a" * 64,
                sourceUrl="https://laws.e-gov.go.jp/api/2/law_file/xml/415AC0000000044",
                verificationStatus="verified",
            )
        actual = self.parse([fact])
        self.assertEqual(actual, [fact])
        self.assertTrue(is_law_revision_facts_shape(actual[0]))

    def test_strict_output_null_metadata_becomes_omission_without_mutation(self):
        fact = self.fact()
        for name in ("examTime", "current"):
            fact[name].update({k: None for k in LAW_REVISION_SNAPSHOT_KEYS
                               if k != "correctChoiceText"})
        original = copy.deepcopy(fact)
        for wire_value in (fact, [fact]):
            with self.subTest(array=isinstance(wire_value, list)):
                parsed = self.parse(wire_value)
                result = parsed[0] if isinstance(parsed, list) else parsed
                self.assertEqual(result["examTime"], {"correctChoiceText": "正しい"})
                self.assertTrue(is_law_revision_facts_shape(result))
        self.assertEqual(fact, original)
        self.assertEqual(self.parse([self.fact()]), [self.fact()])

    def test_unknown_or_invalid_snapshot_values_are_rejected(self):
        for update in ({"unknownDate": None}, {"referenceDate": 20261028},
                       {"referenceDate": ""}, {"correctChoiceText": None}):
            with self.subTest(update=update):
                fact = self.fact()
                fact["examTime"].update(update)
                with self.assertRaises(QuestionCandidateError):
                    self.parse([fact])
        fact = self.fact()
        del fact["examTime"]["correctChoiceText"]
        with self.assertRaises(QuestionCandidateError):
            self.parse([fact])

    def test_output_is_strict_and_bounded_for_all_contract_snapshot_fields(self):
        schema = output_schema(["q1"], self.targets)
        variants = schema["properties"]["update"]["properties"]["setFields"]["items"]["anyOf"]
        rule = next(v["properties"]["value"] for v in variants
                    if v["properties"]["field"]["const"] == "lawRevisionFacts")
        object_fact, array_fact = rule["anyOf"]
        for fact in (object_fact, array_fact["items"]):
            for name in ("examTime", "current"):
                wire_rule = fact["properties"][name]
                minimal, snapshot = wire_rule["anyOf"]
                self.assertEqual(set(minimal["properties"]), {"correctChoiceText"})
                self.assertEqual(set(snapshot["properties"]), LAW_REVISION_SNAPSHOT_KEYS)
                self.assertEqual(set(snapshot["required"]), LAW_REVISION_SNAPSHOT_KEYS)
                self.assertFalse(snapshot["additionalProperties"])
                self.assertEqual(snapshot["properties"]["correctChoiceText"]["type"], "string")
                self.assertEqual(snapshot["properties"]["referenceDate"]["type"], ["string", "null"])
                self.assertTrue(_matches_rule({"correctChoiceText": "正しい"}, wire_rule))
                full = {k: None for k in LAW_REVISION_SNAPSHOT_KEYS}
                full.update(correctChoiceText="正しい", referenceDate="2026-10-28")
                self.assertTrue(_matches_rule(full, wire_rule))
                self.assertFalse(_matches_rule({**full, "unknownDate": None}, wire_rule))
        # The wire format must not enumerate 2^N subsets of optional locators.
        self.assertLess(len(json.dumps(schema)), 30000)


if __name__ == "__main__":
    unittest.main()
