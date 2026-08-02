# 資格別補助ドキュメント

このディレクトリには、資格ごとにしか定義できない事実と必要最小限の調整を置きます。解説文の構成・情報量・正誤の示し方・補足の採否は、全資格共通の[`03_prompt_add_explanationText.md`](../03_prompt_add_explanationText.md)を正本とします。

共通field、Firestoreキー、型、`questionType`、`lawReferences`は[question field契約](../../document/reference/question_field_contract.md)、`category.json`の分類・命名は[category taxonomy policy](category_taxonomy_policy.md)を参照します。資格別資料はこれらを上書きしません。

## 置く情報

- 試験範囲、章立て、公式用語、取得済みデータの問題構造
- 取得元や画像など、その資格だけにある入力上の事情
- `category.json`を設計するときに必要な専門家資料と境界
- 通常参照する法令・告示・公式資料の範囲と短縮表記

「正しい。から始める」「誤りは正しい内容を直接示す」「補足は0件を標準とする」といった共通ルールは資格別に複製しません。新しい傾向を見つけても、八つの共通パターンで説明できる内容には既存パターンを適用します。

## 資格固有の調整

共通パターンだけではその資格らしい正確な説明にならない場合は、資格別`README.md`に`## 解説の資格固有調整`を置き、次のような情報だけを短く足します。専用の解説promptは作りません。

- 公式用語、略称、記号の意味や、その資格特有の読み方
- 正誤を分けるために繰り返し使う判断軸
- 根拠として優先する公式資料と、その資格だけにある注意点

例えば、建築法規で設問中の「法」「令」「規則」が通常どの法令を指すか、ガス主任で取得元のjudge欄をどの条件で正答根拠として扱うかを記載できます。共通03の正誤表示、説明順、計算式、補足の基準はそのまま使います。

## 基本構成

資格ごとに必要なファイルだけを置きます。

1. `README.md`
   - 資料の索引、資格コード、取得元などの短い前提。必要なら`解説の資格固有調整`もここへ置く
2. `01_exam_profile.md`
   - 試験範囲、章立て、問題形式、取得データ固有の構造
3. `03_category_preparation.md`
   - `category.json`を新規作成又は見直す場合だけ使う分類資料
4. `*law_reference*.md`
   - 法令問題を扱う資格だけに置く法令スコープ

公式の文体見本や画像規約など、上記へ統合できない資格固有の一次資料がある場合だけ、目的を限定したファイルを追加できます。

## 工程ごとの参照

- 工程03は共通プロンプトと資格別`README.md`を読み、`解説の資格固有調整`があれば加味する。法令問題では`*law_reference*.md`も参照する。
- 工程05は、問題の形式や公式用語を保つために`01_exam_profile.md`を参照する。
- category整備は、まず`category.json`を使い、設計又は見直しが必要な場合だけ`03_category_preparation.md`を参照する。
- 法令問題は資格別スコープを起点にし、e-Gov全体を無差別に検索しない。

法令を扱わない資格は`config/qualification_rules.json`で`law_workflow_enabled=false`にします。この設定では02bと03bを工程一覧・自動選択・完了条件から外し、資格別の細かな除外条件は追加しません。

## 運用

- 新資格の準備工程では、まず`README.md`と`01_exam_profile.md`だけを作る。
- 資格固有の調整は`README.md`へ短く統合し、別の解説方針ファイルは作らない。
- 資格固有資料がなくても問題データと共通正本だけで判断できる場合は、新しい資料を増やさない。
- 日常の`questionSetId`紐付けが補助資料依存なら、先に`category.json`の`description`と`matchingHints`を改善する。
- 継続する仕様は責務に合う既存ファイルへ統合し、取得元別・年度別の文章ルールを作らない。

## 現在ある資格

- `aws-cloud-practitioner`
- `aws-solutions-architect-associate`
- `2nd-class-kenchikushi`
- `gas-shunin-kou`
- `gas-shunin-otsu`
- `kaigofukushi`
- `kyusuikouji-shunin`
- `mecnet-kokushi`
- `nw`
- `judoseifukushi`
- `shinkyu`
