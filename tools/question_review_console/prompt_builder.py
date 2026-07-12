from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def _choice_excerpt(question: Mapping[str, Any], indexes: list[int]) -> str:
    projected = question.get("projected") or {}
    choices = projected.get("choiceTextList")
    correctness = projected.get("correctChoiceText")
    explanations = projected.get("explanationText")
    if not isinstance(choices, list):
        return "（選択肢なし）"
    target_indexes = indexes or list(range(len(choices)))
    lines = []
    for index in target_indexes:
        if index < 0 or index >= len(choices):
            continue
        verdict = correctness[index] if isinstance(correctness, list) and index < len(correctness) else ""
        explanation = explanations[index] if isinstance(explanations, list) and index < len(explanations) else ""
        lines.extend(
            (
                f"### 選択肢{index + 1}",
                str(choices[index]),
                f"- 現在の正誤: `{verdict}`",
                f"- 現在の解説: {explanation}",
                "",
            )
        )
    return "\n".join(lines).strip()


def build_codex_prompt(
    repo_root: Path,
    review_path: Path,
    question: Mapping[str, Any],
    review: Mapping[str, Any],
) -> str:
    paths = question.get("paths") or {}
    selected_indexes = [int(value) for value in review.get("choiceIndexes") or []]
    issue_types = ", ".join(str(value) for value in review.get("issueTypes") or [])
    fields = ", ".join(str(value) for value in review.get("fields") or [])
    path_lines = []
    for label in ("source", "merged", "converted", "uploadReady"):
        value = paths.get(label)
        if value:
            path_lines.append(f"- {label}: `{repo_root / value}`")
    for value in paths.get("patches") or []:
        path_lines.append(f"- patch: `{repo_root / value}`")

    body = str(question.get("body") or "")
    return f"""# 問題整備レビュー対応

ローカル問題レビューUIで次の指摘が作成されました。review JSONを読み、現行workflowに従って原因調査、必要なpatch修正、検証まで行ってください。

## 対象

- reviewId: `{review.get('reviewId')}`
- review JSON: `{review_path}`
- qualification: `{question.get('qualification')}`
- listGroupId: `{question.get('listGroupId')}`
- sourceQuestionKey: `{question.get('sourceQuestionKey')}`
- reviewKey: `{question.get('reviewKey')}`
- issue: {issue_types or '未分類'}
- fields: {fields or '未指定'}

## 人間の指摘

{review.get('note') or '（補足なし）'}

期待する状態:

{review.get('expectedOutcome') or '（Codexで根拠を確認して判断する）'}

## 問題文

{body}

{_choice_excerpt(question, selected_indexes)}

## 関連ファイル

{chr(10).join(path_lines)}

## 守ること

- `00_source`は変更しない。
- 問題文・選択肢・正誤・解説・分類の修正は、責務に合うpatch層へ入れる。
- 問題文と選択肢を結合した完全な判定命題を確認する。
- `correctChoiceText`を変更する場合は、解説先頭、根拠、`lawRevisionFacts`との整合も確認する。
- 既存の`questionId`、`originalQuestionId`、`questionSetId`を不用意に変更しない。
- 対象外の未コミット変更を破棄しない。
- merge、convert、対象quality-gateを実行し、変更が最終成果物へ反映されることを確認する。
- Firestoreへの実アップロードは、この依頼又はユーザーが明示した場合だけ行う。

## 完了時に示すもの

- 変更したpatchとfield
- 判断根拠
- 実行した検証と結果
- Firestore未反映の場合は、その状態
"""
