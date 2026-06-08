# 医師国家試験 補助ドキュメント

このディレクトリは、医師国家試験について、`03_prompt_add_explanationText.md` と `04_prompt_link_questionSetId.md` の判断を安定させるための補助資料である。

主な狙いは次の3点である。

1. 医学知識問題と制度・法令問題を早い段階で切り分ける
2. `lawGroundedExplanationNotNeeded` の判断をぶらさない
3. MHLW ブループリント由来の `category.json` へ違和感なく `questionSetId` を寄せる

## 使い分け

- [01_law_reference_policy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/mecnet-kokushi/01_law_reference_policy.md)
  - `03` で法令ベースの追加解説が必要な問題の境界
  - `lawGroundedExplanationNotNeeded` を `true` / `false` に倒す基準
- [02_law_reference_scope.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/mecnet-kokushi/02_law_reference_scope.md)
  - 医師国家試験で優先的に確認する法令・制度スコープ
  - 判定時に迷いやすい法令名、短縮表記、使う場面、使わない場面
- [03_explanation_strategy.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/mecnet-kokushi/03_explanation_strategy.md)
  - 医師国家試験の解説文で何を短く残すべきか
  - 症例問題、画像問題、制度問題の書き分け
- [04_category_preparation.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/mecnet-kokushi/04_category_preparation.md)
  - `questionSetId` をブループリント名へ寄せるときの優先順位

## 運用ルール

- `03` では、まず制度・法令問題かどうかを判定する。
- 医師国家試験では `lawReferences` を最終成果物に出さない。
- 制度・法令問題でない限り、`lawGroundedExplanationNotNeeded` は原則 `true` に倒す。
- 制度・法令問題、届出、医師の義務、医療安全、感染症対応、母子保健、精神保健、臓器移植などが絡む場合は、原則 `lawGroundedExplanationNotNeeded: false` とする。
- `04` では、まず病態・臓器・診療場面の主題を取り、既存の `questionSets[].questionSetName` に最も近い具体カテゴリへ寄せる。
- 個別の出題回は checkpoint / resume の単位であり、この資料の適用範囲は全 52 出題回・13,060 問である。
