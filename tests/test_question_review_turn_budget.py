from __future__ import annotations

import threading
import time
import unittest

from tools.question_review_console.turn_budget import (
    GLOBAL_TURN_CAPACITY,
    GlobalTurnBudget,
)


class GlobalTurnBudgetTest(unittest.TestCase):
    def test_single_qualification_receives_full_capacity(self) -> None:
        budget = GlobalTurnBudget(32)
        with budget.register("gas"):
            snapshot = budget.snapshot()
        self.assertEqual(snapshot["groups"][0]["allocation"], 32)

    def test_two_qualifications_split_capacity_evenly(self) -> None:
        budget = GlobalTurnBudget(GLOBAL_TURN_CAPACITY)
        with budget.register("gas"), budget.register("aws"):
            allocations = {
                row["group"]: row["allocation"]
                for row in budget.snapshot()["groups"]
            }
        self.assertEqual(allocations, {"aws": 32, "gas": 32})

    def test_default_capacity_is_sixty_four(self) -> None:
        self.assertEqual(GlobalTurnBudget().snapshot()["capacity"], 64)

    def test_three_qualifications_share_all_slots(self) -> None:
        budget = GlobalTurnBudget(32)
        with (
            budget.register("gas"),
            budget.register("aws"),
            budget.register("third"),
        ):
            allocations = [
                row["allocation"] for row in budget.snapshot()["groups"]
            ]
        self.assertEqual(sum(allocations), 32)
        self.assertEqual(sorted(allocations), [10, 11, 11])

    def test_waiter_never_exceeds_global_capacity(self) -> None:
        budget = GlobalTurnBudget(2)
        releases = [threading.Event(), threading.Event()]
        entered: list[int] = []

        def worker(index: int) -> None:
            with budget.slot("gas"):
                entered.append(index)
                releases[index].wait(timeout=2)

        threads = [
            threading.Thread(target=worker, args=(index,), daemon=True)
            for index in range(2)
        ]
        with budget.register("gas"):
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 2
            while len(entered) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(entered), 2)
            self.assertEqual(budget.snapshot()["inFlight"], 2)
            releases[0].set()
            releases[1].set()
            for thread in threads:
                thread.join(timeout=2)
        self.assertEqual(budget.snapshot()["peakInFlight"], 2)

    def test_second_group_reduces_future_acquisition_share(self) -> None:
        budget = GlobalTurnBudget(4)
        acquired = [budget.acquire("gas") for _ in range(4)]
        self.assertEqual(budget.snapshot()["inFlight"], 4)
        with budget.register("gas"), budget.register("aws"):
            budget.release(acquired.pop())
            budget.release(acquired.pop())
            entered = threading.Event()
            release_aws = threading.Event()

            def aws_worker() -> None:
                with budget.slot("aws"):
                    entered.set()
                    release_aws.wait(timeout=2)

            thread = threading.Thread(target=aws_worker, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            groups = {
                row["group"]: row for row in budget.snapshot()["groups"]
            }
            self.assertEqual(groups["gas"]["inFlight"], 2)
            self.assertEqual(groups["aws"]["inFlight"], 1)
            release_aws.set()
            thread.join(timeout=2)
        for group in acquired:
            budget.release(group)

    def test_heartbeat_runs_without_holding_budget_condition(self) -> None:
        budget = GlobalTurnBudget(1)
        held = budget.acquire("gas")
        callback_entered = threading.Event()
        callback_release = threading.Event()
        snapshot_completed = threading.Event()

        def heartbeat() -> None:
            callback_entered.set()
            callback_release.wait(timeout=2)

        def waiter() -> None:
            with budget.slot("gas", heartbeat=heartbeat):
                pass

        waiting_thread = threading.Thread(target=waiter, daemon=True)
        waiting_thread.start()
        self.assertTrue(callback_entered.wait(timeout=2))

        snapshot_thread = threading.Thread(
            target=lambda: (
                budget.snapshot(),
                snapshot_completed.set(),
            ),
            daemon=True,
        )
        snapshot_thread.start()
        self.assertTrue(snapshot_completed.wait(timeout=0.5))
        callback_release.set()
        budget.release(held)
        waiting_thread.join(timeout=2)
        snapshot_thread.join(timeout=2)

    def test_priority_waiter_enters_before_normal_waiter(self) -> None:
        budget = GlobalTurnBudget(1)
        held = budget.acquire("gas")
        entered: list[str] = []
        normal_release = threading.Event()
        priority_release = threading.Event()

        def worker(name: str, priority: bool, release: threading.Event) -> None:
            with budget.slot("gas", priority=priority):
                entered.append(name)
                release.wait(timeout=2)

        normal = threading.Thread(
            target=worker,
            args=("normal", False, normal_release),
            daemon=True,
        )
        priority = threading.Thread(
            target=worker,
            args=("priority", True, priority_release),
            daemon=True,
        )
        normal.start()
        priority.start()
        deadline = time.monotonic() + 2
        while budget.snapshot()["waiting"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        budget.release(held)
        deadline = time.monotonic() + 2
        while not entered and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(entered, ["priority"])
        priority_release.set()
        deadline = time.monotonic() + 2
        while len(entered) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(entered, ["priority", "normal"])
        normal_release.set()
        normal.join(timeout=2)
        priority.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
