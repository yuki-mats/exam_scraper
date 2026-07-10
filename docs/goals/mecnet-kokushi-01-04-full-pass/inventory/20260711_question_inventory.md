# 医師国家試験 問題整備棚卸し

- 生成日: 2026-07-11 JST
- 対象: `output/mecnet-kokushi/questions_json`
- 目的: `questionType` を含む 01→04 全問再整備の開始前に、現物の coverage と再実行対象を固定する
- 注意: これは棚卸しであり、既存成果物の品質承認ではない

## サマリ

- 出題回: 52回 (`69`〜`120`)
- source問題数: 13,060問
- inventory上のsite/source不一致: `missing_total=0`, `extra_source_total=0`
- 既存01→04成果物の件数coverage: `00_source`〜`30_merged_2` は全52回でsource件数と一致
- 最新40_convertのFirestoreドキュメント数: 65,078件
- 再精査対象: 52回 / 13,060問すべて
- 40_convertが最新30_merged_2より古い、または欠ける回: 19回

## 工程別 coverage

| stage | files present | complete occurrences | total source-level entries | missing/mismatch occurrences |
| --- | ---: | ---: | ---: | --- |
| `00_source` | 52/52 | 52/52 | 13,060 | - |
| `10_questionType_fixed` | 52/52 | 52/52 | 13,060 | - |
| `12_merged_questionType` | 52/52 | 52/52 | 13,060 | - |
| `15_correctChoiceText_fixed` | 52/52 | 52/52 | 13,060 | - |
| `20_merged_1` | 52/52 | 52/52 | 13,060 | - |
| `21_explanationText_added` | 52/52 | 52/52 | 13,060 | - |
| `22_questionSetId_linked` | 52/52 | 52/52 | 13,060 | - |
| `30_merged_2` | 52/52 | 52/52 | 13,060 | - |
| `40_convert` | 52/52 | n/a | 65,078 Firestore docs | - |

## 最新30_merged_2の分布

### questionType

- `fill_in_blank`: 46
- `flash_card`: 160
- `true_false`: 12,854

### questionIntent

- `free_text`: 46
- `select_correct`: 12,121
- `select_incorrect`: 893

### 03法令互換フラグ

`21_explanationText_added` latest:
- `False`: 710
- `True`: 12,350

`30_merged_2` latest:
- `False`: 710
- `True`: 12,350

## 最新30_merged_2の欠落チェック

- `questionSetId` 欠落: 0
- `explanationText` 欠落: 0
- `suggestedQuestions` 欠落: 0

## 再整備方針

今回のユーザー指摘は「既存の `questionType` などの精査が甘い」なので、既存patchが全件あることを完了根拠にしない。全13,060問を再監査対象として、次の順で出題回単位に進める。

1. `00_source` と問題文・選択肢構造を見て `10_questionType_fixed` を再作成または全面確認する。
2. `12_merged_questionType` を生成し、`15_correctChoiceText_fixed` で `questionIntent` と正答テキストを監査する。
3. `20_merged_1` を生成し、`21_explanationText_added` を再確認する。医療制度・法令問題は `lawGroundedExplanationNotNeeded` を保守的に判定する。
4. `22_questionSetId_linked` をMHLWブループリントカテゴリへ再リンクする。
5. `30_merged_2`、`40_convert`、upload dry-runまで通し、出題回ごとにreceiptを残す。

## 優先度

- P0: 全52回を再監査対象に固定する。特定回だけをpilot完了扱いにしない。
- P1: `30_merged_2` が2026-07-10に更新されているが `40_convert` が古い回は、再整備後に必ずconvert/upload dry-runを再実行する。
- P2: `questionType=true_false` が12,854問と支配的なので、問題文が「どれか」型でも実データ上の正誤リスト表現に合わせている箇所を重点確認する。
- P2: `fill_in_blank` / `flash_card` など少数型は誤分類の影響が大きいため先にサンプリング確認する。

## 出題回別棚卸し

