# ビル管理師2018〜2025年過去問の取得・整備

## Objective

`https://birukan.kakomonn.com/` から2018〜2025年度の過去問を全件取得し、`00_source`を保護したまま、問題形式・正答・解説・問題集分類・現行法監査・公開前検査までを資格単位で完了する。

## Original Request

まず、https://birukan.kakomonn.com/ より、2018〜2025年の`00_source`を取得して整備してほしい。

## Intake Summary

- Input shape: `specific`
- Audience: Repasoでビル管理師を学習する受験者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: 8年度分の`00_source`全件、後工程成果物、カテゴリ、法令監査、quality-gate、upload-readyの検査結果が、証跡とともに揃うこと
- Goal oracle: 年度・問題番号・canonical identityの欠落/重複を除いた全件台帳と、各工程の機械検証・独立評価receipt・公開前readbackが一致すること
- Likely misfire: 取得件数だけを増やして、部分取得・重複・未評価解説・現行法未監査の問題を整備完了と扱うこと
- Blind spots considered: `00_source`の不変性、サイト由来の重複、画像参照、true_false以外の問題形式、出題当時法と現行法の差、category/questionSetId、Firestore公開との境界、既存の他資格変更
- Existing plan facts: 対象URLはbirukan.kakomonn.com、対象年度は2018〜2025、取得先は資格コード`birukan`の`00_source`

## Goal Oracle

The oracle for this goal is:

`2018〜2025年の全問題が安定IDで一意に取得され、00_source不変、整備工程・現行法監査・category・merge/convert・quality-gate/upload-readyの検証が全対象で通り、独立評価結果が公開候補と一致すること。`

## Goal Kind

`specific`

## Current Tranche

取得設定を正本へ登録し、8年度分を完全取得してsource品質を確定する。その後、年度を大きく分けた整備パッケージを順に実行し、最後に公開前検査まで進める。Firestore本番書込みは今回の依頼範囲外で、別途明示承認がある場合だけ行う。

## Non-Negotiable Constraints

- `00_source`は手作業・AI・後工程で編集、削除、改名しない。取得元更新は標準scraperの成功とsource検証を通した場合だけ反映する。
- `questionId`、`originalQuestionId`、`questionSetId`を推測で変更しない。既存IDがある場合は維持する。
- 問題文・全選択肢・正答・解説を一問単位で確定し、判断不能なものは`hold`にする。
- 法令問題は出題当時法と現行法を分けて確認し、一次情報の根拠なしに`verified`へ進めない。
- 他資格の既存未コミット変更を破棄・巻き戻し・一括コミットしない。
- Firestore本番への書込みは、ユーザーの明示承認なしに実行しない。
- コミット・pushは`main`へ限定し、force pushや履歴上書きをしない。

## Stop Rule

最終監査が全対象の取得・整備・検証・評価を完了と証明するまで停止しない。外部公開や本番書込みだけが残る場合は、その境界を明示したreceiptを残す。

