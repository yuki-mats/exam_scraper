# 資格別補助ドキュメント

このディレクトリは、資格ごとの出題傾向、問題形式、解説方針、`category.json` 整備メモを集約する共通置き場である。

## 役割の優先順位
- 主用途は `03_prompt_add_explanationText.md`
  - 資格ごとの傾向に合わせて、解説文をより学習効果の高い内容にするために使う
  - 目標は「その問題の理解」だけでなく、「その資格でよく問われる傾向への気づき」と「類題に使える判断軸」の付与まで含む
  - 資格固有の章構成、頻出のひっかけ、判定軸、類題を意識した補足知識はここに書く
- 従用途は `04_prompt_link_questionSetId.md`
  - ふだんの紐付けでは `category.json` の `name` / `description` / `matchingHints` を主根拠にする
  - このディレクトリ内の資料は、`category.json` を新規作成・見直しする際の検討資料として使う

## 配置ルール
- 資格別の長文知識は、この `qualification_docs/` に集約する。
- `03_prompt_add_explanationText.md` や `04_prompt_link_questionSetId.md` の本文には、共通原則と参照ルールだけを残す。
- `04_prompt_link_questionSetId/` のような prompt 番号別の補助ディレクトリは増やさず、資格別資料はここにまとめる。
- 新しい資格固有ルールや判断軸が必要になった場合も、prompt 本体へ書き足さず、`qualification_docs/<qualification_key>/` に整理する。

## 推奨構成
各資格ディレクトリでは、次の3本を基本形にする。

1. `01_exam_profile.md`
  - 試験全体の章立て、出題傾向、問題形式
2. `02_explanation_strategy.md`
   - 解説文を書くときに重視すべき判定軸、頻出のひっかけ、章別の補足知識
   - 学習者に「ハッとする」気づきや、類題へ伸ばすための見分け方
3. `03_category_preparation.md`
  - `category.json` を整備する際の分類粒度、章内の切り方、境界ルール

## 運用ルール
- まず `03` のために `01_exam_profile.md` と `02_explanation_strategy.md` を整備する。
- `category.json` の設計や見直しが必要になった段階で、`03_category_preparation.md` を追加・更新する。
- 日常の `questionSetId` 紐付けが補助資料依存になっている場合は、先に `category.json` 側の `description` と `matchingHints` を改善する。
- 資格別の指針を参照する作業では、各資格ディレクトリの内容を正本とみなし、prompt 本体は共通ルールの置き場として扱う。

## 現在ある資格
- `kaigofukushi`
- `kyusuikouji-shunin`
