# 共有taxonomy / 資格mapping 設計ポリシー

この文書は、同じ科目・出題範囲を複数の資格区分で共有する資格を整備するための正本である。

対象例:

- 公害防止管理者
  - 大気関係第1種〜第4種、水質関係第1種〜第4種、ダイオキシン類、騒音・振動、粉じん、主任管理者
- ガス主任技術者
  - 甲種、乙種、丙種

## 結論

`folder` は科目、`qualification` は資格区分、`mapping` はその資格区分で使う科目セットとして扱う。

資格区分や難度を `folder` で表現しない。

```text
良い分離:
qualification = gas-shunin-kou
folder = 法令
folder = 基礎理論
folder = ガス技術

避ける分離:
folder = 甲種
folder = 乙種
folder = 丙種
```

## 3層モデル

### 1. qualificationGroup

資格ファミリーを表す。

例:

- `kougai`
- `gas-shunin`

### 2. qualification

ユーザーが学習対象として選ぶ資格区分を表す。

例:

- `kougai-taiki-1`
- `kougai-taiki-2`
- `kougai-suishitsu-1`
- `kougai-dioxin`
- `kougai-chief`
- `gas-shunin-kou`
- `gas-shunin-otsu`
- `gas-shunin-hei`

### 3. canonical taxonomy

資格区分に依存しない科目・出題範囲を表す。

例:

- `kougai_f01_kougai_soron`: 公害総論
- `kougai_f02_taiki_gairon`: 大気概論
- `kougai_f07_suishitsu_gairon`: 水質概論
- `gas_f01_hourei`: 法令
- `gas_f02_kiso_riron`: 基礎理論

### 4. qualification mapping

どの資格区分がどの canonical folder / questionSet を使うかを表す。

例:

```json
{
  "qualificationId": "kougai-taiki-1",
  "qualificationGroupId": "kougai",
  "canonicalFolderIds": [
    "kougai_f01_kougai_soron",
    "kougai_f02_taiki_gairon",
    "kougai_f03_taiki_tokuron",
    "kougai_f04_baifun_tokuron",
    "kougai_f05_taiki_yugai_tokuron",
    "kougai_f06_daikibo_taiki_tokuron"
  ]
}
```

## Firestore 互換方針

当面は Firestore の `folders` / `questionSets` を資格区分ごとに materialize する。

理由:

- 現在の app と upload は `folder.qualificationId` / `questionSet.qualificationId` を前提にしている。
- 学習履歴、問題数、編集権限、表示順を資格区分ごとに分けた方が安全。
- 同一 doc ID を物理共有すると、複数資格の questionCount や学習状態が混ざる。

materialized category の例:

```json
{
  "folderId": "kougai-taiki-1_f01_kougai_soron",
  "name": "01_公害総論",
  "qualificationId": "kougai-taiki-1",
  "canonicalFolderId": "kougai_f01_kougai_soron",
  "sourceSharedFolderId": "kougai_f01_kougai_soron"
}
```

```json
{
  "questionSetId": "kougai-taiki-1_qs01_01_kankyo",
  "folderId": "kougai-taiki-1_f01_kougai_soron",
  "name": "1-1 環境基本法",
  "qualificationId": "kougai-taiki-1",
  "canonicalFolderId": "kougai_f01_kougai_soron",
  "canonicalQuestionSetId": "kougai_qs01_01_kankyo",
  "sourceSharedQuestionSetId": "kougai_qs01_01_kankyo"
}
```

## ID 方針

### canonical ID

canonical ID は資格ファミリー内で一意にする。

```text
canonicalFolderId:      <qualification_group>_fNN_<ascii_slug>
canonicalQuestionSetId: <qualification_group>_qsNN_MM_<ascii_slug>
```

### materialized ID

Firestore 互換の表示用 doc ID は資格区分ごとに一意にする。

```text
folderId:      <qualification_id>_fNN_<ascii_slug>
questionSetId: <qualification_id>_qsNN_MM_<ascii_slug>
```

既存 Firestore doc ID がある資格では、既存 ID を優先する。ID を変える場合は、問題側 `questionSetId`、Firestore の古い category doc、questionCount、学習履歴影響まで含む migration として扱う。

## exam_scraper の整備順

1. 公式資料や教材目次から canonical folder / questionSet を作る。
2. 資格区分ごとの `qualificationId` を定義する。
3. `qualification -> canonical folder/questionSet` の mapping を作る。
4. mapping から資格別 `output/<qualification>/category/category.json` を生成する。
5. 問題には materialized `questionSetId` を付与する。
6. 必要に応じて `canonicalQuestionSetId` を補助情報として保持する。
7. upload dry-run と questionSet coverage check を通してから Firestore へ反映する。

## app 実装の移行順

### Phase 1: 互換維持

既存の `folders` / `questionSets` を資格区分ごとに読む。

追加フィールド:

- `folders.canonicalFolderId`
- `folders.sourceSharedFolderId`
- `questionSets.canonicalFolderId`
- `questionSets.canonicalQuestionSetId`
- `questionSets.sourceSharedQuestionSetId`

### Phase 2: 管理・表示補助

app や管理画面で canonical ID を使い、同じ科目を共有している資格区分を識別できるようにする。

この段階でも、学習履歴と questionCount は資格区分ごとの materialized ID で分ける。

### Phase 3: 正規化

必要になった場合のみ、`taxonomyFolders` / `taxonomyQuestionSets` / `qualificationMappings` のような collection を追加する。

この段階で、app は `qualificationMappings` から表示 tree を組み立てられる。ただし、既存ユーザーの履歴移行が必要になるため、Phase 1 と Phase 2 を経てから判断する。

## 過去問整備の注意

- 同じ問題が複数資格区分に出る場合でも、最初は資格区分ごとの問題 doc として扱う。
- 共通問題を束ねる場合は、`sourceSharedQuestionId` や自然キーで補助的に紐付ける。
- 学習履歴、正答率、苦手単元は資格区分ごとに分けるのを既定にする。
- 横断学習や重複問題統合は、canonical ID を使った追加機能として後から実装する。

## 公害防止管理者への適用

`output/kougai/category/category.json` は、JEMAI 公式の18試験科目と PDF の出題範囲を表す canonical taxonomy として扱う。

今後、次のような資格区分別 category を生成する。

- `kougai-taiki-1`
- `kougai-taiki-2`
- `kougai-taiki-3`
- `kougai-taiki-4`
- `kougai-suishitsu-1`
- `kougai-suishitsu-2`
- `kougai-suishitsu-3`
- `kougai-suishitsu-4`
- `kougai-dioxin`
- `kougai-soon-shindo`
- `kougai-tokutei-funjin`
- `kougai-ippan-funjin`
- `kougai-chief`

各資格区分は、JEMAI の試験科目表に従って canonical folder を参照する。

## ガス主任技術者への適用

`甲種`、`乙種`、`丙種` は `folder` ではなく `qualification` として扱う。

`法令`、`基礎理論`、`ガス技術` などの科目が `folder` に相当する。

既存 Firestore 由来の question doc ID は維持し、`questionSetId` の付け替えが必要な場合は review ledger と upload gate で確認する。
