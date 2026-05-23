# 介護福祉士試験 補助ドキュメント

このディレクトリは、介護福祉士試験について、出題傾向・解説方針・`category.json` 整備方針を分けて整理したものである。主用途は `03_prompt_add_explanationText.md` の補助であり、`04_prompt_link_questionSetId.md` では `category.json` 見直し時だけ使う。

この資格で目指す解説は、次の3点を同時に満たすものである。
1. その問題について理解できる
2. 介護福祉士試験らしい出題傾向やひっかけに気づける
3. 類題でも再利用できる判断軸が残る

## 使い分け
- [01_exam_profile.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kaigofukushi/01_exam_profile.md)
  - 新旧科目の対応、出題形式、学習者が押さえるべき大きな判断軸
  - 介護福祉士で受験者がどこで迷いやすいかという全体傾向
- [02_explanation_strategy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kaigofukushi/02_explanation_strategy.md)
  - `03_prompt_add_explanationText.md` から参照する解説文用の補助資料
  - 「何を書くと学習者がハッとするか」という内省結果を整理した正本
- [03_category_preparation.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/kaigofukushi/03_category_preparation.md)
  - `category.json` を設計・見直しする際の検討資料

## この資格で先に整理しておくべきこと
介護福祉士の `qualification_docs` では、少なくとも次を明文化しておくと、解説文の質が安定する。

1. この資格で頻出の判断軸
  - 本人意思、自立支援、安全配慮、制度主体、症状と支援の対応など
2. 一見もっともらしい誤答パターン
  - 丁寧そうだが主題がずれる支援
  - 安全を理由にした過介助
  - 家族の意向優先
3. 学習者がつまずきやすい概念の境界
  - 似た概念をどう切り分けるか
4. 科目ごとに足すべきプラスアルファ情報
  - 制度なら主体・対象・要件
  - 事例なら優先順位
  - 身体・疾患なら観察と援助へのつながり
5. 「ハッとする情報」の型
  - なぜその誤答が魅力的に見えるのか
  - 正答は何を優先しているのか
  - 類題でも再利用できる判断軸は何か
6. その資格らしい出題の癖
  - 何を問うときに、どの価値や視点が優先されやすいか
  - どういう一般論が誤答として紛れ込みやすいか

## 推奨読了順
- `03_prompt_add_explanationText.md` のために読むとき:
  1. `01_exam_profile.md`
  2. `02_explanation_strategy.md`
- `04_prompt_link_questionSetId.md` のために読むとき:
  1. まず `category.json`
  2. それだけで不足する場合のみ `03_category_preparation.md`
  3. 大章構成から見直す場合のみ `01_exam_profile.md`

## 前提
- これらは、`output/kaigofukushi/questions_json/` 配下の `20_merged_1` を前提に整理した。
- 介護福祉士では、解説の主参照は `explanation_common_prefix` とし、`explanation_choice_snippets` は補助素材として使う。
- 日常の `questionSetId` 紐付けは `category.json` が主資料であり、このディレクトリはその整備と解説品質向上のための補助資料である。
