from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from typing import Any


class RunSnapshot(Mapping[str, Any]):
    """Detach only the immutable-plan fields a consumer actually reads.

    The coordinator owns ``plan`` and must not mutate it during the run.
    ``runtime`` is an already detached, compact manifest/question snapshot.
    Each instance belongs to one consumer: mutable values read from the plan
    are copied once into that instance, never shared with a sibling question.
    Iterating/materializing the mapping still exposes the complete contract.
    """

    def __init__(
        self, plan: Mapping[str, Any], runtime: Mapping[str, Any]
    ) -> None:
        self._plan = plan
        self._values = dict(runtime)

    def __getitem__(self, key: str) -> Any:
        if key not in self._values:
            self._values[key] = copy.deepcopy(self._plan[key])
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys((*self._plan, *self._values)))

    def __len__(self) -> int:
        return len(self._plan.keys() | self._values.keys())
