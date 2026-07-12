# 現行法監査とLawzilla MCP

この文書は、法令関連問題のevidence取得、現行法監査、正誤更新の正本です。field構造は[question field契約](../reference/question_field_contract.md)、具体的なpatch作業は[02b](../../prompt/02b_prompt_prepare_law_context.md)と[03b](../../prompt/03b_prompt_audit_current_law_and_patch.md)に従います。

## 適用範囲

- 02bで法令関連性と根拠候補を準備する。
- 03bで法令関連問題を一問一肢ずつ監査する。
- 年次再監査又は`auditMethodVersion`変更時に全対象を洗い替える。
- 出題当時の公式正答と現行法ベースの学習上の正誤を分ける。

通常の01から04の順序や保存先はここへ複製せず、[幹](exam_pipeline_manual_and_automation.md)と[artifact契約](artifact_contract.md)を参照します。

## 原則

- `00_source`と既存IDを変更しない。
- 問題文と各選択肢を結合した完全命題を、一問一肢ずつ確認する。
- `correctChoiceText`を既存メタデータ、類似問題、検索結果だけで反転させない。
- Lawzilla MCP単独で`verified`又は`updated_to_current_law`を確定しない。
- 条文本文はevidence cacheへ保存し、question docにはlocator、hash、要約を残す。
- 判断不能は`hold`又は`needs_secondary_review`へ戻す。
- `updated_to_current_law`は`tertiary_verified`後だけ公開確定する。

## evidence sourceの役割

| source | 用途 | 確定できること |
| --- | --- | --- |
| e-Gov・官公庁一次資料 | 法令名、条・項・号、施行日、本文確認 | `verified`の直接根拠。 |
| Firestore条文検索・整備済みcorpus | 既存locator、revision、hashとの照合 | 固定snapshotとの一致。 |
| Lawzilla MCP | 条文候補、関連条項、項号・別表の探索 | candidateの発見。単独確定は不可。 |
| 公式問題・公式解答 | 出題時の正答と問題構造 | `examTime.correctChoiceText`。 |
| 資格別law policy | 対象法令、許可source、過去法令方針 | 監査scope。 |

LawzillaとFirestore条文検索は同じ問題・選択肢に対して使い、法令名、条・項・号、条件、例外を本文で照合します。片方がno-hitでも、もう片方の回答を無条件に採用しません。

## 監査手順

### 1. Scope

qualification、全listGroupId又は対象group、基準日、資格別law policyを固定します。資格全体の洗い替えでは、対象ファイル一覧を固定してから一問ずつ処理します。

### 2. Evidence bundle

各選択肢について次を固定します。

- 問題・選択肢・現行正誤・出題時正誤。
- `lawId`、法令名、条・項・号、必要な別表・附則。
- `referenceDate`、revision、施行日。
- 条文snapshotの`articleTextHash`とraw response hash。
- LawzillaとFirestore条文検索の照合結果。

固定入力全体から`auditInputHash`を作り、後段も同じbundleを参照します。

### 3. 一次監査

evidenceを取得し、暫定verdictと不足点を記録します。根拠不足は推測せず`needs_secondary_review`又は`hold`です。

### 4. 二次監査

一次bundleを変えずに、正答、解説、根拠locator、差分説明の妥当性を再確認します。`same_as_current`と`not_law_related`は、根拠が十分なら`secondary_verified`で確定できます。

### 5. 三次確定

`updated_to_current_law`、一次・二次不一致、高リスク判断を確定します。確定前の正答変更は公開しません。

## 状態

| `auditStatus` | 意味 | 公開条件 |
| --- | --- | --- |
| `same_as_current` | 出題時正答と現行法判定が同じ | `secondary_verified`以上。 |
| `updated_to_current_law` | 現行法に合わせ正誤又は説明を更新 | `tertiary_verified`必須。 |
| `not_law_related` | 法令監査対象ではない | 根拠付き`secondary_verified`。 |
| `hold` | evidence又は方針不足 | 公開不可。 |

監査結果には`auditedAt`、`nextAuditDueAt`、`auditMethodVersion`、`auditInputHash`、`lawCorpusSnapshotId`と各run IDを残します。

## 更新先

| 変更 | 更新先 |
| --- | --- |
| 法令関連性・根拠候補 | `18_law_context_prepared` |
| 現行法で確定した正誤 | `23_correctChoiceText_fixed` |
| 解説・想定質問・監査facts | `21_explanationText_added` |
| 監査履歴と未確認事項 | `output/<qualification>/review/law_revision_audit/` |
| 条文本文・raw hash | `output/<qualification>/law_evidence/<list_group_id>/` |

正誤を更新した場合は`23`をmergeして`20_merged_1`へ反映し、その値を前提に03を再生成します。出題当時の正答は`lawRevisionFacts.examTime`へ保持します。

## 公開前条件

- `isLawRelated=true`の全問題に必要な`lawReferences`と`lawRevisionFacts`がある。
- トップレベル正答、`lawRevisionFacts.current.correctChoiceText`、解説先頭が一致する。
- `hold`と未完了review stateが残っていない。
- evidence summaryから主要根拠を追跡できる。
- [delivery workflow](delivery_workflow.md)のquality-gateとupload dry-runが通る。

Lawzilla自体の検索品質や改善要望は[practical review workflow](lawzilla_mcp_practical_review_workflow.md)へ記録し、問題監査factsへ評価メモを混ぜません。
