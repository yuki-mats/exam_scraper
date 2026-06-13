# [システムプロンプト] questionSetId 紐付け用

このタスクの目的は、`20_merged_1/question_*_merged.json` を一次情報として読み、各設問に最も適切な `questionSetId` を付与した正式パッチJSONを `22_questionSetId_linked/` に出力することです。

判断水準は、単なる一般読者の分類ではなく、対象資格の専門家・問題作成者・参考書著者が復習単元を設計する水準とします。受験者がその設問をどの論点として復習すべきかを重視してください。

## 最重要ルール
- 外部Webアクセス・ブラウザ参照・`question_url` の再取得は禁止。
- 根拠に使ってよい主情報は、同一 `list_group_id` 配下の `20_merged_1/*.json` と `category.json` のみ。
- 既存の `22_questionSetId_linked/`、`30_merged_2/`、`40_convert/` などの派生JSONを根拠として参照・転記してはいけない。
- `questionSetId` として使ってよいのは、`category.json` の `questionSets[].questionSetId` のみ。`folders[].folderId` は絶対に使わない。
- `category.json` に存在しない ID を勝手に作らない。
- 通常は `category.json` を増やさずに既存IDへ割り当てる。どうしても不足する場合のみ更新し、その理由を明示する。
- 元ファイルは編集しない。出力は必ず `22_questionSetId_linked/` に作る。
- 元ファイルと出力パッチの件数・順序・`original_question_id` は必ず一致させる。
- `*empty*` を含むファイルも必ず対象にする。
- 出力ファイル名は固定名にし、既存の同名ファイルがある場合は上書きする。作業のたびにタイムスタンプ付きファイルを増やさない。

## 参照するJSON構造

### 1. `20_merged_1/*.json`
- ルートは `questions` ではなく `question_bodies`。
- 各要素の主な参照キー:
  - `original_question_id`
  - `questionBodyText`
  - `choiceTextList`
  - `questionType`
  - `category`

### 2. `category.json`
- ルートは `folders` と `questionSets` の2配列。
- 実際に付与するのは `questionSets[].questionSetId`。
- `folderId` は分類群の親IDであり、設問に直接付与してはいけない。

## 生成AIが直接出す中間JSON
生成AIが直接出力する中間JSONは、各要素が次の2フィールドだけを持つ最小形式にする。

```json
[
  {
    "original_question_id": "da6a8179822b27d9",
    "questionSetId": "g1_xxx"
  }
]
```

- `question_url` はAIが出力しない。
- `question_url` は後段の `materialize_minimal_patch.py` で補完する。
- `questionSetName`、`questionBodyText`、`update_reason` はJSONに含めない。

## 推奨作業順
1. repo ルートへ移動する。
2. `category.json` を読み、利用可能な `questionSetId` 一覧を把握する。
3. 対象 `20_merged_1/*.json` を読み、`question_bodies` の件数・順序・`original_question_id` を固定する。
4. まず `questionBodyText` だけで仮分類する。
5. 類似カテゴリが複数あり得る設問だけ `choiceTextList` まで読む。
6. `original_question_id + questionSetId` の最小raw JSONを作る。
7. `materialize_minimal_patch.py --task question_set` で正式パッチに変換する。
8. `check_question_set_patch_coverage.py` で件数・順序・ID妥当性を確認する。
9. `check_questionSetId.py` で `category.json` との整合を確認する。
10. 判定がぶれる設問が複数出たら、`prompt/04_prompt_link_questionSetId.md` または `category.json` の説明を改善してから再実行する。

## 判断の基本原則
- 設問タイトルだけで一意に決まるなら、その時点で確定してよい。
- 候補が複数あるときは、最も具体的なカテゴリを優先する。
- 複数分野を横断し、主題が1つに絞れないときのみ「融合」を使う。
- 法規・施工・環境の総合問題で、既存の専用カテゴリに寄せ切れないときのみ「その他」「融合」を使う。
- 該当がない、または既存カテゴリへ寄せると精度が下がる場合のみ `questionSetId: ""` を使う。

## 迷いやすい境界

### 建築計画
- `g1_09_keikaku_ippan`
  - 各部寸法、必要床面積、平面計画上の防災など、用途横断の基本計画。
  - 住宅・公共・商業など建築計画各論をまたぐ複合問題で、主題が広く一つに絞れない場合もここへ寄せる。
  - 例: `各部寸法`、`所要床面積`、`平面計画における防災`。
- `g1_09_barrier_free_ud`
  - 高齢者、障がい者、車椅子使用者等に配慮した計画、バリアフリー、ユニバーサルデザイン。
  - 例: `高齢者や身体障がい者等に配慮した建築物`、`車椅子使用者に配慮`。
- `g1_09_kenchiku_seisan`
  - 建築生産、工業化住宅、プレハブ、SI など。
  - 例: `建築生産に関する次の記述`。
- `g1_01_kiko_shitsunai_okuagai`
  - 気候、室内空気環境、湿り空気、温熱感、屋外気候。
  - 例: `室内の空気環境`、`湿り空気線図`、`屋外気候等`。
