# Lawzilla MCP practical review workflow

この文書は、Lawzilla MCP を `exam_scraper` の法令根拠整備に並列利用しながら、「実務に耐えうるか」という観点で継続レビューし、Lawzilla 側へ定期的にフィードバックできる形で情報を蓄積するための運用正本です。

API キー、エンドポイント URL、Bearer token、個人メール、ローカル secret path は、この repo、review artifact、送付文面に書かないでください。設定値はローカルの MCP 設定と環境変数で扱います。

## 位置づけ

- 既存の e-Gov / 整備済み corpus / `lawReferences` / `lawRevisionFacts` は正本のまま維持する。
- Lawzilla MCP は、条文候補探索、既存根拠との突合、解説文の根拠漏れ検出、既存検索ロジック改善のための並列検証レイヤーとして使う。
- Lawzilla MCP 単独で `correctChoiceText`、`updated_to_current_law`、`verificationStatus="verified"` を確定しない。
- 現行法中心の補助情報として扱い、過去法令、附則、施行日、改正経緯が必要な判断は既存の三段階法令監査へ戻す。
- アプリ実行時に Lawzilla MCP を呼び出さない。問題整備時の evidence 取得と品質改善に限定する。

## 蓄積する review artifact

資格・年度単位の作業では、次の場所に JSONL と summary を残します。

```text
output/<qualification>/review/lawzilla_mcp_feedback/
```

推奨ファイル名:

```text
<list_group_id>_lawzilla_mcp_review_<timestamp>.jsonl
<list_group_id>_lawzilla_mcp_review_<timestamp>_summary.md
```

`output/` 配下は通常生成物なので、共有や commit が必要な場合だけ対象ファイルを明示して扱います。レビュー送付用に整形する場合は、API キー、endpoint、ローカル絶対パス、非公開 credential、不要な個人情報を除去した markdown を作ります。

## JSONL record schema

1 行 1 検証ケースにします。問題単位または選択肢単位で、実務判断に必要な粒度を優先します。

```json
{
  "schemaVersion": "lawzilla-mcp-practical-review/v1",
  "reviewedAt": "YYYY-MM-DDTHH:MM:SS+09:00",
  "reviewer": "codex",
  "aiTool": "OpenAI Codex",
  "qualification": "<qualification>",
  "listGroupId": "<list_group_id>",
  "examYear": 2024,
  "originalQuestionId": "<originalQuestionId>",
  "questionId": "<questionId or empty>",
  "choiceIndex": 0,
  "workflowStage": "02b_law_context | 03_explanation | 03b_law_audit | search_improvement",
  "questionSummary": "送付可能な範囲に丸めた設問要約",
  "existingEvidence": {
    "source": "egov_or_existing_corpus",
    "lawReferences": [],
    "articleTextHash": "sha256..."
  },
  "lawzillaEvidence": {
    "querySummary": "実行した質問・検索意図の要約。APIキーやendpointは書かない。",
    "returnedLawTitle": "",
    "returnedArticle": "",
    "returnedParagraph": "",
    "articleTextHash": "sha256...",
    "rawResponseHash": "sha256...",
    "resultType": "matched | additional_candidate | mismatch | no_hit | too_broad | tool_error"
  },
  "comparison": {
    "agreement": "same_article | same_law_different_article | lawzilla_more_precise | existing_more_precise | conflict | inconclusive",
    "impactOnExplanation": "improved | no_change | hold_required | existing_search_improvement_needed",
    "impactOnExistingSearch": "alias_needed | query_rewrite_needed | article_normalization_needed | scope_doc_needed | none"
  },
  "practicalEvaluation": {
    "verdict": "usable | usable_with_review | not_enough_for_final_judgment | blocked",
    "confidence": "high | medium | low",
    "strengths": [],
    "concerns": [],
    "requestedImprovements": []
  },
  "feedbackDraft": {
    "questionContent": "Lawzilla側へ共有してよい範囲の質問内容",
    "aiAnswerSummary": "回答内容の要約",
    "businessUseEvaluation": "実務に使えるレベルか",
    "goodPoints": [],
    "issuesAndRequests": [],
    "otherComments": []
  }
}
```

