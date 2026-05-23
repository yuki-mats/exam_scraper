# 給水装置工事主任技術者試験 補助ドキュメント

このディレクトリは、給水装置工事主任技術者試験について、出題傾向・解説方針・`category.json` 整備方針を分けて整理したものである。主用途は `03_prompt_add_explanationText.md` の補助であり、`04_prompt_link_questionSetId.md` では `category.json` 見直し時だけ使う。

## 使い分け
- [01_exam_profile.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kyusuikouji-shunin/01_exam_profile.md)
  - 章立て、出題数の偏り、出題形式、学習者が押さえるべき大きな流れ
- [02_explanation_strategy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kyusuikouji-shunin/02_explanation_strategy.md)
  - `03_prompt_add_explanationText.md` から参照する解説文用の補助資料
- [03_category_preparation.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kyusuikouji-shunin/03_category_preparation.md)
  - `category.json` を設計・見直しする際の検討資料

## 推奨読了順
- `03_prompt_add_explanationText.md` のために読むとき:
  1. `01_exam_profile.md`
  2. `02_explanation_strategy.md`
- `04_prompt_link_questionSetId.md` のために読むとき:
  1. まず `category.json`
  2. それだけで不足する場合のみ `03_category_preparation.md`
  3. 大章構成から見直す場合のみ `01_exam_profile.md`

## 前提
- これらは、ユーザー提供の参考書・過去問・章扉・目次・解説ページ写真を基に整理した。
- 日常の `questionSetId` 紐付けは `category.json` が主資料であり、このディレクトリはその整備と解説品質向上のための補助資料である。
