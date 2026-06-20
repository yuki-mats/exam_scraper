# category.json 分類・命名ポリシー

この文書は、各資格の `category.json` を共通方針で整備し、Firestore の `folders` / `questionSets` / `questions` へ安全に取り込める状態にするための正本である。

## 目的

`category.json` は、単なる内部分類表ではなく、ユーザーが過去問を復習するときの学習単元そのものである。

そのため、分類の正本は AI の推測ではなく、次のような専門家・教材側の体系を優先する。

1. ユーザーが確認した過去問集、問題集、参考書、資格スクール教材の目次・章立て
2. 試験実施団体、官公庁、公式ブループリント、出題基準の分類
3. 既存の `category.json` と、すでに Firestore に反映済みの分類
4. AI による補助分類案

AI は、専門家分類の抽出、Firestore 用 JSON への整形、境界ルールの明文化、既存問題への紐付け補助に限定する。専門家資料と矛盾する独自分類を正本にしてはいけない。

## 保存場所

資格ごとの分類作業では、次を基本セットにする。

- `output/<qualification>/category/category.json`
  - Firestore へ取り込む folder / questionSet の正本 JSON。
- `prompt/qualification_docs/<qualification>/03_category_preparation.md`
  - どの教材・目次・公式分類を根拠にしたか、どの粒度で切るか、迷う境界をどう扱うかを残す検討資料。
- 必要に応じて `prompt/qualification_docs/<qualification>/01_exam_profile.md`
  - 試験全体の科目構成や公式分類。
- `prompt/qualification_docs/shared_taxonomy_mapping_policy.md`
  - 同じ科目・出題範囲を複数の資格区分で共有する場合の `folder` / `qualification` / `mapping` の分離方針。

ユーザー提供の書籍写真や目次画像は、分類を起こすための入力資料として扱う。repo に残すのは原則として、出典メモ、分類名、境界ルール、要約した判断軸であり、写真そのものや長い本文は Git 管理に入れない。

## category.json の基本構造

ルートは原則として `folders` と `questionSets` の2配列にする。`metadata` と `updatedAt` は持ってよい。

```json
{
  "metadata": {
    "qualificationId": "example",
    "licenseName": "サンプル資格",
    "taxonomySource": "ユーザー提供の過去問集目次と公式出題範囲"
  },
  "folders": [
    {
      "folderId": "example_f01_social_system",
      "name": "01_社会制度",
      "description": "制度、行政、関係法令を扱う。",
      "questionCount": 0,
      "isDeleted": false
    }
  ],
  "questionSets": [
    {
      "questionSetId": "example_qs01_01_insurance",
      "folderId": "example_f01_social_system",
      "name": "1-1 介護保険制度",
      "description": "保険者、被保険者、給付、地域支援事業を扱う。障害福祉制度は別カテゴリ。",
      "matchingHints": ["介護保険", "保険者", "被保険者"],
      "questionCount": 0,
      "isDeleted": false
    }
  ]
}
```

現在の upload 処理では、Firestore の `folders` / `questionSets` に主に反映されるのは `name`、`folderId`、`questionCount`、`isDeleted` である。`description` と `matchingHints` は、repo 内で分類・紐付けの根拠として使う補助項目として扱う。

## folder の考え方

`folder` は、ユーザーが最初に選ぶ大分類である。原則として、専門家資料・公式資料の大章、試験科目、ブループリントの上位分類に対応させる。

公害防止管理者やガス主任技術者のように、同じ科目を複数の資格区分で共有する資格では、`folder` に資格区分や難度を入れない。`folder` は科目、`qualification` は資格区分、`mapping` はその資格区分で使う科目セットとして扱う。詳細は `prompt/qualification_docs/shared_taxonomy_mapping_policy.md` を正本にする。

良い `folder` の条件:

- 書籍目次や公式分類の大きな棚と対応している
- ユーザーが一覧で見たときに、どの分野かすぐ分かる
- 下位の `questionSet` を複数持てる
- 試験年度や問題番号ではなく、学習分野を表している

避けるべき `folder`:

- 年度別、回別、ページ別など、復習単元ではない分類
- 甲種、乙種、丙種、第1種、第2種など、本来は資格区分を表す分類
- 1問だけの特殊論点を大分類にしたもの
- AI が便宜上まとめただけで、専門家資料や出題範囲に根拠がない分類

## questionSet の考え方

`questionSet` は、ユーザーが誤答後に復習する単元である。分類の基準は「この問題を間違えた学習者は、何を復習すべきか」に置く。

良い `questionSet` の条件:

- 専門家資料の小見出し、中項目、頻出論点と対応している
- 過去問で繰り返し出る論点をまとめている
- `description` だけで、入れる問題と入れない問題の境界が分かる
- 近接カテゴリとの違いが明記されている
- `matchingHints` に代表語、制度名、疾患名、計算名、手続名などが入っている

粒度の目安:

- 公式科目そのままだと大きすぎる場合は、書籍目次や頻出論点に沿って分割する。
- 1つの専門家見出しに過去問が十分集まるなら、独立した `questionSet` にする。
- 似た小見出しが少数問しかなく、受験者の復習先として分ける意味が薄い場合は統合する。
- 「総合」「融合」「その他」は、主題が一つに絞れない問題の受け皿に限定する。

## 表示名の命名規則

表示名は `name` に入れる。Firestore とアプリの表示で使われるため、ユーザーにとって自然な日本語を優先する。

### folder.name

新規資格では、原則として次の形式にする。

