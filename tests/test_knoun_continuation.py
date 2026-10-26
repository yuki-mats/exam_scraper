from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.common.question_identity import SourceIdentityBinding
from scripts.pipeline.continue_knoun_maintenance import Console, current_bindings, eligible_sources


class KnounContinuationTests(unittest.TestCase):
    def test_native_handle_is_saved_before_wait_failure(self):
        console = Console("http://127.0.0.1:12345")
        console.post = Mock(side_effect=[
            {"canStart":True,"questionConcurrency":1,"isProductionWrite":False,"previewToken":"preview"},
            {"run":{"runId":"durable-run"}}])
        saved=[]
        def wait(run_id):
            self.assertEqual(saved,[run_id])
            raise TimeoutError("observation failed; native run remains addressable")
        console.wait_run=wait
        with self.assertRaises(TimeoutError):
            console.start_run({},on_started=saved.append)
        self.assertEqual(saved,["durable-run"])
        self.assertEqual(console.post.call_count,2)

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


class KnounContinuationTest(unittest.TestCase):
    def test_registered_transcription_conflict_is_held_without_blocking_other_ids(self):
        urls = [f"https://knoun.jp/book/mondai1_{n}.html" for n in (7,2,9)]
        manifest = {"records": dict.fromkeys(urls, {})}
        values = {u: {"source_question_id": u, "questionBodyText": "現在の転記"} for u in urls}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / "questions_json/knoun-2026-mock-1/00_source"
            sources.mkdir(parents=True)
            conflict = sources / "question_mondai1_2.json"
            old = json.dumps({"question_bodies": [values[urls[1]] | {"questionBodyText": "登録時の転記"}]}).encode()
            conflict.write_bytes(old)
            unchanged = sources / "question_mondai1_9.json"
            unchanged.write_text(json.dumps({"question_bodies": [values[urls[2]]]}))
            with patch("scripts.pipeline.continue_knoun_maintenance.read_manifest", return_value=manifest), patch(
                "scripts.pipeline.continue_knoun_maintenance.source_record",
                side_effect=lambda root, group, source_id, **kwargs: values[source_id],
            ):
                eligible, holds = eligible_sources(root, 1)
            self.assertEqual(eligible, [urls[0], urls[2]])
            self.assertEqual(len(holds), 1)
            self.assertEqual(holds[0]["sourceQuestionId"], urls[1])
            self.assertIn("登録済み00_source", holds[0]["reason"])
            self.assertEqual(conflict.read_bytes(), old)
            self.assertFalse((sources / "question_mondai1_7.json").exists())


if __name__ == "__main__":
    unittest.main()
