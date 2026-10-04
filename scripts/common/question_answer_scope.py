from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_PM_SOURCE_ID = re.compile(
    r"^[^:]+:pm[0-9]+:setumon[0-9]+:[0-9]*:(?P<marker>[a-z]+):(?P<url>https?://.+)$"
)


def question_answer_scope(record: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an acquired answer unit from its preserved source identity."""

    label = str(record.get("questionLabel") or "").strip()
    scope: dict[str, Any] = {"questionLabel": label} if label else {}
    match = _PM_SOURCE_ID.fullmatch(str(record.get("source_question_id") or ""))
    if match is None or match["marker"] == "main":
        return scope
    marker = match["marker"]
    if match["url"] != record.get("question_url") or not label.endswith(" " + marker):
        raise ValueError("source identityとquestionLabelの解答対象が一致しません。")
    scope["answerTarget"] = {"kind": "named_answer_slot", "marker": marker}
    return scope


def question_body_for_answer(
    record: Mapping[str, Any], *, body_text: str | None = None
) -> str:
    """Keep the source body and explicitly label the selected answer unit."""

    body = str(record.get("questionBodyText") or "") if body_text is None else body_text
    target = question_answer_scope(record).get("answerTarget")
    if not isinstance(target, Mapping):
        return body
    annotation = f"解答対象：{target['marker']}"
    return body if body.endswith(annotation) else body + "\n\n" + annotation