| 回 | source | 10 | 15 | 21 | 22 | 30 | 40 docs | 備考 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 69 | 2 | 2 | 2 | 2 | 2 | 2 | 10 | - |
| 70 | 1 | 1 | 1 | 1 | 1 | 1 | 5 | - |
| 71 | 3 | 3 | 3 | 3 | 3 | 3 | 14 | - |
| 72 | 5 | 5 | 5 | 5 | 5 | 5 | 24 | - |
| 73 | 5 | 5 | 5 | 5 | 5 | 5 | 22 | - |
| 74 | 1 | 1 | 1 | 1 | 1 | 1 | 4 | - |
| 75 | 15 | 15 | 15 | 15 | 15 | 15 | 70 | - |
| 76 | 8 | 8 | 8 | 8 | 8 | 8 | 40 | - |
| 77 | 12 | 12 | 12 | 12 | 12 | 12 | 58 | - |
| 78 | 17 | 17 | 17 | 17 | 17 | 17 | 85 | - |
| 79 | 33 | 33 | 33 | 33 | 33 | 33 | 161 | - |
| 80 | 48 | 48 | 48 | 48 | 48 | 48 | 231 | - |
| 81 | 65 | 65 | 65 | 65 | 65 | 65 | 311 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 82 | 55 | 55 | 55 | 55 | 55 | 55 | 268 | - |
| 83 | 63 | 63 | 63 | 63 | 63 | 63 | 304 | - |
| 84 | 63 | 63 | 63 | 63 | 63 | 63 | 300 | - |
| 85 | 84 | 84 | 84 | 84 | 84 | 84 | 404 | - |
| 86 | 90 | 90 | 90 | 90 | 90 | 90 | 440 | - |
| 87 | 88 | 88 | 88 | 88 | 88 | 88 | 432 | - |
| 88 | 100 | 100 | 100 | 100 | 100 | 100 | 492 | - |
| 89 | 115 | 115 | 115 | 115 | 115 | 115 | 574 | - |
| 90 | 112 | 112 | 112 | 112 | 112 | 112 | 560 | - |
| 91 | 129 | 129 | 129 | 129 | 129 | 129 | 645 | - |
| 92 | 144 | 144 | 144 | 144 | 144 | 144 | 720 | - |
| 93 | 128 | 128 | 128 | 128 | 128 | 128 | 640 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 94 | 176 | 176 | 176 | 176 | 176 | 176 | 880 | - |
| 95 | 259 | 259 | 259 | 259 | 259 | 259 | 1233 | - |
| 96 | 337 | 337 | 337 | 337 | 337 | 337 | 1685 | - |
| 97 | 363 | 363 | 363 | 363 | 363 | 363 | 1815 | - |
| 98 | 379 | 379 | 379 | 379 | 379 | 379 | 1895 | - |
| 99 | 530 | 530 | 530 | 530 | 530 | 530 | 2650 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 100 | 530 | 530 | 530 | 530 | 530 | 530 | 2650 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 101 | 500 | 500 | 500 | 500 | 500 | 500 | 2500 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 102 | 500 | 500 | 500 | 500 | 500 | 500 | 2500 | - |
| 103 | 500 | 500 | 500 | 500 | 500 | 500 | 2523 | - |
| 104 | 500 | 500 | 500 | 500 | 500 | 500 | 2515 | - |
| 105 | 500 | 500 | 500 | 500 | 500 | 500 | 2530 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 106 | 500 | 500 | 500 | 500 | 500 | 500 | 2503 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 107 | 500 | 500 | 500 | 500 | 500 | 500 | 2503 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 108 | 500 | 500 | 500 | 500 | 500 | 500 | 2490 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 109 | 500 | 500 | 500 | 500 | 500 | 500 | 2493 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 110 | 500 | 500 | 500 | 500 | 500 | 500 | 2498 | - |
| 111 | 500 | 500 | 500 | 500 | 500 | 500 | 2489 | - |
| 112 | 400 | 400 | 400 | 400 | 400 | 400 | 1992 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 113 | 400 | 400 | 400 | 400 | 400 | 400 | 1996 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 114 | 400 | 400 | 400 | 400 | 400 | 400 | 1988 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 115 | 400 | 400 | 400 | 400 | 400 | 400 | 1995 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 116 | 400 | 400 | 400 | 400 | 400 | 400 | 1989 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 117 | 400 | 400 | 400 | 400 | 400 | 400 | 1992 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 118 | 400 | 400 | 400 | 400 | 400 | 400 | 1988 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 119 | 400 | 400 | 400 | 400 | 400 | 400 | 1984 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |
| 120 | 400 | 400 | 400 | 400 | 400 | 400 | 1988 | 40_convert older than latest 30_merged_2; 30_merged_2 was regenerated/modified on 2026-07-10; rerun 40_convert/upload dry-run before using |

## 詳細JSON

- `docs/goals/mecnet-kokushi-01-04-full-pass/inventory/20260711_question_inventory.json`
