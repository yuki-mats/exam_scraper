# 公害防止管理者 `category.json` 整備メモ

この文書は、`output/kougai/category/category.json` を整備するときの分類正本である。

## 方針

- folder は JEMAI 過去問題ページの18試験科目に対応させる。
- questionSet は JEMAI 公式PDF `pol_subjects1.pdf` の「試験科目の範囲」にある numbered range に対応させる。
- questionSetId は `kougai_qs<folder番号>_<範囲番号>` の固定形式にする。
- 年度別 folder / questionSet は作らない。
- yaku-tik の topic prefix は source 由来の補助情報であり、category の正本にはしない。
- `output/kougai/category/category.json` は、公害防止管理者ファミリー全体で共有する canonical taxonomy として扱う。
- 大気関係第1種〜第4種、水質関係第1種〜第4種、ダイオキシン類、騒音・振動、粉じん、主任管理者などの資格区分別 category は、canonical taxonomy と qualification mapping から生成する。
- 資格区分や難度は folder で表現しない。`folder = 科目`、`qualification = 資格区分`、`mapping = その資格で使う科目セット` として扱う。共通方針は `prompt/qualification_docs/shared_taxonomy_mapping_policy.md` を参照する。

## source

- JEMAI 過去問題ページ: `https://www.jemai.or.jp/polconman/examination/past.html`
- PDF: `https://www.jemai.or.jp/polconman/examination/dd4ht300000005eq-att/pol_subjects1.pdf`
- Local checked copy: `/Users/yuki/Downloads/pol_subjects1.pdf`
- PDF ページ数: 2

## folder

1. `kougai_f01_kougai_soron`: 公害総論
2. `kougai_f02_taiki_gairon`: 大気概論
3. `kougai_f03_taiki_tokuron`: 大気特論
4. `kougai_f04_baifun_tokuron`: ばいじん・粉じん特論
5. `kougai_f05_taiki_yugai_tokuron`: 大気有害物質特論
6. `kougai_f06_daikibo_taiki_tokuron`: 大規模大気特論
7. `kougai_f07_suishitsu_gairon`: 水質概論
8. `kougai_f08_osui_shori_tokuron`: 汚水処理特論
9. `kougai_f09_suishitsu_yugai_tokuron`: 水質有害物質特論
10. `kougai_f10_daikibo_suishitsu_tokuron`: 大規模水質特論
11. `kougai_f11_soon_shindo_gairon`: 騒音・振動概論
12. `kougai_f12_soon_shindo_tokuron`: 騒音・振動特論
13. `kougai_f13_baifun_ippan_tokuron`: ばいじん・一般粉じん特論
14. `kougai_f14_dioxin_gairon`: ダイオキシン類概論
15. `kougai_f15_dioxin_tokuron`: ダイオキシン類特論
16. `kougai_f16_taiki_suishitsu_gairon`: 大気・水質概論
17. `kougai_f17_taiki_gijutsu_tokuron`: 大気関係技術特論
18. `kougai_f18_suishitsu_gijutsu_tokuron`: 水質関係技術特論

## qualification mapping

13種類の資格区分は `output/kougai/category/qualification_mappings.json` を正本にする。JEMAI 公式時間割 `pol_timetable_1.pdf` を主根拠とし、マイナビ転職エージェント記事は13区分と科目一覧の補助確認に使う。

| qualificationId | 資格区分 | canonical folder |
| --- | --- | --- |
| `kougai-taiki-1` | 大気関係第1種 | 01 公害総論 / 02 大気概論 / 03 大気特論 / 04 ばいじん・粉じん特論 / 05 大気有害物質特論 / 06 大規模大気特論 |
| `kougai-taiki-2` | 大気関係第2種 | 01 公害総論 / 02 大気概論 / 03 大気特論 / 04 ばいじん・粉じん特論 / 05 大気有害物質特論 |
| `kougai-taiki-3` | 大気関係第3種 | 01 公害総論 / 02 大気概論 / 03 大気特論 / 04 ばいじん・粉じん特論 / 06 大規模大気特論 |
| `kougai-taiki-4` | 大気関係第4種 | 01 公害総論 / 02 大気概論 / 03 大気特論 / 04 ばいじん・粉じん特論 |
| `kougai-tokutei-funjin` | 特定粉じん関係 | 01 公害総論 / 02 大気概論 / 04 ばいじん・粉じん特論 |
| `kougai-ippan-funjin` | 一般粉じん関係 | 01 公害総論 / 02 大気概論 / 13 ばいじん・一般粉じん特論 |
| `kougai-suishitsu-1` | 水質関係第1種 | 01 公害総論 / 07 水質概論 / 08 汚水処理特論 / 09 水質有害物質特論 / 10 大規模水質特論 |
| `kougai-suishitsu-2` | 水質関係第2種 | 01 公害総論 / 07 水質概論 / 08 汚水処理特論 / 09 水質有害物質特論 |
| `kougai-suishitsu-3` | 水質関係第3種 | 01 公害総論 / 07 水質概論 / 08 汚水処理特論 / 10 大規模水質特論 |
| `kougai-suishitsu-4` | 水質関係第4種 | 01 公害総論 / 07 水質概論 / 08 汚水処理特論 |
| `kougai-soon-shindo` | 騒音・振動関係 | 01 公害総論 / 11 騒音・振動概論 / 12 騒音・振動特論 |
| `kougai-dioxin` | ダイオキシン類関係 | 01 公害総論 / 14 ダイオキシン類概論 / 15 ダイオキシン類特論 |
| `kougai-chief` | 公害防止主任管理者 | 01 公害総論 / 16 大気・水質概論 / 17 大気関係技術特論 / 18 水質関係技術特論 |

資格区分別 `category.json` は次で生成する。

```bash
.venv/bin/python scripts/category/build_kougai_qualification_categories.py
```

生成先は `output/<qualificationId>/category/category.json` とする。各 folder / questionSet は資格区分ごとに materialize し、`canonicalFolderId` / `canonicalQuestionSetId` / `sourceSharedFolderId` / `sourceSharedQuestionSetId` で `kougai` canonical taxonomy に戻れるようにする。

## questionSet 粒度

- 公害総論: 5
- 大気概論: 5
- 大気特論: 6
- ばいじん・粉じん特論: 6
- 大気有害物質特論: 4
- 大規模大気特論: 5
- 水質概論: 6
- 汚水処理特論: 5
- 水質有害物質特論: 3
- 大規模水質特論: 3
- 騒音・振動概論: 14
- 騒音・振動特論: 4
- ばいじん・一般粉じん特論: 5
- ダイオキシン類概論: 7
- ダイオキシン類特論: 5
- 大気・水質概論: 11
- 大気関係技術特論: 17
- 水質関係技術特論: 8

合計 119 questionSets。

## 04 での運用

- `questionSetId` は `output/kougai/category/category.json` の `questionSets[].questionSetId` だけを使う。
- source の `questionLabel` が旧 yaku-tik topic の場合でも、そのまま questionSetId にはしない。
- PDF の numbered range に対応する根拠が薄い場合は、review ledger か `99_model_review_flags/` に保留理由を残す。
- `questionCount` は問題側の questionSetId 再マッピング後に集計する。category 正本上は、公式分類を active に保つため `isDeleted: false` を明示する。
