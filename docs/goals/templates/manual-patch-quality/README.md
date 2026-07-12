# 手作業 patch 品質向上 goal テンプレート

このテンプレートは、`exam_scraper` 配下の過去問について、各設問を「対象資格の専門家、問題作成者、参考書著者の観点で一問ずつ精査し、必要なら検索しながら精度を上げる」ための GoalBuddy 用雛形です。

スクレイピングから upload までの全体フローは [exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md) を正本にします。このテンプレートは、そのうち正答・設問意図・解説品質を一問ずつ厳密レビューする品質改善 goal 用です。

対象にできる patch family は次の4つです。

- `10_questionType_fixed`
- `15_correctChoiceText_fixed`（実質 `questionIntent` パッチ）
- `23_correctChoiceText_fixed`
- `21_explanationText_added`

`22_questionSetId_linked` は全体フロー上は必須ですが、このテンプレートでは主対象にしません。カテゴリ設計や設問分類の見直しは、別 goal として扱うほうが安全です。

## 想定用途

- 新規 patch を最初から高品質で作る
- 既存 patch を再点検して更新する
- 特定資格、特定 `list_group_id`、特定設問群に限定して品質底上げする
- 各設問について、正しい回答と専門家水準の解説品質の両方を確認する

## 重要方針

- 自動生成スクリプトで patch 本文を量産しない
- 1問ずつ、対象資格の専門家・問題作成者・参考書著者の観点で読む
- `1 Worker = 1問` を基本単位にする
- 既存 patch は流用せず、必要なら再判定する
- `correctChoiceText` は `99.99%` を目指す厳密レビュー対象として扱う
- `explanationText` は正誤理由、誤り箇所、正しい内容、必要な根拠を満たすか確認する
- `explanationText` は必要に応じて一次情報を検索してよい
- `question_url` の再取得や、外部の解説サイト依存はしない
- 受験者が誤学習しないか、参考書や公式教材に載せても破綻しないかを確認する

## 専門家レビューの想定

Worker は、単なる一般読者としてではなく、対象資格の専門家・問題作成者・参考書著者として振る舞います。

- 出題者が何を問いたい設問かを確認する
- 正答番号だけでなく、各選択肢がなぜ正しい/間違いなのかを確認する
- 法令、数値、制度、用語定義を推測で補わず、必要なら一次情報で裏取りする
- 受験者が次に同じ論点を見たときに自力で判断できる解説か確認する
- 参考書本文や解答解説として公開しても矛盾や曖昧さが残らないか確認する

## prompt ごとの正しい入力

このテンプレートでは、prompt ごとに入力の正本を分けて扱います。

- `01_prompt_fix_questionType.md`
  - 主入力: `00_source/question_*_*.json`
  - `20_merged_1` などは補助参照に限る
  - 外部Web参照は禁止
  - 出力先: `10_questionType_fixed/`

- `02_prompt_fix_questionIntent.md`
  - 主入力: `20_merged_1/question_*_merged.json`
  - 不足時のみ `00_source/question_*_*.json` を最小限参照
  - 外部Web参照は禁止
  - 出力先: `15_correctChoiceText_fixed/`
  - 注意: `correctChoiceText` を直接判定する prompt ではなく、`questionIntent` 精査用

- `03_prompt_add_explanationText.md`
  - 主入力: `20_merged_1/question_*_merged.json`
  - 必要時のみ `23_correctChoiceText_fixed/`
  - 必要時のみ `00_source/`
  - 必要時のみ信頼できる外部Web一次情報
  - 出力先: `21_explanationText_added/`

## 推奨の切り方

最も安定するのは、1つの goal で 1資格 + 1 `list_group_id` + 明示した設問範囲に絞る運用です。

推奨順:

1. `10_questionType_fixed`
2. `15_correctChoiceText_fixed`
3. merge / correctChoiceText 補完
4. `23_correctChoiceText_fixed`
5. `21_explanationText_added`

`explanationText` は `questionType` と `correctChoiceText` の精度に依存するため、通常は最後に回すほうが安全です。ただし今回の厳密運用では、各 Worker が担当する1問について `correctChoiceText` と `explanationText` の両方を同時に確認してよいです。

## `23_correctChoiceText_fixed` について

実装上は `questionIntent` 補助や `15_correctChoiceText_fixed` の文脈が残っていますが、品質 goal の主対象は最終的な `correctChoiceText` の妥当性です。

`prompt/02_prompt_fix_questionIntent.md` は `correctChoiceText` を直接目視判定する prompt ではありません。役割は `questionIntent` を目視確認することです。厳密レビューでは、`02` を `questionIntent` 確認の基準として使い、そのうえで `answer_result_text` と突き合わせて最終的な `correctChoiceText` の妥当性を確認します。

そのため、このテンプレートでは:

- board の主語は `23_correctChoiceText_fixed`
- 原因調査で `20_merged_1`、`00_source`、`15_correctChoiceText_fixed` は参照可。ただし `00_source` は原本のため更新禁止とし、修正は必ずpatch層へ入れる

という整理を前提にします。

## 使い方

1. このディレクトリを新しい slug へコピーする
2. `goal.md` のプレースホルダを埋める
3. `state.yaml` のプレースホルダを埋める
4. 対象設問ごとに Worker task を複製する
5. `/goal Follow docs/goals/<slug>/goal.md.` で開始する

## コピー後に埋める主な項目

- `qualification_code`
- `qualification_name`
- `list_group_id`
- 対象 patch family
- 対象設問一覧
- tranche の順序
- 検証コマンド

## どの Worker を複製するか

- 設問単位で回答と解説を同時に見直すとき:
  - `T501` 系を複製
- `questionType` だけを先に見直す必要があるとき:
  - `T201` 系を複製
- `questionIntent` / `correctChoiceText` だけを見直す必要があるとき:
  - `T301` 系を複製
- `explanationText` だけを見直す必要があるとき:
  - `T401` 系を複製

厳密運用では `1 Worker = 1問` を基本にします。patch file 単位の Worker は、事前棚卸しや低リスクの補助作業に限定します。

## 保存先

テンプレート本体:

- [goal.md](/Users/yuki/development/exam_scraper/docs/goals/templates/manual-patch-quality/goal.md)
- [state.yaml](/Users/yuki/development/exam_scraper/docs/goals/templates/manual-patch-quality/state.yaml)