## 評価軸

レビューは「当たった / 外れた」だけでなく、過去問整備で使えるかを分けて記録します。

| 評価軸 | 見ること |
| --- | --- |
| 条文探索 | 正しい法令に到達できるか。略称、制度名、問題文の言い換えに耐えるか。 |
| 条項精度 | 条だけでなく項・号・別表まで絞れるか。広すぎる根拠を返していないか。 |
| 本文取得 | 現行法本文、別表、主要規則を安定して取得できるか。本文の欠落がないか。 |
| 解説適合 | 正誤判断や解説文に必要な条文事実を過不足なく示せるか。 |
| 差分検出 | 既存 e-Gov / corpus と不一致のとき、追加確認が必要な形で分かるか。 |
| 実務安全性 | 推測断定、附則・過去法令・施行日不足、条文の読み替えミスを抑制できるか。 |
| 運用性 | レスポンスの再現性、レスポンス量、レビューしやすさ、batch 作業への適性。 |

## 判定ラベル

- `usable`: 既存 evidence と一致し、解説精度向上に使える。
- `usable_with_review`: 候補探索や補助説明には有用だが、最終確定には既存 evidence 照合が必要。
- `not_enough_for_final_judgment`: 関連候補は出るが、条項精度・本文根拠・施行日等が不足する。
- `blocked`: 誤条文、根拠不明、tool error、再現不能、回答が広すぎる等で実務利用に支障がある。

## 作業フロー

1. 既存ルートで `lawReferences` 候補または `lawRevisionFacts` を作る。
2. 同じ設問・選択肢について Lawzilla MCP に条文候補と根拠説明を照会する。
3. 既存 evidence と Lawzilla evidence を比較し、JSONL record に差分を残す。
4. 一致した場合は、解説文の表現精度や条項粒度の改善に使う。
5. Lawzilla がより良い候補を出した場合は、既存検索の改善候補として `impactOnExistingSearch` に分類する。
6. 不一致の場合は、`hold` または `needs_secondary_review` に回し、推測で正答・解説を確定しない。
7. 一定件数ごとに summary を作り、送付テンプレートに沿って Lawzilla 側へフィードバックする。

## 定期フィードバック単位

最初は小さく、20 から 30 ケースごと、または 1 資格 1 年度ごとに送付用 summary を作ります。

summary には次を含めます。

- 利用した AI: `OpenAI Codex`
- 質問内容: 代表的な照会パターンと、問題整備上の目的
- AI の回答内容: 成功例、追加候補例、不一致例、tool error 例の要約
- 実務に使えるレベルか: 判定ラベルの件数、使える場面、最終判断に使えない場面
- 良かった点: 既存検索より良かった条文探索、項・号粒度、解説補助
- 気になった点・改善要望: 過去法令、附則、施行日、別表、根拠範囲、レスポンス形式など
- その他コメント: batch 利用で必要な field、hash / locator、再現性、レスポンス量

## 既存検索改善への還元

Lawzilla MCP の結果は、次の改善候補へ分類して backlog 化します。

| `impactOnExistingSearch` | 還元先 |
| --- | --- |
| `alias_needed` | `prompt/qualification_docs/<qualification>/*law_reference*.md` の略称・対象法令表 |
| `query_rewrite_needed` | 既存検索 query builder / prompt の検索語 |
| `article_normalization_needed` | 条番号、枝番号、別表番号の正規化処理 |
| `scope_doc_needed` | 資格別 law reference policy の対象法令スコープ |
| `none` | 改善不要、または Lawzilla 側フィードバックのみ |

この分類により、Lawzilla の評価だけで終わらせず、既存の条文検索精度改善へ戻します。

## 送付前チェック

- API キー、endpoint、Bearer token、cookie、credential、ローカル secret path が含まれていない。
- 問題文や解説文を共有する場合、必要最小限に丸めている。
- Lawzilla MCP の回答を長文で丸ごと転載していない。要約と短い該当箇所に留める。
- 「Lawzilla 単独で正答確定した」と誤解される書き方をしていない。
- 不一致例は、既存 evidence 側の根拠 hash / locator とセットで説明している。
