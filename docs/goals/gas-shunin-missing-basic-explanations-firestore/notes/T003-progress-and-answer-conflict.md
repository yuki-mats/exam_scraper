# T003 progress and answer conflict

## Completed safely

- 甲種2020の欠落1件 `chiefgasengineerlicense-C-10137` は、同一問題のローカル21 patchを採用した。
- upload artifactは `writeFields=["explanationText"]` を明示し、既存IDを維持したまま基本解説だけを更新する。
- materialization auditは `targetCount=1`、`nonExplanationDriftCount=0`。
- uploader dry-run、実upload、Firestore live readbackが成功した。
- readbackした `explanationText` はartifactと一致し、`isDeleted=false`、`isChoiceOnly=false`、`correctChoiceText=正しい`を維持した。

## 乙種2020

- 対象143件のうち、112件はFirestore同一問題又はローカル21 patchから自動一意解決した。
- 要レビュー31件のうち30件は、対象文・正答・類題・検証済みローカルpatchを照合して個別解説を確定した。
- reviewed ledgerは `ready=142`、`needsReview=1`。

## Human decision required

対象: `gasushunin-otsushu-gizyutsu-2020-1-1`

- Firestore questionText: `国の関与を最小限に`
- Firestore correctChoiceText: `正しい`
- 2020原問題の対象肢、21 patch、人手01-04レビュー、e-Govガス事業法第1条の検証結果: `間違い`
- 正しい解説: `間違い。空欄（イ）の「国の関与を最小限に」が誤り。ガス事業法第1条は、ガス事業の運営を調整することによって、ガスの使用者の利益を保護し、ガス事業の健全な発達を図ることを目的の一部として定めている。`

誤ったFirestore正答に合わせて「正しい」と説明すると誤学習を生む。今回goalは正答を変更しない制約で開始したため、`correctChoiceText`を`間違い`へ修正してから解説を登録してよいか、ユーザー判断を待つ。

## Environment note

Lawzilla skillの手順で既存接続を確認したが、この環境にはLawzilla API key／endpointが登録されていなかった。新規法解釈は行わず、既にe-Gov一次法令まで検証済みのローカルlaw evidenceだけを再利用した。
