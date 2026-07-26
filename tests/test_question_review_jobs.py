from __future__ import annotations

import threading
import time
import unittest

from tools.question_review_console.jobs import (
    REPOSITORY_OPERATION_KEY,
    JobConflictError,
    JobManager,
    qualification_operation_key,
)


class JobManagerQualificationLockTest(unittest.TestCase):
    def test_reports_conflict_only_while_matching_job_is_active(self) -> None:
        manager = JobManager()
        started = threading.Event()
        release = threading.Event()

        def worker(_emit):
            started.set()
            self.assertTrue(release.wait(2))
            return {"ok": True}

        job = manager.start(
            kind="test",
            key=qualification_operation_key("sample"),
            worker=worker,
        )
        self.assertTrue(started.wait(1))
        self.assertTrue(
            manager.has_conflict(qualification_operation_key("sample"))
        )
        self.assertFalse(
            manager.has_conflict(qualification_operation_key("other"))
        )

        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                manager.get(job["jobId"])["status"] == "succeeded"
                and not manager.has_conflict(
                    qualification_operation_key("sample")
                )
            ):
                break
            time.sleep(0.01)

        self.assertFalse(
            manager.has_conflict(qualification_operation_key("sample"))
        )

    def test_different_qualifications_can_run_together(self) -> None:
        manager = JobManager()
        release = threading.Event()

        def worker(_emit):
            release.wait(timeout=2)
            return {}

        first = manager.start(
            kind="maintenance",
            key=qualification_operation_key("gas"),
            worker=worker,
        )
        second = manager.start(
            kind="maintenance",
            key=qualification_operation_key("aws"),
            worker=worker,
        )
        self.assertNotEqual(first["jobId"], second["jobId"])
        release.set()

    def test_same_qualification_conflicts(self) -> None:
        manager = JobManager()
        release = threading.Event()
        manager.start(
            kind="maintenance",
            key=qualification_operation_key("gas"),
            worker=lambda _emit: (release.wait(timeout=2) or {}),
        )
        with self.assertRaises(JobConflictError):
            manager.start(
                kind="maintenance",
                key=qualification_operation_key("gas"),
                worker=lambda _emit: {},
            )
        release.set()

    def test_repository_operation_conflicts_with_qualification(self) -> None:
        manager = JobManager()
        release = threading.Event()
        manager.start(
            kind="maintenance",
            key=qualification_operation_key("gas"),
            worker=lambda _emit: (release.wait(timeout=2) or {}),
        )
        with self.assertRaises(JobConflictError):
            manager.run_exclusive(
                key=REPOSITORY_OPERATION_KEY,
                worker=lambda: {},
            )
        release.set()


if __name__ == "__main__":
    unittest.main()