```text
NN_大分類名
```

例:

- `01_社会の理解`
- `02_関税法等`
- `03_通関実務`

公式・教材上のラベルが学習上重要な場合は、ラベルを残してよい。

例:

- `01_学科I：建築計画`
- `02_医学総論：I 保健医療論`

### questionSets.name

新規資格では、原則として次の形式にする。

```text
N-M 小分類名
```

または、専門家資料の階層が強い資格では次を使う。

```text
大項目｜小分類名
```

例:

- `1-1 介護保険制度`
- `2-3 輸出入申告`
- `社会保障制度｜医療保険`

表示名には、内部ID、資格コード、年度、元サイト名を入れない。順序を保つための番号は、Firestore 側に表示順フィールドがない現状では `name` に入れてよい。

## 内部IDの命名規則

`folderId` と `questionSetId` は表示名ではなく、永続的な内部IDである。後から名前を変えても、IDの意味は変えない。

新規資格では次を推奨する。

```text
folderId:      <qualification_key>_fNN_<ascii_slug>
questionSetId: <qualification_key>_qsNN_MM_<ascii_slug>
```

例:

- `tsukanshi_f01_gyoho`
- `tsukanshi_qs01_01_kyoka`
- `kaigofukushi_f03_social`
- `kaigofukushi_qs03_02_social_insurance`

ルール:

- 小文字英数字と `_` のみを使う。
- 日本語、空白、記号、年度、回番号は入れない。
- 既存IDは安易に変えない。
- 表示名だけ変える場合は `name` を更新する。
- IDを変える場合は、全 question の `questionSetId` と Firestore 上の古い questionSet doc の整理まで含む migration task として扱う。

複数資格区分で科目を共有する場合は、canonical ID と Firestore 表示用 ID を分ける。

```text
canonicalFolderId:      <qualification_group>_fNN_<ascii_slug>
canonicalQuestionSetId: <qualification_group>_qsNN_MM_<ascii_slug>

folderId:               <qualification_id>_fNN_<ascii_slug>
questionSetId:          <qualification_id>_qsNN_MM_<ascii_slug>
```

当面の Firestore 互換では、資格区分ごとの `folderId` / `questionSetId` を維持し、`canonicalFolderId` / `canonicalQuestionSetId` / `sourceSharedFolderId` / `sourceSharedQuestionSetId` で共通分類との対応を保持する。

## description / matchingHints

`description` には、分類作業者が迷わないように次を入れる。

- 何を扱うカテゴリか
- どの近接カテゴリとは分けるべきか
- 代表語、制度名、疾患名、手続名、計算名
- 書籍目次や公式分類での対応関係

`matchingHints` には、問題文・選択肢・解説に出やすい短い語を入れる。長文説明や出典本文の転載は入れない。

## 専門家資料からの整理手順

ユーザーが本屋・手元教材・過去問集の目次や章立てを提示した場合は、次の順で進める。

1. 資料の種類を記録する。
   - 例: 過去問集目次、章扉、公式出題範囲、スクール教材の単元表。
2. 大章を `folder` 候補にする。
3. 小見出し・頻出論点を `questionSet` 候補にする。
4. 既存 `category.json` がある場合は、IDを維持できるものと新規追加が必要なものを分ける。
5. 近い論点の境界を `03_category_preparation.md` に書く。
6. `category.json` を作成・更新する。
7. dry-run で Firestore schema と count 更新の挙動を確認する。
8. 問題データに `questionSetId` を付与し、coverage check を通す。
9. questions と category を Firestore に反映する。

## 検証コマンド

`category.json` の作成・更新後は、少なくとも dry-run を通す。

```bash
cd /Users/yuki/development/exam_scraper

.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/<qualification>/category/category.json \
  --all-list-groups \
  --questions-json-dir output/<qualification>/questions_json
```

問題側の `questionSetId` を整備した後は、資格単位または list_group_id 単位で `prepare_firestore_upload.py` を通す。

```bash
.venv/bin/python scripts/pipeline/prepare_firestore_upload.py <qualification> \
  --exam-name <表示する資格名> \
  --category-json output/<qualification>/category/category.json \
  --questionset-only
```

Firestore へ反映する場合は、questions の upload と category の upload を分けて確認する。

```bash
.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/<qualification>/category/category.json \
  --all-list-groups \
  --questions-json-dir output/<qualification>/questions_json \
  --upload
```

## 既存資格を統一していくときの方針

既存資格では、すでに Firestore や問題データが参照している `folderId` / `questionSetId` を優先して守る。

統一は次の順で行う。

1. `name` の表示ルールを整える。
2. `description` と `matchingHints` を足して、専門家分類との対応を明確にする。
3. `questionCount` と `isDeleted` を最新化する。
4. どうしても必要な場合のみ、ID変更を migration task として実施する。

ID変更を伴わない `name` の変更は比較的安全である。ID変更は、既存 question の `questionSetId`、Firestore の active questionSet、古い doc の soft delete まで影響するため、通常の分類整備とは別タスクにする。

## 判断に迷った場合

迷った場合は、AI が独断で分類を増やさない。次のどれかで止める。

- 既存 `questionSet` の `description` を改善して吸収できるか確認する。
- `03_category_preparation.md` に未確定論点として残す。
- 問題側では一時的に `questionSetId: ""` とし、後で専門家資料・ユーザー判断で確定する。

分類は、Firestore に入った後にユーザーの学習体験へ直接出る。短期的な作業効率より、専門家資料に基づく一貫した復習単元を優先する。