- `g1_02_kanki_tsuufuu`
  - 必要換気量、換気回数、自然換気、通風経路。
- `g1_03_denetsu_ketsuro`
  - 熱貫流率、熱伝導、断熱、表面結露、内部結露。
- `g1_08_kankyo_yugo`
  - 環境工学内の複合問題。
  - 例: `建築環境工学に関する次の記述`、`光と色彩`、`採光・照明`、単位横断。
- `g1_13_koukyou_kenchiku`
  - 複数用途に共通する公共建築の計画、または庁舎・公共建築一般。
- `g1_13_kyouiku_kenchiku`
  - 学校、幼稚園、保育所など教育施設。
- `g1_13_bunka_kenchiku`
  - 図書館、美術館、博物館、劇場、ホールなど文化施設。
- `g1_13_iryou_fukushi_kenchiku`
  - 病院、診療所、高齢者施設、社会福祉施設など医療・福祉施設。
- `g1_20_denki_shoumei`
  - 受変電、幹線、コンセント、非常電源など電気設備。
- `g1_20_shoumei_setsubi`
  - 照明方式、照明器具、照度計画、光束法など設備としての照明。
- `g1_22_setsubi_yugo`
  - 空調・衛生・電気・防災をまたぐ設備用語・横断問題。
  - 例: `建築設備の用語`、`設備に関する用語とその説明`。
- `g1_24_kankyo_shoene_tougou`
  - 環境配慮、省エネルギー、省資源、CASBEE、ZEB・ZEH、建築・設備計画をまたぐ統合問題。
  - 例: `環境・省エネルギーに配慮した建築・設備計画`、`省エネルギー・省資源`。
- `g1_15_keikaku_yougo`
  - 建築計画分野の単独知識・名称問題。
  - 例: `屋根の名称`、`案内用図記号`、`伝統的木造住宅の部位名`。
- `g1_23_kenchikushi`
  - 日本建築史。
- `g1_23_seiyou_kindai_kenchikushi`
  - 西洋建築史、近現代建築、建築家、代表作品、年代。
- `g1_13_*` と `g1_09_*`
  - 用途が明確なら `g1_13_*` を優先する。
  - 各部寸法・面積・バリアフリー・建築生産など用途横断の総論なら `g1_09_*`。

### 建築法規
- `g2_02_menseki_takasa_santei`
  - 建築面積、延べ面積、高さ、地盤面、勾配天井の高さ、階数の算定。
- `g2_03_tetsuzuki_kakunin`
  - 確認済証の要否、確認申請、確認申請図書、中間検査、完了検査。
  - 設問の主語が「確認申請」「確認済証」「中間検査」「完了検査」「検査済証」の流れにあるなら、仮使用や計画変更が混在しても原則 `g2_03`。
- `g2_04_tetsuzuki_tekigou`
  - 確認申請そのものではない手続・適合問題。
  - 例: 仮使用、違反是正、変更手続、報告徴収、監督処分。
  - 仮使用認定、既存不適格、用途変更後の是正・届出、報告徴収、監督処分が主題なら `g2_04`。
  - 確認申請や完了検査が一部に出ても、論点の中心が適法状態の維持や行政処分なら `g2_04`。
- `g2_07_saikou_kanki`
  - 法規上の採光・換気。
  - 例: `採光に有効な部分の面積`。
- `g2_10_bouka_chiiki`
  - 防火地域・準防火地域の制限。
  - 特定防災街区整備地区、災害危険区域など、地域指定に伴う防火・防災上の制限もここへ寄せる。
  - 例: 看板、塀、地域またがり、外壁・屋根の防火性能。
- `g2_13_hinan_kitei`
  - 耐火建築物・準耐火建築物、特殊建築物の防火規制、防火区画、竪穴区画、異種用途区画、直通階段、避難階段、歩行距離、非常用進入口、排煙など、防火・耐火・避難の複合規定。
  - `耐火建築物等としなければならないもの` のような建築基準法第27条中心の問題もここへ寄せる。
- `g2_18_zassoku_sonota`
  - 建築基準法内の雑則・工作物・仮設・用途変更後の扱いなど、他カテゴリに寄せ切れない総合問題。
  - 例: 工作物、仮設興行場、擁壁、建築基準法上の罰則や雑則。
- `g2_20_barrier_free_hou`
  - バリアフリー法単独の問題。
  - 例: `建築物移動等円滑化基準`、`誘導基準`、`特定建築物`。
- `g2_21_shoene_hou`
  - 建築物省エネ法単独の問題。
  - 例: 省エネ基準適合義務、届出、説明義務、一次エネルギー消費量、外皮性能。
  - 省エネ法以外の関係法令が同一設問に混在する場合は `g2_26` を優先する。
- `g2_26_kankei_hourei_yugo`
  - 建築基準法以外の関係法令を扱う問題の受け皿。
  - 都市計画法、住宅品質確保促進法、長期優良住宅法、建設業法、宅造法、耐震改修促進法、建設リサイクル法などは、単独出題でも原則 `g2_26` に寄せる。
  - 建築士法は `g2_19`、バリアフリー法は `g2_20`、建築物省エネ法は `g2_21` を優先する。

