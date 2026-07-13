# 二級土木施工管理技士試験 補助ドキュメント

このディレクトリは、二級土木施工管理技士試験について、出題傾向・解説方針・`category.json` 整備方針・法令参照方針を分けて整理したものである。主用途は `03_prompt_add_explanationText.md` の補助であり、`04_prompt_link_questionSetId.md` では `category.json` 見直し時だけ使う。

## 使い分け
- [01_exam_profile.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-doboku-sekou/01_exam_profile.md)
  - 試験全体の章立て、年度別の形式差、出題タイプ、見落としやすい構成上の特徴
- [02_explanation_strategy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-doboku-sekou/02_explanation_strategy.md)
  - `03_prompt_add_explanationText.md` から参照する解説文用の補助資料
- [03_category_preparation.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-doboku-sekou/03_category_preparation.md)
  - `category.json` を設計・見直しする際の検討資料
- [04_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-doboku-sekou/04_law_reference_policy.md)
  - 法令・政令・省令・公的基準をどこまで解説根拠に使うかの方針

## 推奨読了順
- `03_prompt_add_explanationText.md` のために読むとき:
  1. `01_exam_profile.md`
  2. `02_explanation_strategy.md`
  3. 法令問題なら `04_law_reference_policy.md`
- `04_prompt_link_questionSetId.md` のために読むとき:
  1. まず `category.json`
  2. それだけで不足する場合のみ `03_category_preparation.md`
  3. 大章構成や近年の形式差まで見直す場合のみ `01_exam_profile.md`

## 前提
- これらは、取得済み `00_source`、保存済み `question_images`、`output/2nd-class-doboku-sekou/category/category.json`、およびユーザー提供PDFを基に整理した。
- 日常の `questionSetId` 紐付けは `category.json` が主資料であり、このディレクトリはその整備と解説品質向上のための補助資料である。
