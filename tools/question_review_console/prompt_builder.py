from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


LAW_AUDIT_ISSUES = {
    "law_audit_metadata_incomplete",
    "law_audit_verdict_mismatch",
    "law_hold",
    "law_basis_missing",
}

QUALIFICATION_LAW_AUDIT_REQUEST = "qualification_law_audit"


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


def _is_law_audit_review(review: Mapping[str, Any]) -> bool:
    issue_types = {str(value) for value in review.get("issueTypes") or []}
    fields = {str(value) for value in review.get("fields") or []}
    selection = review.get("selection") or {}
    selection_fields = {
        str(value) for value in selection.get("fields") or []
    } if isinstance(selection, Mapping) else set()
    return bool(issue_types & LAW_AUDIT_ISSUES) or any(
        field.startswith(("lawRevisionFacts", "lawReferences"))
        for field in fields | selection_fields
    )


def _law_audit_instruction() -> str:
    return """## 法令監査指示

- 既存の`lawReferences`、`lawRevisionFacts`、`explanationText`は候補根拠であり、値を写すだけで確定しない。
- 各対象選択肢で「問題文＋選択肢」の完全命題を作り、保存済み`apiUrl`/`sourceUrl`又はe-Gov条文本文を開いて目視照合する。
- 条文本文で確認できた場合だけ`lawRevisionFacts.current.correctChoiceText`を設定する。patchでは各選択肢と同じ順序・件数で保存し、トップレベル`correctChoiceText`と一致させる。確認不能・根拠不足は`hold`/`needs_secondary_review`へ戻す。
- 類似問題も一問一肢ずつ同じ手順で確認する。一括コピーや正誤ラベルだけの補完は禁止。
- 完了時は「選択肢 / 条文 / 判定 / patch有無」の短い確認表を出す。

"""


def is_qualification_law_audit(review: Mapping[str, Any]) -> bool:
    if review.get("requestKind") == QUALIFICATION_LAW_AUDIT_REQUEST:
        return True
    selection = review.get("selection") or {}
    return (
        isinstance(selection, Mapping)
        and selection.get("targetLabel") == "法令監査メタデータの一括報告"
        and review.get("investigationScope") == "qualification"
    )


def _build_qualification_law_audit_prompt(
    repo_root: Path,
    review_path: Path,
    question: Mapping[str, Any],
    review: Mapping[str, Any],
) -> str:
    del review_path, question
    target_paths = []
    for value in review.get("targetFiles") or []:
        relative = Path(str(value))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        target_paths.append(repo_root / relative)
    path_lines = "\n".join(f"- `{path}`" for path in target_paths)
    sidecar_paths: set[Path] = set()
    for value in review.get("targetSourceFiles") or []:
        relative = Path(str(value))
        parts = relative.parts
        if (
            relative.is_absolute()
            or ".." in parts
            or len(parts) < 5
            or parts[0] != "output"
            or parts[2] != "questions_json"
        ):
            continue
        sidecar_paths.add(
            repo_root
            / "output"
            / parts[1]
            / "review"
            / "law_revision_audit"
            / f"{parts[3]}_law_revision_audit.jsonl"
        )
    sidecar_lines = "\n".join(
        f"- `{path}`" for path in sorted(sidecar_paths)
    )
    return f"""# 法令監査パッチ一括修正

## 対象ファイル

{path_lines or '- （対象ファイルを取得できないため、依頼を作り直す）'}

## 監査sidecar

{sidecar_lines or '- （対象年度を取得できないため、依頼を作り直す）'}

## やること

- 各ファイル内で`law_audit_metadata_incomplete`等の法令監査品質不備がある全questionを特定し、一問一肢ずつ「問題文＋選択肢」の完全命題を作る。
- 各命題についてCodex組み込みweb検索を使い、e-Gov法令検索又は所管官庁の一次情報を開いて条文本文を目視レベルで照合する。主体、要件、数値、例外、委任先まで確認する。
- 既存の正誤・解説・法令メタデータや検索要約を正本扱いしない。不一致又は根拠不足は推測せず`hold`/`needs_secondary_review`にする。
- 確認結果と根拠を各questionのpatchへ個別に反映する。正誤を変えない場合も`lawRevisionFacts.current.correctChoiceText`を省略せず、各選択肢と同じ順序・件数でトップレベル正誤及び解説先頭に整合させる。
- `law_audit_metadata_incomplete`又は`law_audit_verdict_mismatch`が残るquestionをno-opで完了しない。根拠を確認できない場合は推測で補完せず`hold`にする。
- 一問ごとの判断、根拠、未確認事項を対象年度の監査sidecarへ1行1問で記録する。
- `00_source`と既存IDは変更しない。patchを更新してpatch単体の検証を行う。merge、convert、upload-ready生成、Firestore反映はこのsessionでは行わず、問題整備システムの別工程へ残す。
"""


def build_codex_prompt(
    repo_root: Path,
    review_path: Path,
    question: Mapping[str, Any],
    review: Mapping[str, Any],
) -> str:
    if is_qualification_law_audit(review):
        return _build_qualification_law_audit_prompt(
            repo_root, review_path, question, review
        )

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
        "current_group": "同じqualification・listGroupId内を調査する。ただし、このrunで修正するのは指定された問題だけとし、類似問題は別reviewへ残す。",
        "qualification": "同じqualificationの全listGroupIdを調査する。ただし、このrunで修正するのは指定された問題だけとし、類似問題は別reviewへ残す。",
        "all_qualifications": "全qualificationを検索し、同じ原因と確認できた類似問題も修正する。",
    }
    scope_label = scope_labels.get(scope, scope_labels["current_question"])
    scope_text = scope_instruction.get(scope, scope_instruction["current_question"])
    law_audit_section = _law_audit_instruction() if _is_law_audit_review(review) else ""
    evaluation_snapshot = review.get("evaluationSnapshot")
    rework_section = ""
    if isinstance(evaluation_snapshot, Mapping):
        rework_section = f"""## 独立評価の構造化結果

次は現在の問題内容に対する評価結果だけです。評価threadの会話は引き継がず、指摘と根拠を再確認して必要なpatchだけを修正してください。

```json
{json.dumps(evaluation_snapshot, ensure_ascii=False, indent=2)}
```

"""
    return f"""# 問題整備レビュー対応

問題整備システムで次の指摘が作成されました。review JSONを読み、現行workflowに従って原因調査、必要なpatch修正、検証まで行ってください。

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

{law_audit_section}{selection_section}{rework_section}## 問題文

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
- patch単体のschema又は対象quality-gateを実行する。merge、convert、upload-ready生成はこのsessionで行わず、問題整備システムの別工程へ残す。
- Firestore、Storage、GitHubへ反映しない。
- 調査範囲は「{scope_label}」とする。{scope_text}文言が似ているだけで一括置換せず、問題文と選択肢を結合した判定命題と根拠を個別に確認する。

## 完了時に示すもの

- 変更したpatchとfield
- 判断根拠
- 実行した検証と結果
- merge、convert、upload-ready、Firestoreが未反映であること
"""
