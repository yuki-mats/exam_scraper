from __future__ import annotations

import re
from typing import Any, Iterable


LAW_AS_SENTENCE_SUBJECT = re.compile(
    r"^(?:正しい|間違い)。\s*"
    r"[^、。]{1,80}(?:法|令|規則|省令|告示)"
    r"第[^、。]{1,80}は[、，]"
)
POINT_IS_WRONG = re.compile(r"(?:点|ところ)が誤り(?:である)?(?:。|$)")


def explanation_style_issues(explanations: Iterable[Any]) -> list[str]:
    """Return deterministic violations of the stage-03 Japanese style policy."""

    issues: list[str] = []
    for choice_index, raw in enumerate(explanations, start=1):
        text = str(raw or "").strip()
        if not text:
            issues.append(f"選択肢{choice_index}: 解説が空です。")
            continue
        if LAW_AS_SENTENCE_SUBJECT.search(text):
            issues.append(
                f"選択肢{choice_index}: 法令名・条文を機械的に文頭の主語へ"
                "置かず、正しい内容を主語にしてください。"
            )
        if POINT_IS_WRONG.search(text):
            issues.append(
                f"選択肢{choice_index}: 「点が誤り」ではなく、正しい内容と"
                "選択肢との差を示して「ため誤りである」と説明してください。"
            )
    return issues
