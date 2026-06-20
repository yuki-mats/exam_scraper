# 公害防止管理者 補助ドキュメント

このディレクトリは、`kougai` の 01〜04 作業を安定させるための資格固有メモである。  
主に `03_prompt_add_explanationText.md` と `04_prompt_link_questionSetId.md` の前提として読む。

## 使い分け

- `01_exam_profile.md`
  - 2010〜2025 の yaku-tik 過去問がどんな構造で並んでいるかを整理する。
- `02_explanation_strategy.md`
  - `explanationText` を一問ずつ確認するときの書き方と、穴埋め true/false 問題の扱い方をまとめる。
- `03_category_preparation.md`
  - `category.json` の questionSet 粒度と境界ルールを決める。

## この資格の前提

- canonical source は yaku-tik のみを使う。
- 2010〜2025 の全年度で、questionLabel / source_question_id の prefix が安定している。
- 問題形式は全問 `true_false` だが、穴埋め由来の設問が多い。
- 04 の `questionSetId` は、年度ではなく topic prefix で固定する。

## 参照順

1. `01_exam_profile.md`
2. `02_explanation_strategy.md`
3. `03_category_preparation.md`

