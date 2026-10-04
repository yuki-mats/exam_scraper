from __future__ import annotations

import re
import hashlib
from collections.abc import Mapping
from typing import Any


_PM_SOURCE_ID = re.compile(
    r"^[^:]+:pm[0-9]+:setumon[0-9]+:[0-9]*:(?P<marker>[a-z]+):(?P<url>https?://.+)$"
)

_CHOICE_LABELS = "アイウエオカキクケコサシスセソタチツテトナニヌネノ"
_UNORDERED_LINE = re.compile(r"^[\s∴]*([a-z]+)\s*[＝=]\s*([ァ-ン])(?:[：:][^\n（）()]*)?\s*[（(]順不同[）)]\s*$")
_UNORDERED_PROSE = re.compile(r"([a-z]+(?:[、,，]\s*[a-z]+)+)に入るのは((?:「[ァ-ン]」(?:と|[、,，])?)+)です[（(]順不同[）)]")


def unordered_answer_evidence(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read explicit interchangeable answer positions, without changing source."""
    match = _PM_SOURCE_ID.fullmatch(str(record.get("source_question_id") or ""))
    if not match or match["marker"] == "main":
        return None
    marker = match["marker"]
    if match["url"] != record.get("question_url") or not str(record.get("questionLabel") or "").endswith(" " + marker):
        raise ValueError("source identityとquestionLabelの解答対象が一致しません。")
    prefix = record.get("explanation_common_prefix")
    if not isinstance(prefix, list) or not all(isinstance(x, str) for x in prefix):
        return None
    text = "\n".join(prefix)
    groups = []
    lines = text.splitlines()
    current = []
    for line in [*lines, ""]:
        found = _UNORDERED_LINE.fullmatch(line)
        if found:
            current.append((found[1], found[2], line))
        else:
            if len(current) >= 2:
                groups.append(([x[0] for x in current], [x[1] for x in current], "\n".join(x[2] for x in current)))
            current = []
    for found in _UNORDERED_PROSE.finditer(text):
        groups.append((re.split(r"[、,，]\s*", found[1]), re.findall(r"「([ァ-ン])」", found[2]), found[0]))
    matching = [g for g in groups if marker in g[0]]
    if not matching:
        return None
    if len(matching) != 1:
        raise ValueError("順不同の解答範囲が複数あり、一意に確定できません。")
    markers, labels, excerpt = matching[0]
    choices = record.get("choiceTextList")
    if not isinstance(choices, list) or len(markers) != len(labels) or len(set(markers)) != len(markers) or len(set(labels)) != len(labels):
        raise ValueError("順不同の解答位置と解答語句が一対一ではありません。")
    if any(label not in _CHOICE_LABELS or _CHOICE_LABELS.index(label) >= len(choices) for label in labels):
        raise ValueError("順不同の解答肢記号を元の解答群へ対応付けられません。")
    numbers = sorted(_CHOICE_LABELS.index(label) + 1 for label in labels)
    return {"markers": markers, "acceptedChoiceNumbers": numbers,
            "acceptedChoiceTexts": [choices[n - 1] for n in numbers],
            "sourceExcerpt": excerpt, "sourceExcerptHash": hashlib.sha256(excerpt.encode()).hexdigest()}


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
    evidence = unordered_answer_evidence(record)
    if evidence:
        scope["answerTarget"]["unorderedAnswerEvidence"] = evidence
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
    evidence = target.get("unorderedAnswerEvidence")
    if evidence:
        annotation += f"（{'・'.join(evidence['markers'])}は順不同）"
    return body if body.endswith(annotation) else body + "\n\n" + annotation
