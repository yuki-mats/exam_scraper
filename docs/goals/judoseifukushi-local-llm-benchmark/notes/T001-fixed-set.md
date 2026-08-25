# T001 固定評価集合

## 選定規則

選定時に使用したfieldは`source_question_id`、`public_question_id`、`examYear`、問題文、選択肢、`questionIntent`、`sourceAnswerStatus`、画像有無、source locatorだけである。`answer_result_text`、`correctChoiceText`、`answerTableCorrectChoiceNumbers`、review結果、既存model結果は、30問を固定するまで参照していない。

同じ層の候補は、論点の重複を避けた後、`source_question_id`昇順で固定した。oracleは固定後にだけ照合し、実行harnessから分離する。

## 柔道整復師30問

| 層 | public ID | source ID | 年 | locator | 評価目的 |
|---|---|---|---:|---|---|
| 正答欠損 | `9c3273bf54057cd0` | `judoseifukushi:2007:r15:q146` | 2007 | `question_2007_6.json#question_bodies[20]` | 組合せ問題で推測せずhold |
| 正答欠損 | `fa0d4e2042e65b59` | `judoseifukushi:2003:r11:q124` | 2003 | `question_2003_5.json#question_bodies[23]` | 医学知識で正答を捏造せずhold |
| 現行法 | `c5167b46942fb08e` | `judoseifukushi:2024:r32:q041` | 2024 | `question_2024_2.json#question_bodies[15]` | 療養費受領委任 |
| 現行法 | `0eb595c2c11278f5` | `judoseifukushi:2024:r32:q042` | 2024 | `question_2024_2.json#question_bodies[16]` | 免許制度 |
| 現行法 | `8987ec55216cbc63` | `judoseifukushi:2025:r33:q045` | 2025 | `question_2025_2.json#question_bodies[19]` | 広告規制 |
| 旧法 | `dfb3fe84e07f47f9` | `judoseifukushi:1993:r1:q091` | 1993 | `question_1993_4.json#question_bodies[15]` | 免許権者の時点差 |
| 旧法 | `1ebaca9b85c6dd6e` | `judoseifukushi:2002:r10:q094` | 2002 | `question_2002_4.json#question_bodies[18]` | 施術所構造設備基準の時点差 |
| 数値 | `130d5c77cc5c2b8b` | `judoseifukushi:2005:r13:q089` | 2005 | `question_2005_4.json#question_bodies[13]` | 不快指数・複数正答 |
| 数値 | `5c1ab42128e170ab` | `judoseifukushi:2015:r23:q067` | 2015 | `question_2015_3.json#question_bodies[16]` | 脂質エネルギー量 |
| 数値 | `ee361042818c9b9f` | `judoseifukushi:2023:r31:q166` | 2023 | `question_2023_7.json#question_bodies[15]` | 糖尿病診断値 |
| 数値 | `c582757f2a97a68a` | `judoseifukushi:2024:r32:q172` | 2024 | `question_2024_7.json#question_bodies[21]` | SpO2臨床判断 |
| 数値 | `77eea1850fecb0ab` | `judoseifukushi:2025:r33:q166` | 2025 | `question_2025_7.json#question_bodies[15]` | HbA1c目標値 |
| 長文比較 | `b22f649e0b947399` | `judoseifukushi:2020:r28:q239` | 2020 | `question_2020_10.json#question_bodies[13]` | 長文臨床・複数正答 |
| 長文比較 | `c1363e3f0487174c` | `judoseifukushi:2025:r33:q248` | 2025 | `question_2025_10.json#question_bodies[22]` | 小児変形と画像所見比較 |
| 長文比較 | `85cdb2c54567b04a` | `judoseifukushi:2026:r34:q249` | 2026 | `question_2026_10.json#question_bodies[23]` | 長文臨床・固定指導・複数正答 |
| 否定設問 | `8300bc9178872872` | `judoseifukushi:2025:r33:q093` | 2025 | `question_2025_4.json#question_bodies[17]` | 血液生理 |
| 否定設問 | `31fa2012e42b713a` | `judoseifukushi:2023:r31:q125` | 2023 | `question_2023_5.json#question_bodies[24]` | 自己免疫疾患の組合せ |
| 否定設問 | `23af5153f13d1a5c` | `judoseifukushi:2021:r29:q197` | 2021 | `question_2021_8.json#question_bodies[21]` | 鎖骨骨折姿勢 |
| 否定設問 | `63ed9af2dc1cda2b` | `judoseifukushi:2020:r28:q225` | 2020 | `question_2020_9.json#question_bodies[24]` | 肩関節脱臼の理由比較 |
| 否定設問 | `715ae907c4b74436` | `judoseifukushi:2023:r31:q244` | 2023 | `question_2023_10.json#question_bodies[18]` | 長文臨床 |
| 最新医学 | `a28db6ba1c8e4c65` | `judoseifukushi:2025:r33:q073` | 2025 | `question_2025_3.json#question_bodies[22]` | 神経解剖 |
| 最新医学 | `01bd0255c9fc8371` | `judoseifukushi:2025:r33:q092` | 2025 | `question_2025_4.json#question_bodies[16]` | 生理・産褥 |
| 最新医学 | `2380e0e939cce3b7` | `judoseifukushi:2025:r33:q217` | 2025 | `question_2025_9.json#question_bodies[16]` | 手根骨脱臼 |
| 最新医学 | `1d2014527a7acabb` | `judoseifukushi:2026:r34:q121` | 2026 | `question_2026_5.json#question_bodies[20]` | 病理・充血 |
| 最新医学 | `da301fba93a4d48c` | `judoseifukushi:2026:r34:q171` | 2026 | `question_2026_7.json#question_bodies[20]` | 神経疾患 |
| 最新医学 | `ab3c9ed5d41aa3fd` | `judoseifukushi:2025:r33:q067` | 2025 | `question_2025_3.json#question_bodies[16]` | 女性生殖器解剖 |
| 最新医学 | `8168f912ed724458` | `judoseifukushi:2024:r32:q110` | 2024 | `question_2024_5.json#question_bodies[9]` | 運動学 |
| 最新医学 | `e1a6f5219f4e01d8` | `judoseifukushi:2024:r32:q061` | 2024 | `question_2024_3.json#question_bodies[10]` | 循環解剖 |
| 最新医学 | `a2f7e9ea703084cb` | `judoseifukushi:2024:r32:q055` | 2024 | `question_2024_3.json#question_bodies[4]` | 関節解剖 |
| 最新医学 | `5d85b2e35574d926` | `judoseifukushi:2026:r34:q138` | 2026 | `question_2026_6.json#question_bodies[12]` | 微生物・消毒 |

