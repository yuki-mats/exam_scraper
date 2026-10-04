# SG・最初の500問で保留になった74問の再整備

対象は2026-10-04のrun `20261004T142646638795-27236c4b`で保留になった74問。704問全部の整備完了又はFirestore公開を意味しない。残り204問はこの再整備の対象外。

## 再発防止

- 午後問題の解答欄を取得元の安定IDから確定し、共通の解答群を別の空欄と混同しない。
- 原本に明記された順不同の範囲を根拠として保存し、候補ごとの独立判定と最後に照合する。表示解答の一例だけに限定しない。
- 集約回答の原文spanを保持し、ローマ数字・数字・単独項目を含む組合せを、各記述の独立判定後に照合する。正誤配列を自動で書き込まない。
- 法令候補の時点別判定、正答、各肢の監査状態、問題全体の監査状態を保存前に照合する。未実施の独立監査はreviewStateで表し、根拠から確定済みの差分と混同しない。
- 法令監査の二次・必要な三次確認を別sessionで実行し、対象ID、入力hash、実行ID、一次資料URLを記録する。
- 中断回収も資格単位のprocess leaseを保持し、別processが所有する実行中の資格を中断・rollbackしない。
- 必須検査のpatch照合ではsourceQuestionKey、reviewQuestionId、sourceRecordRefを使い、午後の共通URLから複数問題へ誤結合しない。
- 通常の再整備にも、対象問題の現在内容hashに合う人間の指摘を渡す。公開preview、保存済み実行plan、問題別queue、候補inputまで保持し、review更新時は古いpreviewを失効させる。対象外field、別問題、古い内容の指摘を使わない。

## 一問ずつの精査

問題文、全選択肢、正答、解説、法令根拠を各問で直視し、共通事例20件の全文と、正答判断に使う図表も確認した。機械検査を通った問題でも、根拠や主語が誤っている解説は再整備した。

目視対象・現在内容hash・再整備指摘・検査ログの保存先は `tmp/sg_held_recovery_20261004/`。このディレクトリのJSONは監査用projectionであり、公開用merged artifactではない。取得原文を改変せず、修正は通常の整備工程及び一問reviewのpatchへ保存した。

## 現行法の条件不足が残る一問

- 対象: 2018年春・午後問1設問2(3)
- questionId: `0c92d2a32566c62927cd2b73`
- 原本は「高度な暗号化」を施したデータの誤送信を含むが、復号鍵の管理条件を示していない。
- 現行法の報告対象外とするには、第三者が読み取れない暗号化に加えて、復号手段の適切な管理も必要。[個人情報保護委員会 Q6-19](https://www.ppc.go.jp/all_faq_index/faq1-q6-19_/)
- 顧客情報DBに病歴が含まれる点は本文で確認できる。現在は、このDB全体の滅失についても報告要件の照合が必要であり、出題時の正答をそのまま現行法の正答として保存しない。

ユーザーは「公式過去問を維持し、この1問は公開対象外にする」と明示決定した。原文・選択肢・公式解答は維持し、条件補充や独自問題化は行わない。この一問の対応方針は公開対象外で確定したが、現行法による内容検証に合格したことにはしない。通常の法令監査holdを維持し、別session評価・公開前gateを迂回しない。

判断の対象identity・source hash・理由は `tmp/sg_held_recovery_20261004/publication_exclusion_decision.json` に保存した。限定公開を準備する場合も、このIDを除いた検証済み対象集合を使い、既存のvalidated summary照合及びserverのpublishReady計算を通す。監視ではこの確定済みの除外を新しい障害として繰り返し通知しない。

## 関連検証と限界

人間の指摘の引継ぎ、preview再利用、記録scopeに対する関連テスト68件は成功した。別の広めの復旧回帰検査87件では、法令監査fixtureに関わる3件が失敗した。この追加経路を無効にした比較でも同じ3件が失敗しており、全体検証成功とはしていない。ログは `/tmp/sg-human-feedback-targeted-final.log`、`/tmp/sg-human-feedback-tests.log`、`/tmp/sg-human-feedback-baseline-failures.log`。以前の集約回答・法令候補・patch coverage検証94件も成功した（`/tmp/sg-aggregate-law-tests.log`）。

補助調査が長期化した一問human reviewは、このrunだけのbaselineへ回復し、内容差分が残らないことを確認して中断記録を保存した。共有serverや別資格の実行は停止していない。人間の指摘を候補へ渡さない経路が残っていたため、構造化候補の通常再整備で引継ぎを修正し、開始前previewと実際の問題別queueに同じreview IDがあることを確認した。

## 最終結果

最終確認日時: 2026-10-04T19:25:55.650210+09:00

| 対象 | 結果 |
| --- | --- |
| 元の保留対象 | 74問・ID重複なし |
| 正答・解説の内容確認と整備 | 73問 |
| ユーザー判断による公開対象外 | 1問（原文保持・現行法監査hold維持） |
| 未確定の対応方針 | 0問 |
| 現在内容と再読記録のhash | 73問すべて一致 |
| 選択肢と正答／必須field／patch coverage | 3系統すべて成功 |
| 取得原文 | 36ファイル・704問・source ID一意704件、保護manifestと全SHA256一致 |
| 今回対象外の未処理問題 | 204問 |

解説の最終整備runは `20261004T191421110713-f4b6b396`（6問）と `20261004T192138362389-2ceb3cec`（1問）。どちらも終端は `succeeded`、`receiptValidated=true`、対象全問 `validated`、保留0件。出題時と現行法の正答が異なる罰則問題の二次・三次独立確認は `20261004T183805729352-ba0bbccf` に保存されている。最終文面の処罰対象・刑期・罰金額は当日e-Gov APIのXMLで再照合した。Lawzilla wrapperへ接続できなかったため、公式e-Gov APIで確認した。

修正はローカルpatchへ保存した。各runの `artifactSync=blocked` は残っており、年度全体のmerge・convert・upload-ready、正式な別session評価、Firestore反映及びアプリ公開は完了していない。73問の内容確認を公開準備完了と混同しない。

取得元には、2018年秋・午後問3設問2(2)の選択肢1が「名誉段損」と書かれている。取得元ページでも同じ表記であり、取得漏れやスクレイピングによる文字化けではない。原文と取得時bytesを維持し、解説では「名誉毀損」として意味を説明している。表示用選択肢を正式に訂正する場合は、既存の公式根拠付き24工程を使う。

監査用成果物は `tmp/sg_held_recovery_20261004/` に保存した。`final_receipt.json` は74問の対応方針、73問の現在内容hash、公開除外の判断、終端run、原文不変、必須検査を参照する。これは監査記録であり、通常のmodel評価receiptや公開用question_summaryの代わりに使わない。

| 監査成果物 | SHA256 |
| --- | --- |
| `final_receipt.json` | `c8d8015bdaf9f038acf96dede20267338d8d5254e65a2e41525f1139166e67c7` |
| `read_receipt.json` | `4eacd9f746126cf9544df791b81c73451f97a90656c30693c51a1ff040f8cedc` |
| `mechanical_checks.json` | `acd0576b4b875fdb0278c417df5216f6a11933b736cd27f7d4e941301bbdf717` |
| `projection_receipt.json` | `76d210f533231c74a1844aadf7577304dc8f2875e328724cdc5f5284db3bbc8d` |
| `source_final_verification.json` | `fd59344a4cdedb9d0a39ed2a3089e58578e97b1ea0ac64f31ad4aea18fbeca98` |
| `publication_exclusion_decision.json` | `cb5d83235cc13cfa6c9147f572387246ba74632b23cfa8ac4fe8d753c0d70b3e` |

