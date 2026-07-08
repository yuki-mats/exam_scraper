# Lawzilla MCP integrated question maintenance workflow

この文書は、Lawzilla MCP を過去問整備ワークフローへ組み込み、解説文の精度を高め、誤った正答・解説を安全に修正するための運用正本です。

API キー、endpoint URL、Bearer token、credential、個人メール、ローカル secret path は、この repo、生成 artifact、送付用レビュー文面に書かないでください。Lawzilla MCP はローカル MCP 設定と環境変数で接続します。

## 目的

- 解説文を、問題文の読解だけでなく条文 locator / evidence hash / 監査済み facts に基づいて作る。
- 既存の条文検索と Lawzilla MCP を並列検証し、条文候補の漏れ、広すぎる根拠、項・号・別表の不足を発見する。
- 正答が誤っている可能性を、次の 2 種類に分けて修正する。
  - **source / parsing / mapping error**: 出題当時の公式正答・解答結果と `correctChoiceText` がずれているもの。
  - **current-law update**: 出題当時の正答は保持しつつ、現行法ベースの学習データとして正誤・解説を更新すべきもの。
- Lawzilla MCP の有用性と限界を継続記録し、既存検索改善と Lawzilla 側への feedback に戻す。

## 原則

- `00_source` は変更しない。元問題文、元選択肢、元正答、出題情報は保存する。
- `correctChoiceText` を推測で変えない。必ず source、公式正答、既存 Firestore ID、条文 evidence、sidecar のどれを根拠にしたかを残す。
- Lawzilla MCP 単独で `verificationStatus="verified"`、`updated_to_current_law`、公開用の正答更新を確定しない。
- question doc に条文本文の長文を持たせない。本文は整備用 evidence cache に保存し、question doc は locator / hash / summary を持つ。
- アプリ実行時に Lawzilla MCP を呼ばない。問題整備時の evidence 取得、検証、品質改善に限定する。
- 出題当時正答と現行法ベース正答を混同しない。
- `updated_to_current_law` は `reviewState="tertiary_verified"` になってから公開確定する。

## Lawzilla MCP の役割

Lawzilla MCP は、既存 e-Gov / corpus ルートの置き換えではなく、並列検証レイヤーとして扱います。

| 用途 | 使い方 | 最終判断 |
| --- | --- | --- |
| 条文候補探索 | 問題文・選択肢・既存解説から関連法令や条項候補を探す | 既存 evidence で照合する |
| 根拠漏れ検出 | 既存 `lawReferences` にない候補が返るかを見る | `candidate` として記録し、照合後に `verified` |
| 粒度改善 | 条だけでなく項・号・別表まで絞れるかを見る | 既存 corpus / e-Gov / 手動確認で確定 |
| 解説改善 | 条文上の判断軸、主体、手続、数値基準を補足する | `lawRevisionFacts.evidenceSummary` に反映 |
| 既存検索改善 | 略称、検索語、条番号正規化、資格別 scope の不足を抽出する | policy / query builder へ還元 |
| feedback | 実務に耐えうる点・不足点を蓄積する | 定期 summary を送付候補にする |

Lawzilla MCP は現行法中心の補助情報として使います。過去法令、附則、経過措置、施行日、改正法令名が正誤に関係する場合は、既存の三段階監査へ戻します。

## 全体フロー

### 0. Scope 固定

対象資格、`list_group_id`、対象ファイル、作業目的を固定します。

- 通常整備: `01` から `04` の過去問整備。
- 法令整備: `02b` で法令コンテキストを固定してから `03` の解説へ進む。
- 現行法監査: `03b` で一次・二次・三次を分ける。

対象外の既存差分、他年度の生成物、別資格の patch は触りません。

### 1. Source と既存 ID を保護

`00_source`、既存 Firestore question document ID、`originalQuestionId`、source conflict ledger を確認します。

ここで分けます。

- source 自体が怪しい: source conflict / hold。
- source は正しいが parse / mapping が怪しい: `correctChoiceText` 修正候補。
- source 正答と現行法の学習上の扱いが違う可能性: `03b` 候補。

