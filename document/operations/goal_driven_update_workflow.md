# goal 駆動の日次更新フロー整理

この文書は、`exam_scraper` 配下で現在行っている作業を、Codex の goal 運用に載せやすい単位で整理したものです。

スクレイピングから Firestore upload までの手順そのものは [exam_pipeline_manual_and_automation.md](/Users/yuki/development/exam_scraper/document/operations/exam_pipeline_manual_and_automation.md) を正本とします。この文書は、その全体フローを goal に分解するための運用設計です。

目的は次の3点です。

1. 現在の実務フローを共通認識として固定する
2. `qualification_code` や `list_group_id` 指定で回せる goal の粒度を決める
3. 最終的に「半自動」から「日次の手放し運用」へ寄せるための前提条件を明示する

## 1. 現在の大枠認識

現状の本流は、単なるスクレイピングではなく、次の多段フローです。

1. 資格ごとの過去問を取得する
2. `00_source` と画像を作る
3. `questionType`、`questionIntent`、`correctChoiceText`、`explanationText`、`questionSetId` の patch を作る
4. patch を順序どおり merge して、`12_merged_questionType`、`20_merged_1`、`30_merged_2` を更新する
5. Firestore 用 JSON に変換する
6. validation と dry-run を通す
7. 画像 / category / questions を upload する

つまり、日々の更新対象は大きく2種類あります。

- `取得の更新`
  - 新しい年度や不足年度をスクレイピングして `00_source` を増やす
- `品質の更新`
  - 既存の過去問に対して、`questionSetId`、`correctChoiceText`、`explanationText` などを改善する

ユーザー認識の「スクレイピングした過去問と回答を元に、正誤修正やより分かりやすい解説へ更新する」は、この実装実態と一致している。

## 2. 現在の作業単位

### 2-1. 最上位単位

- `qualification_code`
  - 資格単位のまとまり
  - 例: `sg`, `kaigofukushi`, `gas-shunin-otsu`

### 2-2. 中位単位

- `list_group_id`
  - 年度、回次、春秋区分などを表す処理単位
  - スクレイピング、merge、convert、upload は基本的にこの単位で閉じる

### 2-3. 下位単位

- `question_*_merged.json`
  - patch 作業のファイル単位
  - ただし正答精度と解説品質を厳密に上げる goal では、さらに `question_bodies[]` の1設問単位へ分解する

### 2-4. task 単位

- `question_type`
- `question_intent`
- `correct_choice_review`
- `question_set`
- `explanation`

これらは、同じ `list_group_id` でも入力正本と品質基準が違う。`explanation` は正しい `questionType`、`questionIntent`、`correctChoiceText` を前提にするため、最後に回す。

厳密レビューでは、Worker の役割を一般的な目視確認者ではなく、その資格の専門家、問題作成者、参考書著者として扱う。正答を当てるだけでなく、出題意図、根拠、受験者が誤学習しない説明、教材としての分かりやすさまで確認する。

## 3. 現在の正本フロー

### 3-1. スクレイピング

入口:

- 推奨: `scripts/scrape/run_qualification_scrape.py`
- 個別互換: `code.py`, `scrape_gassyunin.py`, `scrape_sgsiken.py`

出力:

- `output/<qualification>/questions_json/<list_group_id>/00_source/`
- `output/<qualification>/question_images/<list_group_id>/`

### 3-2. patch 作成

patch の保存先:

- `10_questionType_fixed/`
- `15_correctChoiceText_fixed/`（実質 `questionIntent` パッチ）
- `21_explanationText_added/`
- `22_questionSetId_linked/`
- `23_correctChoiceText_fixed/`（最終 `correctChoiceText` の厳密レビュー対象）

merge が生成する主な出力:

- `12_merged_questionType/`（中間 view。手作業 patch の出力先ではない）
- `20_merged_1/`
- `30_merged_2/`

ここはまだ「完全自動バッチ」ではなく、task ごとの明示的な patch 作成運用である。

