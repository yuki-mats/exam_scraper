# ガス主任技術者甲種2017・2018 問題整備監査

## 対象と完了条件

- 資格: `gas-shunin-kou`（ガス主任技術者甲種）
- 年度: 2017、2018
- 完了条件: 問題整備システムの別セッション評価で、全問が`passed`かつUI上で`公開可能`となること
- 実施日: 2026-07-14

## 評価結果

| 年度 | 問題数 | 合格 | 評価待ち | 要再整備 | 公開可能 | 解説スコア | 正答対応 | 全選択肢確認 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2017 | 58 | 58 | 0 | 0 | 58 | 91-100 | 全問一致 | 全問完了 |
| 2018 | 58 | 58 | 0 | 0 | 58 | 91-98 | 全問一致 | 全問完了 |

2017は初回評価で53問合格、問7・問9・問17・問24・問25の5問が要再整備となった。修正は`output/gas-shunin-kou/questions_json/2017/21_explanationText_added/`へ入れ、`00_source`は変更していない。最終再評価では問7・問9も各5肢すべて確認済み、解説94点で合格し、2017の58問すべてが公開可能になった。

問9の臭気設備は、臭気設備建屋を負圧にして活性炭フィルターとブロワーを設ける直接記載がある[New York State DPS資料](https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B01BB7D44-55EC-442B-A213-26D81E926C4D%7D)と、付臭設備に換気ブロワー・脱臭器を含む[JICA LNG基地資料](https://openjicareport.jica.go.jp/pdf/12057188_02.pdf)へ根拠を寄せた。抽象的な類推は削除し、各肢の正誤を直接根拠で説明する形にした。

## 公開用成果物

- 2017: `output/gas-shunin-kou/questions_json/upload_to_firestore/2017_firestore_20260714_214536.json`（288 documents）
- 2018: `output/gas-shunin-kou/questions_json/upload_to_firestore/2018_firestore_20260714_215221.json`（290 documents）
- 両年度ともmerge後・Firestore変換後のrequirements checkに合格
- 両年度ともupload dry-run成功、アップロード不能レコード0件

## Firestore読取確認

問題整備システムから本番project `repaso-rbaqy4`を資格単位で読み取った。結果は`output/question_review_console/firestore_readback/gas-shunin-kou/manifest.json`へ保存した。

- 9年度、528問、2616 documentsを読取
- 比較不可0問
- 一致380問、差分あり148問
- 2017は58問すべて差分あり
- 2018は58問すべて差分あり

2017・2018の差分は、今回の整備済み成果物がまだ本番Firestoreへ反映されていないことを示す。本監査ではFirestoreへの書込み・公開操作を行っていない。両年度は評価ゲートとdry-runを通過した`公開可能`状態であり、公開操作は別途明示的に実行する。

## 検証

- `python3 -m unittest discover -s tests -p 'test_question_review_*.py'`: 99 tests OK
- `python3 -m unittest tests.test_convert_merged_to_firestore tests.test_documentation_structure tests.test_question_review_workflow_catalog`: 19 tests OK
- `python3 -m py_compile tools/question_review_console/evaluation.py scripts/convert/convert_merged_to_firestore.py`: OK
- `node --check tools/question_review_console/static/app.js`: OK
- `python3 scripts/check/check_00_source_immutability.py`: 4525 files、差分なし
- `git diff --check`: OK

## アクセス

- Mac: `http://127.0.0.1:8765/?qualification=gas-shunin-kou&listGroupId=2017`
- iPhone: `https://macbook-air.tail53d594.ts.net/?qualification=gas-shunin-kou&listGroupId=2017`

iPhoneはTailscaleを有効にし、`yuki.matsuda007@gmail.com`で同じtailnetへ接続する。問題整備システムのサーバーは継続起動している。
