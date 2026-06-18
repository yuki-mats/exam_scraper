# 柔道整復師 補助ドキュメント

柔道整復師過去問の `01〜04prompt` 作業では、このディレクトリの資料を資格別の前提として参照する。

## 参照順

1. `01_exam_profile.md`
2. `02_explanation_strategy.md`
3. `03_category_preparation.md`
4. `04_law_reference_policy.md`

## 対象データ

- 資格コード: `judoseifukushi`
- 取得元: `output/judoseifukushi/questions_json`
- 対象年: 1993〜2026年
- 対象問題数: 7,600問
- category: `output/judoseifukushi/category/category.json`

## 使い分け

- `01` / `02` / `04` は、まずローカル一次情報を土台にする。
- `03` は、`00_source` の `explanation_common_prefix` / `explanation_common_summary` / `explanation_choice_snippets` が欠損または全空で、解説根拠が薄い場合だけ、権威ある一次情報を外部 Web で補強してよい。
- 関係法規や制度問題は、`04_law_reference_policy.md` を先に確認する。
