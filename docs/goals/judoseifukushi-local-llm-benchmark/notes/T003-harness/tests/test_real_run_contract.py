import sys
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))
from prepare_blind_runtime import ALLOWED, FORBIDDEN_TEXT, ROUTES  # noqa: E402


class RealRunContractTest(unittest.TestCase):
    def test_three_routes_and_sanitized_allowlist(self):
        self.assertEqual(("codex_only", "qwen3:14b", "qwen3.5:27b"), ROUTES)
        self.assertIn("questionIntent", ALLOWED)
        self.assertIn("answer_result", FORBIDDEN_TEXT)

    def test_no_credential_or_image_payload_fields(self):
        from capture_transport import FORBIDDEN_KEYS, redact
        self.assertTrue({"authorization", "cookie", "credential", "image_bytes", "data"} <= FORBIDDEN_KEYS)
        self.assertEqual("[redacted-data-url]", redact("data:image/png;base64,AAAA"))

    def test_t015_audit_covers_all_target_dimensions(self):
        from t015_continuation import audit_schema
        properties = audit_schema()["properties"]["results"]["items"]["properties"]
        self.assertTrue({"acceptedTargets", "correctedResult", "criticalFlags", "examTimeBasis",
                         "imageReviewed", "calculationReviewed"} <= set(properties))

    def test_t015_prompt_rejects_oracle_fields(self):
        from t015_continuation import reject_prompt_leakage
        with self.assertRaises(RuntimeError):
            reject_prompt_leakage('{"correctChoiceText":"leak"}')

    def test_one_terminal_law_failure_makes_law_gate_unreachable(self):
        from benchmark_contract import ALL_IDS
        from t015_continuation import ContinuationRunner
        runner = object.__new__(ContinuationRunner)
        runner.items = [{"id": item_id} for item_id in ALL_IDS]
        reachable, failed = runner.quality_reachable([
            {"id": "c5167b46942fb08e", "status": "availability_reject"}
        ])
        self.assertFalse(reachable)
        self.assertIn("law", failed)

if __name__ == "__main__": unittest.main()
