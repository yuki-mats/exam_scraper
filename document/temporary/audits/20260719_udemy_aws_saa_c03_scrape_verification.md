# Udemy AWS SAA-C03取得検証（2026-07-19）

## 対象

- 講座: [【SAA-C03版】AWS認定ソリューションアーキテクト アソシエイト模擬試験問題集](https://tokyo-gas-dx.udemy.com/course/aws-knan/)
- preset: `aws-solutions-architect-associate_udemy`
- 保存group: `udemy-aws-saa-c03`
- 取得方法: ログイン済みブラウザのレビュー画面を一時exportし、Udemy専用parserで`00_source`へ保存

## 表示件数の差異

依頼時と講座タイトルは「6回分390問」だが、取得日の講座概要は合計395問を表示していた。レビュー画面を一意な問題番号で数えると、第1回から第5回は各65問、第6回は70問だった。追加された5問を除外すると現在の取得元を正確に保持できないため、395問を全件取得した。

| 回 | quiz ID | レビュー画面の問題数 |
|---:|---:|---:|
| 1 | `4699792` | 65 |
| 2 | `4632478` | 65 |
| 3 | `4632480` | 65 |
| 4 | `4632482` | 65 |
| 5 | `4709805` | 65 |
| 6 | `4841536` | 70 |
| 合計 | 6 quiz | 395 |

## 保存結果

- source file: 395件
- 問題文欠落: 0件
- 選択肢2件未満: 0件
- 正答欠落: 0件
- 全体解説欠落: 0件
- `source_question_id`重複: 0件
- `public_question_id`重複: 0件
- `question_url`重複: 0件
- `examYear`混入: 0件
- 分野表示なし: 3件（取得元にも分野paneがないため空のまま保持）
- 画像参照: 231件
- 保存画像: 231件
- 空画像: 0件
- parser error: 0件

保存先:

- `output/aws-solutions-architect-associate/questions_json/udemy-aws-saa-c03/00_source/`
- `output/aws-solutions-architect-associate/question_images/udemy-aws-saa-c03/`
- `output/aws-solutions-architect-associate/reports/udemy_aws-knan_scrape_result.json`

## 洗い替え検証

同じライブ取得結果を入力に全件再実行した。2回目は`new=0`、`updated=0`、`verified=395`で完了し、安定IDと内容が収束した。

- source aggregate SHA-256: `fb669c2f1e3d65a7fdae94140dcf77d6f117bd0851d0fce62cc4d3bfddb0fa02`
- image aggregate SHA-256: `c706104254740bc09539ad70482cef59aae53460e95f4845db063f1403f64c74`
- source quality gate: pass
- `00_source` manifest: 新規395件を登録済み

ブラウザexportは一時ファイルとして扱い、認証情報、Cookie、token、試行ID、試行固有のreview URLは`00_source`とrepositoryへ保存していない。
