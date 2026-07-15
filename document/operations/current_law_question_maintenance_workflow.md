# 現行法監査

この文書は、法令関連問題の根拠取得、現行法監査、patch更新の正本です。fieldは[question field契約](../reference/question_field_contract.md)、patch作業は[02b](../../prompt/02b_prompt_prepare_law_context.md)と[03b](../../prompt/03b_prompt_audit_current_law_and_patch.md)に従います。

## 原則

- `00_source`と既存IDを変更しない。
- 問題文と各選択肢を結合した完全命題を、一問一肢ずつ確認する。
- 既存の正誤、解説、法令metadata、検索要約だけで正誤を確定しない。
- e-Gov法令検索又は所管官庁の一次情報で、法令名、条・項・号、施行日、本文を確認する。
- 根拠不足は`hold`又は`needs_secondary_review`にする。
- `updated_to_current_law`は`tertiary_verified`後だけ公開確定する。
- 03bの判断方法が変わる場合は03bの作業版だけを上げ、現行版で法令問題を洗い替える。問題単位の`auditMethodVersion`は使用した監査方式の証跡であり、作業版の代わりにしない。

Codex App Serverでは、組み込みweb検索を一次情報の入口として使います。外部MCP、Lawzilla、Firestore条文検索は使いません。保存済みの`lawReferences`、`lawRevisionFacts`、evidence cacheは候補として読み、一次情報と一致した場合だけ根拠にします。

## 監査

1. qualification、対象listGroupId、基準日、資格別law policyを固定する。
2. 各選択肢の完全命題と、法令名、条・項・号、施行日、locator、本文hashをまとめる。
3. 一次監査でevidence bundleと暫定判定を作る。
4. 二次監査で同じbundleを使い、正答、解説、locator、差分説明を再確認する。
5. 正答変更、一次・二次不一致、高リスク判断は三次確定へ回す。

`auditInputHash`、`lawCorpusSnapshotId`、一次・二次・三次のrun IDを残し、別phaseで入力を変えません。

## 状態

| `auditStatus` | 意味 | 公開条件 |
| --- | --- | --- |
| `same_as_current` | 出題時正答と現行法判定が同じ | `secondary_verified`以上 |
| `updated_to_current_law` | 現行法に合わせ正誤又は説明を更新 | `tertiary_verified` |
| `not_law_related` | 法令監査対象ではない | 根拠付き`secondary_verified` |
| `hold` | evidence又は方針不足 | 公開不可 |

出題当時の公式正答は`lawRevisionFacts.examTime`へ保持し、現行法判定と混同しません。出題当時の条文を確認できない場合は、その事実を明記して推測を避けます。

## 保存先

| 内容 | 保存先 |
| --- | --- |
| 法令関連性・根拠候補 | `18_law_context_prepared/` |
| 現行法で確定した正誤 | `23_correctChoiceText_fixed/` |
| 解説・監査facts | `21_explanationText_added/` |
| 監査履歴・未確認事項 | `output/<qualification>/review/law_revision_audit/` |
| 既存の条文cache | `output/<qualification>/law_evidence/<list_group_id>/`（App Serverからは更新しない） |

Codex App Serverの整備sessionが更新するのは、対象問題の`18` / `21` / `23` patchと対象年度の監査sidecarだけです。`law_evidence`、`merge`、`convert`、`upload-ready`、Firestoreは変更せず、必要な後続処理は問題整備システムの別工程で実行します。

## 公開前条件

- 必要な`lawReferences`と`lawRevisionFacts`がある。
- トップレベル正答、`lawRevisionFacts.current.correctChoiceText`、解説先頭が一致する。
- `hold`と未完了review stateがない。
- evidenceから公的一次情報を追跡できる。
- [delivery workflow](delivery_workflow.md)のquality-gateとupload dry-runが通る。