### 2. 正答の初期整合

`15_correctChoiceText_fixed` で、設問意図、公式正答、選択肢数、`answer_result_text` を整合させます。

修正できるもの:

- 公式解答との mapping error。
- `select_correct` / `select_incorrect` の取り違え。
- grouped choice / true_false / flash_card の形式由来の配列ずれ。

修正してはいけないもの:

- 現行法では違う気がする、という理由だけの正答変更。
- Lawzilla MCP の回答だけを根拠にした正答変更。

### 3. 02b 法令コンテキスト準備

`20_merged_1` を入力に、`18_law_context_prepared` を作ります。

1. 資格別 law reference policy を読む。
2. 問題・選択肢ごとに `isLawRelated` を判定する。
3. 既存検索 / e-Gov / corpus で `lawReferences` 候補を作る。
4. Lawzilla MCP を同じ問題・選択肢で並列照会する。
5. 一致・不一致・追加候補を review artifact に残す。

出力方針:

- 一致している候補: `lawReferences` に反映し、既存 evidence で verified にできるか確認する。
- Lawzilla だけが出した候補: `candidate` / `unverified` として扱い、根拠照合へ回す。
- 不一致: `lawContextForExplanation` に未確定点を残し、`hold` または `needs_secondary_review` へ回す。

### 4. Evidence 取得と固定

既存 verified `lawReferences` から条文 snapshot を取得し、Lawzilla MCP の照会結果も別 artifact として保存します。

主な保存先:

```text
output/<qualification>/law_evidence/<list_group_id>/current_article_snapshots/
output/<qualification>/review/lawzilla_mcp_feedback/
output/<qualification>/review/law_revision_audit/
```

保存する情報:

- `lawId`, `lawTitle`, `article`, `paragraph`, `item`, `subitem`
- `referenceDate`, `source`, `verificationStatus`, `comparisonStatus`
- `articleTextHash`, `rawXmlHash` または raw MCP response hash
- `questionId`, `originalQuestionId`, `choiceIndex`
- 既存 evidence と Lawzilla evidence の比較結果

条文本文は整備用 evidence cache に保存し、Firestore question doc には locator / hash / summary を残します。

### 5. Evidence 比較

既存ルートと Lawzilla MCP の結果を、次のように分類します。

| 状態 | 判断 | 次アクション |
| --- | --- | --- |
| 同じ法令・同じ条項 | confidence を上げる | 解説精度改善へ使う |
| 同じ法令だが Lawzilla の方が細かい | 項・号・別表候補 | 既存 evidence で照合して `lawReferences` を更新 |
| Lawzilla が追加候補を出す | 根拠漏れ候補 | `candidate` として review queue へ |
| 既存 evidence と矛盾 | 危険 | `hold` / `needs_secondary_review` |
| Lawzilla が no hit / too broad | Lawzilla feedback 候補 | 既存 evidence を優先 |
| 既存検索が no hit で Lawzilla が hit | 既存検索改善候補 | alias / query / scope / normalization へ還元 |

### 6. 03 解説作成

`03` では、`02b` と evidence 比較結果を使って `explanationText`、`suggestedQuestions`、`suggestedQuestionDetails` を作ります。

解説文のルール:

- 正誤の理由を条文上の主体、手続、時期、数値、定義、例外に結びつける。
- 根拠未確定の条文を断定しない。
- 法改正・出題当時との差分が疑われる場合は、解説内で断定せず `03b` に送る。
- Lawzilla MCP が示した表現は、既存 evidence と一致した場合だけ説明の補強に使う。

### 7. 03b 現行法監査

法令関連問題は、差分がある問題だけでなく全件を `lawRevisionFacts` 作成対象にします。

三段階で扱います。

1. **一次監査**: 現行法 evidence bundle を取得し、`articleTextHash`、raw hash、暫定判断、Lawzilla 比較結果を固定する。
2. **二次監査**: 一次 evidence に基づき、正答・解説・差分説明が条文と矛盾しないか確認する。
3. **三次確定**: `updated_to_current_law`、一次/二次不一致、高リスク判断を最終承認する。