## 二級建築士補助6問

| original ID | locator | 評価目的 |
|---|---|---|
| `dd07b4977677b7bb` | `85010/00_source/question_85010_3.json#question_bodies[0]` | 荷重・外力の説明 |
| `3239d392acd6236c` | `85010/00_source/question_85010_3.json#question_bodies[1]` | 設計用地震力・正答精査 |
| `64032ef7f4bac816` | `85010/00_source/question_85010_3.json#question_bodies[2]` | 問題形式 |
| `64bd269e44533561` | `85010/00_source/question_85010_3.json#question_bodies[8]` | group choice・画像・配筋計算 |
| `3b24e06367db4222` | `85003/00_source/question_85003_2.json#question_bodies[17]` | flash card・法令時点hold |
| `ed7d14b661421a12` | `85003/00_source/question_85003_2.json#question_bodies[19]` | flash card・画像・梁計算 |

## 実行・判定案

- 柔道整復師: 24/30以上をlocal監査合格。
- 全体: 29/36以上をlocal監査合格。
- 重大な正答誤り、法令時点混同、医学的捏造、画像誤認は0件。
- 正答欠損2問は2/2で根拠付きhold。正答確定は重大事故。
- Codex-only比でCodex call 30%以上、Codex input+output token 20%以上を削減。
- fallback、repair、holdをlocal-only成功へ数えない。
- `localPass + remaining < threshold`なら品質不採用を早期確定する。速度だけでは停止しない。
- 現repoのprofileは`codex_only operational=true`、`local_generate_codex_audit operational=false`。隔離copyだけでlocalを有効化する。
- `qwen3:14b`と`qwen3.5:27b`を候補とし、候補ごとに独立した早期停止規則を適用する。

