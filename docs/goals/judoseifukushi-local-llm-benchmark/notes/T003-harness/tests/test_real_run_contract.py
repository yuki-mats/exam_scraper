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
        from capture_transport import FORBIDDEN_KEYS
        self.assertTrue({"authorization", "cookie", "credential", "image_bytes", "data"} <= FORBIDDEN_KEYS)

if __name__ == "__main__": unittest.main()
