# 公害防止管理者 `category.json` 整備メモ

この文書は、`output/kougai/category/category.json` を整備するときの分類正本である。

## 方針

- folder は 1 つにまとめる
- questionSet は yaku-tik の `questionLabel` / `source_question_id` prefix に 1:1 で対応させる
- 年度別の切り分けはしない
- 同じ topic を別 year で別 ID にしない

## folder

- `公害防止管理者`
  - yaku-tik の 10 topic 群を束ねる最上位 folder

## questionSet 一覧

1. `kougai_qs01_kousou`
   - name: `公害総論`
   - count: 240
   - hints: `公害総論`, `環境基本法`, `環境基準`, `責務`, `総論`
2. `kougai_qs02_baifun`
   - name: `ばいじん・粉じん特論`
   - count: 240
   - hints: `ばいじん`, `粉じん`, `集じん`, `除じん`, `ばいじん特論`
3. `kougai_qs03_daitai`
   - name: `大規模大気特論`
   - count: 160
   - hints: `大規模大気特論`, `排ガス処理`, `NOx`, `SOx`, `ダイオキシン`, `水銀`
4. `kougai_qs04_daisui`
   - name: `大規模水質特論`
   - count: 160
   - hints: `大規模水質特論`, `排水処理`, `公共用水域`, `水質測定`, `水質基準`
5. `kougai_qs05_osui`
   - name: `汚水処理特論`
   - count: 400
   - hints: `汚水処理`, `活性汚泥`, `凝集`, `沈殿`, `ろ過`, `脱水`
6. `kougai_qs06_suigai`
   - name: `水質概論`
   - count: 160
   - hints: `水質概論`, `BOD`, `COD`, `SS`, `pH`, `DO`, `公共用水域`
7. `kougai_qs07_suiyuu`
   - name: `水質有害物質特論`
   - count: 240
   - hints: `有害物質`, `重金属`, `シアン`, `吸着`, `中和`, `水質有害物質特論`
8. `kougai_qs08_taigai`
   - name: `大気概論`
   - count: 160
   - hints: `大気概論`, `大気汚染`, `気象`, `ばい煙`, `浮遊粒子状物質`
9. `kougai_qs09_taitoku`
   - name: `大気特論`
   - count: 240
   - hints: `大気特論`, `燃焼`, `脱硫`, `脱硝`, `集じん`, `排ガス`
10. `kougai_qs10_taiyuu`
   - name: `大気有害物質特論`
   - count: 160
   - hints: `大気有害物質特論`, `VOC`, `ベンゼン`, `ダイオキシン`, `水銀`, `有害大気汚染物質`

## 境界ルール

- `水質概論` は基礎概論として扱い、処理設備の詳細は `汚水処理特論` に寄せる
- `大気概論` は基礎概論として扱い、設備・処理法の詳細は `大気特論` に寄せる
- `大気有害物質特論` は、有害大気汚染物質や微量有害成分を扱う
- `ばいじん・粉じん特論` は、集じん・除じん・粉じん対策を扱う
- `大規模水質特論` と `汚水処理特論` はどちらも水処理だが、前者は大規模施設の管理論点、後者は処理法の詳細に寄せる

## 04 での運用

- `questionSetId` は上の 10 個だけを使う
- 年度別の ID を作らない
- source の `questionLabel` / `source_question_id` prefix が一致しない場合は、まず source の分類ミスを疑う