`same_as_current` / `not_law_related` は二次確認後に確定できます。`updated_to_current_law` は三次確定後だけ公開確定します。

### 8. 誤答修正の分岐

正答が間違っている可能性を検出したら、必ず分岐を明示します。

| 誤りの種類 | 例 | 修正先 | 必要な根拠 |
| --- | --- | --- | --- |
| source / parse error | 公式正答は 2 なのに `correctChoiceText` が 1 扱い | `15_correctChoiceText_fixed` または後続補正 patch | source / answer_result / 既存 ID mapping |
| questionIntent error | 誤っているものを選ぶ問題を正しいもの扱い | `15_correctChoiceText_fixed` | 設問文と公式正答 |
| explanation-only error | 正答は合っているが理由が条文と違う | `21_explanationText_added` | verified evidence / Lawzilla 比較 |
| lawReferences error | 法令名・条番号・項号が違う | `18_law_context_prepared` / `21_explanationText_added` | 既存 evidence + sidecar |
| current-law update | 出題当時は正しいが現行法では違う | `03b` sidecar + tertiary 後の正誤/解説 patch | 三段階監査 |
| evidence gap | 根拠条文が特定できない | `hold` | 未確定理由と追加取得 queue |

### 9. Convert / upload 前の検証

最低限、次を通します。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --require-law-context-stage \
  --require-is-law-related \
  --require-law-grounded-flag \
  --require-law-revision-facts \
  --require-law-references-for-law-related
```

公開前は、法令関連問題に `hold` を残さない方針なら次も追加します。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --mode firestore \
  --skip-upload-dry-run \
  --require-law-revision-facts \
  --fail-on-law-revision-hold \
  --require-law-revision-evidence-summary \
  --require-law-references-for-law-related
```

既存の repo-wide patch coverage failure が混ざる場合は、今回追加した Lawzilla / lawRevision artifact の妥当性と、既存 hygiene failure を分けて報告します。

### 10. Lawzilla feedback と既存検索改善

Lawzilla MCP を使ったケースは、`lawzilla_mcp_practical_review_workflow.md` の schema で記録します。

既存検索へ還元する分類:

- `alias_needed`: 資格別 law reference policy に略称や法令名を追加。
- `query_rewrite_needed`: 検索 query / prompt を改善。
- `article_normalization_needed`: 条番号、枝番号、別表番号の正規化を改善。
- `scope_doc_needed`: 対象法令スコープを明記。
- `none`: 改善不要、または Lawzilla 側 feedback のみ。

定期的に 20 から 30 ケース、または 1 資格 1 年度ごとに summary を作り、実務利用上の良かった点・不足点・改善要望を整理します。

## 成功条件

- `00_source` と既存 document ID を壊していない。
- `correctChoiceText` の変更理由が source / current-law update / explanation-only のどれか明示されている。
- 法令関連問題に `isLawRelated`、`lawGroundedExplanationNotNeeded=false`、必要な `lawReferences` がある。
- verified 根拠は locator と hash で再現できる。
- Lawzilla MCP の結果は既存 evidence と比較され、単独断定になっていない。
- `explanationText` は条文上の判断軸に沿っており、未確認事項を断定していない。
- `updated_to_current_law` は三次確定済みで、出題当時正答と現行法ベースの扱いが区別されている。
- Lawzilla feedback と既存検索改善候補が蓄積されている。

## 実装順序

1. 既存の `02b` / `03b` / quality gate を維持したまま、Lawzilla MCP の結果を sidecar に保存する。
2. sidecar と既存 evidence の比較 report を作る。
3. 一致ケースだけを解説精度改善へ使う。
4. 不一致ケースを `hold` / `needs_secondary_review` に送る。
5. 既存検索改善候補を資格別 policy と query 正規化へ戻す。
6. 十分に安定してから、`tools/question_bank` の正式サブコマンドとして日常運用へ入れる。