正答精度と解説品質を上げる場合の順序:

1. `01_prompt_fix_questionType.md`
   - 主入力: `00_source/question_*_*.json`
   - 出力: `10_questionType_fixed/`
2. merge
   - `20_merged_1/` を更新
3. `02_prompt_fix_questionIntent.md`
   - 主入力: `20_merged_1/question_*_merged.json`
   - 出力: `15_correctChoiceText_fixed/`
   - 注意: `correctChoiceText` 直接判定ではなく `questionIntent` 精査
4. merge / correctChoiceText 補完
   - `questionIntent + answer_result_text` から `correctChoiceText` を整える
5. `23_correctChoiceText_fixed/` 厳密レビュー
   - 下書き補完結果を `questionIntent`、`answer_result_text`、選択肢、元解説と一問ずつ突き合わせる
   - 必要なら最終 `correctChoiceText` patch を更新する
6. `03_prompt_add_explanationText.md`
   - 主入力: `20_merged_1/question_*_merged.json`
   - 必要時のみ `23_correctChoiceText_fixed/`、`00_source/`、外部一次情報を参照
   - 出力: `21_explanationText_added/`
7. `04_prompt_link_questionSetId.md`
   - 主入力: `20_merged_1/question_*_merged.json` と `category.json`
   - 出力: `22_questionSetId_linked/`

### 3-3. merge / convert / validation

主要コマンド:

- `scripts/merge/00_merge_all.py`
- `scripts/pipeline/prepare_firestore_upload.py`
- `scripts/check/check_required_fields.py`
- `scripts/check/check_explanation_patch_coverage.py`
- `scripts/check/check_question_set_patch_coverage.py`

この層はかなり自動化されており、goal に載せやすい。

### 3-4. publish

主要コマンド:

- `scripts/upload/upload_question_images_to_storage.py`
- `scripts/upload/upload_questions_to_firestore.py`
- `scripts/upload/upload_category_to_firestore.py`
- `scripts/pipeline/upload_all_to_firestore.py`

publish は自動化しやすいが、前段 patch の品質が不十分だと危険。

## 4. goal に載せるべき粒度

結論として、goal の入力単位は次の3層に分けるのがよい。

### 4-1. qualification 単位 goal

用途:

- 既知資格の全年度 refresh
- 資格全体の prepare
- 資格全体の upload 前検証

向いている処理:

- scrape preset に基づく `00_source` 更新
- `prepare_firestore_upload.py <qualification>`
- 件数集計、summary 作成、dry-run

### 4-2. list_group_id 単位 goal

用途:

- ある年度だけ最新化したい
- ある年度だけ patch 完成から publish まで進めたい

向いている処理:

- 単年スクレイピング
- 単年 merge
- 単年 convert
- 単年 validation

これは日々運用の主力粒度になる。

### 4-3. question 単位 goal

用途:

- `correctChoiceText` を `99.99%` 水準まで精査する
- `explanationText` の品質を正答とセットで確認する
- 難問だけの個別修正をする

向いている処理:

- `question_bodies[]` の1設問に対する、専門家水準の正答・設問意図・解説品質の確認

これは「品質向上」系の goal に限定して使うべきで、全資格一括処理には向かない。厳密運用では `1 Worker = 1問` を基本にする。

## 5. goal の推奨入力

日常運用では、最低でも次の入力を持たせるとよい。

- `qualification_code`
- `list_group_id` または `all`
- `mode`
  - `scrape`
  - `patch`
  - `prepare`
  - `publish`
  - `full_refresh`
- `tasks`
  - 例: `question_set`, `explanation`
- `dry_run`
- `publish`
  - `true` / `false`

実務上は、次の2系統に分けると扱いやすい。

### 5-1. 取得系 goal

例:

- `qualification_code=sg`
- `list_group_id=202501`
- `mode=full_refresh`

期待動作:

1. preset 解決
2. スクレイピング
3. merge
4. convert
5. validation
6. dry-run まで

