# 訂正解説を資料衝突と誤認した保留の調査

## 対象と観測

- 資格: `boiler1`、2017-1 問4
- question ID: `3d72e9edc782c82ab77a9b02`
- run: `20260921T004439265104-0602ddc5`
- 入力: `projected_inputs/5940e5fd52421a03f2b9b2b1.json`
- 02aは、第5肢の「割合が大きい」と元解説の「割合が小さい」の違いを資料衝突として3回保留した。「確認資料上正しい」とする理由に、確認資料の出所・該当箇所は示されていなかった。

## 独立確認

[省エネルギーセンター・平成27年度エネルギー管理士試験 熱分野 課目IV](https://www.eccj.or.jp/mgr1/test_past/pdf/h27_4.pdf)の印刷ページ7（PDFページ8）、問題13(1)3)を画像で確認した。高圧化に伴い蒸発部分の吸収熱量の割合が小さくなり、蒸発器用水管群を省き、火炉水冷壁の放射熱で賄うという説明がある。対象第5肢の「割合が大きい」を支持する記載ではない。

元解説の訂正文と誤った選択肢は、文言が一致することを期待する組合せではない。この違いだけで保留することと、確認済み根拠が互いに衝突するため保留することを区別する必要がある。

## 修正・検証

- 正本 `prompt/02a_prompt_review_correctChoiceText.md` に、引用と訂正説明の区別、および根拠衝突を主張する際の資料・該当内容の明示を追加した。
- 訂正文への自動追随、選択肢の置換、公式解答からの正誤逆算は許可していない。
- 正本が実際の構造化promptへ組み込まれる回帰テストを追加した。
- `tests.test_question_review_qualification_runs`: 200件成功。
- `tests.test_question_review_codex_app_server`、`tests.test_question_review_primary_law_evidence`、`tests.test_aggregate_answer_decomposition`: 合計104件成功。
- 同じprojected inputと修正後の02a正本を使った読み取り専用の実モデル確認は、`candidate`、全5肢の正誤 `[正しい, 正しい, 正しい, 正しい, 間違い]` を返した。特定の正答や上記PDFの結論を追加指示せず実行した。
  - model: `gpt-5.6-luna`、reasoning: `high`
  - thread: `01a0bfab-4e6f-7f90-986f-3cb587c9121e`
  - turn: `01a0bfab-4e81-7183-afed-755615e1b2f1`
  - `changedFiles=[]`

この実モデル確認は一件の読み取り専用確認であり、正式patchの確定や資格全体の品質合格ではない。通常runで再整備して保存・機械検査を通す必要がある。`00_source`、既存ID、Firestoreは変更していない。

## 継続時の状態

一級の上記runは実行中のため、serverを再起動していない。ビル管理士run `20260921T004931323448-4a974bd1` は167問確定・18問保留・2問未完了で安全に中断した。未完了の `1624dded1f477e14a13bb106` と `2811ca6295c4e3c579ac1796` は、二級と同じremote image URL拒否だった。既にmainで修正済みのinline画像入力を、一級run終了後のserver再起動で反映する。保留だけでなくこれら未完了問題も再整備対象に含める。
