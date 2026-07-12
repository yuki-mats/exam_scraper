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
    selection = review.get("selection") or {}
    selection_text = str(selection.get("selectedText") or "")
    quoted_selection = "\n".join(f"> {line}" for line in selection_text.splitlines())
    selection_section = ""
    if selection:
        selected_fields = ", ".join(str(value) for value in selection.get("fields") or [])
        selected_choices = ", ".join(
            f"選択肢{int(value) + 1}" for value in selection.get("choiceIndexes") or []
        )
        selection_section = f"""## UIで選択した箇所

- 表示位置: {selection.get('targetLabel') or '未指定'}
- data path: `{selection.get('dataPath') or '未指定'}`
- field: {selected_fields or '未指定'}
- 対象選択肢: {selected_choices or 'なし'}

{quoted_selection or '>（表示テキストなし）'}

"""
    scope = str(review.get("investigationScope") or "current_question")
    scope_labels = {
        "current_question": "この問題のみ",
        "current_group": "同じ資格・同じフォルダの類似問題",
        "qualification": "同じ資格の全フォルダにある類似問題",
        "all_qualifications": "全資格にある類似問題",
    }
    scope_instruction = {
        "current_question": "指定された問題だけを調査・修正する。",
        "current_group": "同じqualification・listGroupId内を検索し、同じ原因と確認できた類似問題も修正する。",
        "qualification": "同じqualificationの全listGroupIdを検索し、同じ原因と確認できた類似問題も修正する。",
        "all_qualifications": "全qualificationを検索し、同じ原因と確認できた類似問題も修正する。",
    }
    scope_label = scope_labels.get(scope, scope_labels["current_question"])
    scope_text = scope_instruction.get(scope, scope_instruction["current_question"])
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
- 調査範囲: {scope_label}

## 人間の指摘

{review.get('note') or '（補足なし）'}

期待する状態:

{review.get('expectedOutcome') or '（Codexで根拠を確認して判断する）'}

{selection_section}## 問題文

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
- 調査範囲は「{scope_label}」とする。{scope_text}文言が似ているだけで一括置換せず、問題文と選択肢を結合した判定命題と根拠を個別に確認する。

## 完了時に示すもの

- 変更したpatchとfield
- 判断根拠
- 実行した検証と結果
- Firestore未反映の場合は、その状態
"""
