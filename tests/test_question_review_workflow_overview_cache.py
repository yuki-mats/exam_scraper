import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tools.question_review_console.workflow_overview_cache import (
    WorkflowOverviewCache,
)


def overview(qualification: str, marker: str) -> dict:
    return {
        "qualification": qualification,
        "generatedAt": marker,
        "groups": [],
        "stages": [],
    }


class WorkflowOverviewCacheTests(unittest.TestCase):
    def test_cold_nonblocking_read_starts_one_background_build(self):
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            release = threading.Event()
            calls = []

            def loader(qualification):
                calls.append(qualification)
                started.set()
                self.assertTrue(release.wait(2))
                return overview(qualification, "fresh")

            cache = WorkflowOverviewCache(Path(directory), loader)
            before = time.monotonic()
            first = cache.get("sample", wait_for_initial=False)
            second = cache.get("sample", wait_for_initial=False)

            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertLess(time.monotonic() - before, 0.5)
            self.assertTrue(started.wait(1))
            self.assertEqual(calls, ["sample"])

            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = cache.get("sample", wait_for_initial=False)
                if current is not None:
                    break
                time.sleep(0.01)

        self.assertEqual(current["generatedAt"], "fresh")

    def test_custom_cache_contract_uses_its_own_directory_and_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def validate(qualification, value):
                snapshot = dict(value)
                if snapshot.get("qualification") != qualification:
                    raise ValueError("qualification mismatch")
                if not isinstance(snapshot.get("questions"), list):
                    raise ValueError("questions must be an array")
                return snapshot

            cache = WorkflowOverviewCache(
                root,
                lambda qualification: {
                    "qualification": qualification,
                    "questions": [],
                },
                cache_subdirectory="question_lists",
                schema_version="question-list/v1",
                payload_field="snapshot",
                validator=validate,
            )
            cache.get("sample")
            payload = json.loads(
                (
                    root
                    / "output"
                    / "question_review_console"
                    / "cache"
                    / "question_lists"
                    / "sample.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(payload["schemaVersion"], "question-list/v1")
        self.assertEqual(payload["snapshot"]["qualification"], "sample")

    def test_concurrent_cold_reads_share_one_overview_build(self):
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            release = threading.Event()
            calls = []

            def loader(qualification):
                calls.append(qualification)
                started.set()
                self.assertTrue(release.wait(2))
                return overview(qualification, "fresh")

            cache = WorkflowOverviewCache(Path(directory), loader)
            results = []
            threads = [
                threading.Thread(
                    target=lambda: results.append(cache.get("sample")),
                )
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            self.assertTrue(started.wait(1))
            release.set()
            for thread in threads:
                thread.join(2)

        self.assertEqual(calls, ["sample"])
        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result["generatedAt"] for result in results},
            {"fresh"},
        )

    def test_persisted_snapshot_returns_before_single_background_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            warm = WorkflowOverviewCache(
                root,
                lambda qualification: overview(qualification, "cached"),
            )
            warm.get("sample")

            started = threading.Event()
            release = threading.Event()
            calls = []

            def loader(qualification):
                calls.append(qualification)
                started.set()
                self.assertTrue(release.wait(2))
                return overview(qualification, "refreshed")

            restarted = WorkflowOverviewCache(
                root,
                loader,
                refresh_interval_seconds=60,
            )
            before = time.monotonic()
            first = restarted.get("sample")
            elapsed = time.monotonic() - before
            second = restarted.get("sample")

            self.assertLess(elapsed, 0.5)
            self.assertEqual(first["generatedAt"], "cached")
            self.assertTrue(first["cache"]["refreshing"])
            self.assertEqual(second["generatedAt"], "cached")
            self.assertTrue(started.wait(1))
            self.assertEqual(calls, ["sample"])

            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = restarted.get("sample")
                if not current["cache"]["refreshing"]:
                    break
                time.sleep(0.01)

        self.assertEqual(current["generatedAt"], "refreshed")
        self.assertFalse(current["cache"]["refreshing"])

    def test_invalidation_is_debounced_and_refreshes_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def loader(qualification):
                marker = f"build-{len(calls) + 1}"
                calls.append(marker)
                return overview(qualification, marker)

            cache = WorkflowOverviewCache(
                Path(directory),
                loader,
                invalidation_delay_seconds=0.05,
            )
            first = cache.get("sample")
            cache.invalidate("sample")
            cache.invalidate("sample")
            pending = cache.get("sample")

            self.assertEqual(first["generatedAt"], "build-1")
            self.assertEqual(pending["generatedAt"], "build-1")
            self.assertTrue(pending["cache"]["stale"])
            self.assertFalse(pending["cache"]["refreshing"])
            self.assertEqual(calls, ["build-1"])

            time.sleep(0.06)
            refreshing = cache.get("sample")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = cache.get("sample")
                if not current["cache"]["refreshing"]:
                    break
                time.sleep(0.01)

        self.assertTrue(refreshing["cache"]["stale"])
        self.assertEqual(current["generatedAt"], "build-2")
        self.assertEqual(calls, ["build-1", "build-2"])

    def test_background_refresh_waits_until_canonical_job_is_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            refresh_allowed = False

            def loader(qualification):
                marker = f"build-{len(calls) + 1}"
                calls.append(marker)
                return overview(qualification, marker)

            cache = WorkflowOverviewCache(
                Path(directory),
                loader,
                invalidation_delay_seconds=0,
                refresh_allowed=lambda _qualification: refresh_allowed,
            )
            first = cache.get("sample")
            cache.invalidate("sample")
            blocked = cache.get("sample")

            self.assertEqual(first["generatedAt"], "build-1")
            self.assertEqual(blocked["generatedAt"], "build-1")
            self.assertTrue(blocked["cache"]["stale"])
            self.assertFalse(blocked["cache"]["refreshing"])
            self.assertEqual(calls, ["build-1"])

            refresh_allowed = True
            cache.get("sample")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = cache.get("sample")
                if not current["cache"]["refreshing"]:
                    break
                time.sleep(0.01)

        self.assertEqual(current["generatedAt"], "build-2")
        self.assertEqual(calls, ["build-1", "build-2"])


if __name__ == "__main__":
    unittest.main()
