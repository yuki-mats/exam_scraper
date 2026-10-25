from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from scripts.scrape.knoun_visual_review import CHECKS, validate_result, wait_for_console_idle


class KnounVisualReviewTests(unittest.TestCase):
    def fixture(self):
        record = {"sourceQuestionId": "question-A", "questionImage": {"sha256": "question-image"},
                  "explanationImage": {"sha256": "explanation-image"}}
        candidate = {"sourceQuestionId": "question-A", "questionImageSha256": "question-image",
                     "explanationImageSha256": "explanation-image", "reviewOutcome": "verified",
                     "checks": {key: True for key in CHECKS}, "questionImageContent": "text_only",
                     "questionText": "画像から読んだ問題。", "explanationText": "画像から読んだ解答。"}
        receipt = {"thread_id": "thread", "turn_id": "turn", "session_id": "session", "final_message": "JSON",
                   "image_inputs": [{"sha256": "question-image"}, {"sha256": "explanation-image"}]}
        return record, candidate, receipt

    def test_receipt_requires_the_actual_two_image_inputs_in_role_order(self):
        record, candidate, receipt = self.fixture()
        validate_result(record, candidate, receipt)
        for inputs in ([], [{"sha256": "question-image"}], list(reversed(receipt["image_inputs"])),
                       [{"sha256": "question-image"}, {"sha256": "foreign-answer"}]):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                validate_result(record, candidate, dict(receipt, image_inputs=inputs))

    def test_model_self_report_does_not_override_missing_visual_checks_or_hold(self):
        record, candidate, receipt = self.fixture()
        for key in CHECKS:
            changed = copy.deepcopy(candidate)
            changed["checks"][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_result(record, changed, receipt)
        with self.assertRaises(ValueError):
            validate_result(record, dict(candidate, reviewOutcome="needs_manual_review"), receipt)

    def test_other_question_and_unexpected_side_effects_are_rejected(self):
        record, candidate, receipt = self.fixture()
        with self.assertRaises(ValueError):
            validate_result(record, dict(candidate, sourceQuestionId="question-B"), receipt)
        for extras in ({"subagent_thread_ids": ["another-agent"]}, {"changed_files": ["source.json"]}, {"turn_id": ""}):
            with self.subTest(extras=extras), self.assertRaises(ValueError):
                validate_result(record, candidate, dict(receipt, **extras))

    def test_console_waits_for_both_existing_model_and_run_to_finish(self):
        def response(value):
            result = Mock()
            result.json.return_value = value
            return result
        replies = [response({"appServer": {"modelTurns": {"inFlight": 1}}}), response({"activeRun": {"status": "running"}}),
                   response({"appServer": {"modelTurns": {"inFlight": 0}}}), response({"activeRun": {"status": "running"}}),
                   response({"appServer": {"modelTurns": {"inFlight": 0}}}), response({"activeRun": None})]
        with patch("scripts.scrape.knoun_visual_review.requests.get", side_effect=replies), patch("scripts.scrape.knoun_visual_review.time.sleep") as sleep:
            wait_for_console_idle("http://127.0.0.1:12345")
        self.assertEqual(sleep.call_count, 2)

    def test_unknown_concurrency_or_remote_wait_target_fails_closed(self):
        for base in ("https://remote.example", "http://127.0.0.1:123/?secret=value"):
            with self.assertRaises(ValueError):
                wait_for_console_idle(base)
        response = Mock()
        response.json.side_effect = [{"appServer": {"modelTurns": {}}}, {"activeRun": None}]
        with patch("scripts.scrape.knoun_visual_review.requests.get", return_value=response), self.assertRaises(ValueError):
            wait_for_console_idle("http://127.0.0.1:12345")


if __name__ == "__main__":
    unittest.main()