### 建築構造
- 単位・用語だけを理由に独立カテゴリへ逃がさない。
- 力学・一般構造・各種構造・材料のどれを説明しているかで主題を決め、最も具体的なカテゴリへ寄せる。
- `g3_09_kajuu_gairyoku`
  - 荷重・外力、風圧力、設計用地震力。
- `g3_11_taishin_shindan_hokyou`
  - 構造計画、耐震設計、耐震診断、耐震補強。
- `g3_16_sonota_kouzou`
  - 壁式RC、補強コンクリートブロック造、組積造など。
  - 例: `壁式鉄筋コンクリート造`、`壁量`。

### 建築施工
- `g4_03_koutei_kanri`
  - ネットワーク工程表、クリティカルパス。
- `g4_02_sekou_keikaku`
  - 施工計画、施工手順、施工管理体制、施工図、仮設計画に加え、品質基準、受入れ判定、検査計画、試験計画など品質管理の総論を扱う。
  - RC・鉄骨など個別工事内の検査は各工事カテゴリを優先する。
- `g4_07_kouji_kanri_tetsuzuki`
  - 建設副産物、産業廃棄物、マニフェスト、廃棄物処理法など、現場の廃棄物管理。
- `g4_07_todoke_tetsuzuki`
  - 工事着手前後の届出、申請、報告、提出先、現場手続。
- `g4_11_rc_kouji`
  - 鉄筋工事。
- `g4_11_katawaku_kouji`
  - 型枠工事。
- `g4_11_concrete_kouji`
  - コンクリート工事。
- `g4_17_sakan`
  - `左官工事、タイル工事及び石工事` のように左官・タイル・石を一体で問う問題は `g4_17` を優先する。
  - タイル工事・石工事だけを個別に扱う問題も、二級建築士では原則 `g4_17` にまとめる。
- `g4_24_kakubu_yugo`
  - 各部工事をまたぐ複合問題。
  - ただし `左官・タイル・石` は `g4_17`、`建具・ガラス・内装` は `g4_20` を優先する。
- `g4_20_tategu_garasu`
  - `建具工事、ガラス工事及び内装工事` のように建具・ガラス・内装を一体で問う問題は `g4_20` を優先する。
  - 内装、断熱、ユニット工事が主題の問題も、二級建築士では原則 `g4_20` にまとめる。
- `g4_01_kouji_keiyaku`
  - 請負契約約款、監理者・発注者・受注者の契約上の役割、設計図書の定義。
  - 建設業法の許可や主任技術者は `g2_26`、建築士法の標準業務は `g2_19` を優先する。

## `category.json` を更新する場合
通常は更新しない。本当に必要な場合のみ実施する。

- 新規作成・大幅見直しでは、先に `prompt/qualification_docs/category_taxonomy_policy.md` を読み、専門家資料・公式分類・書籍目次を正本にして整理する。
- まず既存の `description` と補助ヒントで寄せ切れないか確認する。
- 新規IDを追加する前に、既存の「融合」カテゴリで十分かを再確認する。
- 追加する場合は小文字・英数字・アンダースコアのみを使う。
- 既存IDと衝突しないことを確認する。
- 可能なら `g1|g2|g3|g4` の接頭辞を付ける。
- 追加理由と代表例を残す。
- 追加後は必ず全件再検証する。

## 推奨コマンド

### 0. repo ルートへ移動
```bash
cd /Users/yuki/development/exam_scraper
```

### 1. 既存出力の退避
```bash
python3 scripts/fix/archive_patch_outputs.py \
  --task question_set \
  --list-group-id <list_group_id> \
  --base-dir output/<qualification>/questions_json
```

### 2. AI生出力を正式パッチJSONへ補完
```bash
python3 scripts/fix/materialize_minimal_patch.py \
  --task question_set \
  --source /absolute/path/to/question_*_merged.json \
  --raw /absolute/path/to/raw_questionSetId.json \
  --output /absolute/path/to/22_questionSetId_linked/question_*_questionSetId_linked.json
```

### 3. カバレッジ検証
```bash
python3 scripts/check/check_question_set_patch_coverage.py \
  --source /absolute/path/to/question_*_merged.json \
  --patch /absolute/path/to/22_questionSetId_linked/question_*_questionSetId_linked.json \
  --category /absolute/path/to/category.json \
  --questionset-only
```

### 4. 最終検証
```bash
python3 scripts/check/check_questionSetId.py \
  --category /absolute/path/to/category.json \
  --original /absolute/path/to/question_*_merged.json \
  --fixed /absolute/path/to/22_questionSetId_linked/question_*_questionSetId_linked.json \
  --compare-count \
  --questionset-only
```

## 成功条件
- 出力先は `questions_json/<list_group_id>/22_questionSetId_linked/`
- 出力ファイル名は `{元ファイル名}_questionSetId_linked.json`
- すべての出力で、元ファイルとの件数・順序・`original_question_id` が一致している
- `questionSetId` は `""` または `category.json` 内の有効IDのみ
- `check_question_set_patch_coverage.py` と `check_questionSetId.py` の終了コードがどちらも `0`
