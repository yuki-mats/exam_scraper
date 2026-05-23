# `gassyunin.com` スクレイプ方針メモ

更新日: 2026-05-07

## 結論

`gas-shunin-otsu` の `gassyunin.com` スクレイパーでは、選択肢系の一次情報は `詳細` 内の `各選択肢の判定` セクションを正本として扱う。

- `choiceTextList`
- `choiceTextMarkedList`
- `correctChoiceText`
- `explanation_choice_snippets`
- `judgeChoiceMarkers`

上記はすべて `各選択肢の判定` 配下から取得する。

`questionBodyText` は別扱いとし、`<h2>問N</h2>` の直後から最初の選択肢記号が始まる直前までを取得する。

## この方針を採用した理由

### 1. 原本 PDF と照合した結果、`各選択肢の判定` 側の順序が正しかった

手元の原本 PDF で以下を確認した。

- 令和7年度乙種: `/Users/yuki/Downloads/q_otsu_R7.pdf`
- 令和6年度乙種: `/Users/yuki/Downloads/q_otsu_r6.pdf`

確認用のスクリーンショットは以下に保存した。

- 令和7年度: `/Users/yuki/development/exam_scraper/scratch/pdf_preview/`
- 令和6年度: `/Users/yuki/development/exam_scraper/scratch/pdf_preview_r6/`

確認結果:

- 令和7年度 法令 `問1` は、原本 PDF の `(1)〜(5)` の順序が `gassyunin` の `各選択肢の判定` 側と一致した
- 令和7年度 法令 `問2〜問4` は、原本 PDF の `(イ)(ロ)(ハ)(ニ)(ホ)` の順序が `gassyunin` の `各選択肢の判定` 側と一致した
- 令和6年度 法令 `問1〜問5` でも同様に一致した

### 2. 問題本文側はサイト上で崩れているケースがある

少なくとも `2025` 法令 `問1` では、問題本文側の `(1)〜(5)` と `各選択肢の判定` 側の本文対応が一致していなかった。

このため、問題本文側の選択肢を正本にして補正するより、`各選択肢の判定` 側を正本にした方が安定する。

## 実装ルール

### `questionBodyText`

`<h2>問N</h2>` の次から走査し、最初の選択肢記号が出る直前までの行を連結して使う。

対象の選択肢記号:

- `(1)` 形式
- `(イ)` 形式
- `イ` 形式

### 選択肢系フィールド

`詳細` 内の `h3: 各選択肢の判定` の次から、次の `h3` に当たるまでの `div.statement-judge-correct|wrong` を順番に読む。

- `blockquote` -> `choiceTextList`
- `blockquote` の `kw-wrong-inline` を `[wrong]...[/wrong]` にしたもの -> `choiceTextMarkedList`
- `judge-header` の verdict -> `correctChoiceText`
- `correct-text-line` と `judge-meta` -> `explanation_choice_snippets`

### 正解番号

`🎯 正解: (n)` から `answer_result_text` と `answer_result_inferred_correct_choice_numbers` を作る。

正解番号のリマップは行わない。

## 監査用フィールド

目視確認・後工程向けに以下を残す。

- `questionChoiceMarkers`: 問題本文から見つけた選択肢記号
- `judgeChoiceMarkers`: `各選択肢の判定` から見つけた選択肢記号
- `choiceMarkerSource`: 現在は基本的に `judge`
- `markerAlignmentMode`
  - `judge_matches_question_markers`
  - `judge_priority_mismatch`
  - `judge_only`
  - `question_only`
- `markerMismatchDetected`: 問題本文側と `judge` 側の記号列が一致しなかったか
- `answerResultNumbersRemapped`: 現在は常に `false`

## 今後この方針を見直す条件

以下のどれかが起きたら、`各選択肢の判定` 正本方針を再確認する。

- 新年度で原本 PDF と `judge` 側の順序が不一致
- `judge` 側に選択肢本文の省略・要約が増える
- `markerMismatchDetected=true` の件数が急増する
- `judge` セクション自体が欠落した問題が増える

## AI エージェント向け要点

- `gassyunin` では「問題本文の選択肢を復元する」ことを目標にしない
- 「学習データとして安定して取れること」を優先し、`各選択肢の判定` を正本にする
- 迷ったら、まず原本 PDF と `judge` 側の順序を spot check する