### 5-2. 品質改善系 goal

例:

- `qualification_code=2nd-class-kenchikushi`
- `list_group_id=85010`
- `mode=patch`
- `tasks=explanation`

期待動作:

1. 対象 patch の棚卸し
2. 1問ずつ更新
3. coverage check
4. tranche 完了後に merge / prepare

## 6. どこまで手放し化できるか

### 6-1. すでに手放し化しやすい部分

- 既知資格のスクレイピング実行
- merge
- correctChoiceText の下書き補完
- convert
- requirements validation
- upload dry-run
- 全資格 summary 生成

### 6-2. まだ人の判断が強く残る部分

- `questionSetId` の新規または曖昧な分類
- `correctChoiceText` の `99.99%` 水準の最終監査
- `explanationText` の品質改善
- `category.json` の新設 / 再編
- 法令問題での条項裏取り

したがって、現時点での現実的な整理は次の通り。

- `取得と整形の自動化` はかなり可能
- `学習品質の改善` は goal で段階的に回すべき
- `完全手放し` は `explanation` をどこまで自動で許容するかの方針次第

## 7. いま自動化の前提として不足しているもの

### 7-1. preset 未整備の資格がある

現状、`output/` に存在する資格のうち、`config/scrape_presets.json` に載っていないものがある。

- `2nd-class-kenchikushi`
- `kounin-shinrishi`
- `kyusuikouji-shunin`

この状態では、「資格名だけ渡せば毎回同じ入口で回る」とは言い切れない。

### 7-2. qualification ごとの補助資料が未整備なものがある

`prompt/qualification_docs/` は一部資格のみ。
`explanation` を安定運用するには、資格ごとの出題傾向資料が揃っているほうがよい。

### 7-3. category 運用の責務分離が必要

実装上もドキュメント上も、`questionSetId` は `category.json` を正本として扱っている。
したがって、次の2つは別 goal に分けたほうが安全。

- `category を設計・見直しする goal`
- `既存 category に設問を割り当てる goal`

## 8. 日次運用に向けた推奨方針

日次で手放しに近づけるなら、1本の巨大 goal ではなく、次の3段構えがよい。

### 8-1. Stage A: intake goal

役割:

- 対象資格の確定
- 対象 `list_group_id` の確定
- 新規年度か既存改善かの判定
- 必要 task の切り出し

出力:

- 対象一覧
- 実行モード
- publish 可否

### 8-2. Stage B: execution goal

役割:

- scrape / patch / prepare を実行
- 必要なら `question_*_merged.json` 単位へ分解

出力:

- patch
- merged
- convert
- validation 結果

### 8-3. Stage C: publish goal

役割:

- dry-run 結果確認
- Storage / Firestore upload
- category count 更新

出力:

- publish receipt
- 対象 `list_group_id` の公開済み記録

## 9. 現時点の認識合わせ案

現時点での合意候補は次の通り。

1. `qualification_code` と `list_group_id` を goal の基本入力にする
2. `scrape` と `publish` は自動化寄りでよい
3. `correctChoiceText`、`questionSetId`、`explanationText` は、しばらくは quality gate 付き goal にする
4. `full_refresh` は「scrape -> prepare -> dry-run」までを標準にし、本番 publish は別段にする
5. 完全手放し運用の前に、まず全資格を `config/scrape_presets.json` に寄せる

## 10. 次にやるとよいこと

優先順は次の通り。

1. 全資格を `scrape_presets.json` 管理へ寄せる
2. 資格ごとに「日次更新で対象にする `list_group_id` 決定ルール」を固定する
3. `correctChoiceText`、`questionSetId`、`explanationText` をどこまで自動許容するか基準を決める
4. goal テンプレートを `取得系` と `品質改善系` に分けて用意する
5. 最後に `publish` を完全分離して、dry-run 成功時のみ進むようにする

この順で進めれば、「資格名や list_group_id を渡すと、日々の最新化を安定して回す」という形に寄せやすい。
