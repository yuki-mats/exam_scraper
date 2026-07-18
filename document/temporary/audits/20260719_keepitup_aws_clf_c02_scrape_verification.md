# KeepItUp AWS CLF-C02取得監査（2026-07-19）

## 対象

- 取得元: `https://aws.keepitup.jp/CL00/`、`https://aws.keepitup.jp/CL99/`
- 資格: `aws-cloud-practitioner`
- `listGroupId`: `keepitup-aws-clf-c02`
- source: `output/aws-cloud-practitioner/questions_json/keepitup-aws-clf-c02/00_source/`
- 画像: `output/aws-cloud-practitioner/question_images/keepitup-aws-clf-c02/`
- generated report: `output/aws-cloud-practitioner/reports/keepitup_CL00_scrape_result.json`

## 取得結果

ランダム出題ページの表示件数332問に対し、course topから発見した7系列の公開一覧をページ送りし、332個の一意な問題IDを列挙した。各IDの解答・解説ページから問題文、選択肢、正答、解説、問題関連画像を取得し、1問1ファイルで保存した。

| 問題系列 | 件数 |
| --- | ---: |
| `CLF101C` | 49 |
| `CLF201C` | 56 |
| `CLF202C` | 72 |
| `CLF203C` | 84 |
| `CLF204C` | 27 |
| `CLF301S` | 29 |
| `CLF302S` | 15 |
| 合計 | 332 |

単一選択は288問、複数選択は44問である。選択肢数は4肢288問、5肢33問、6肢10問、7肢1問、正答数は1個288問、2個33問、3個11問だった。

## 検証結果

- 保存file 332、record 332、問題ID 332、`source_question_id` 332、問題URL 332、公開ID 332で、すべて一意。
- 問題本文、2件以上の選択肢、1件以上の正答、解説の欠落は0件。正答番号の範囲外は0件。
- 問題文は44〜205文字、解説は451〜1,985文字で、空文字はない。
- source画像参照286、Storage画像参照286、ローカル画像286で一致し、欠落、空file、未参照fileはない。
- 広告・アフィリエイトURL又は画像のsource recordへの混入は0件。
- `examYear`を持つrecordは0件、`questionSourceSite`不一致は0件。
- 7系列から各1問を選んでlive解答ページと独立比較し、問題文、選択肢、正答番号が全件一致した。複数選択の`CLF301S029`は正答2・5、`CLF302S015`は正答3・5で一致した。
- source専用品質ゲートは`[OK] keepitup-aws-clf-c02: stage=source`で通過した。
- `python3 scripts/check/check_00_source_immutability.py --record-new`で332fileを新規scrapeとして登録した。

## 再実行

全件取得後に同じ標準入口を再実行し、全332問をlive解答ページと再照合した。結果は`new=0`、`verified=332`、`missing=0`、`unexpected=0`、内容競合0、エラー0だった。

再実行前後の全`00_source`集約SHA-256は、いずれも次の値で一致した。

```text
f026600495c0af87f83218e026c5bf24500fe66300e8cd5f89a18f5d77b12b1b
```

## 実行入口

```bash
python3 scripts/scrape/run_qualification_scrape.py \
  aws-cloud-practitioner_keepitup \
  keepitup-aws-clf-c02

python3 tools/question_bank/question_bank.py quality-gate \
  --qualification aws-cloud-practitioner \
  --list-group-id keepitup-aws-clf-c02 \
  --mode source
```

再取得で同じ問題IDのlive内容が既存sourceと異なる場合、scraperは既存`00_source`を変更せず、取得元内容の競合として不完了レポートを出す。
