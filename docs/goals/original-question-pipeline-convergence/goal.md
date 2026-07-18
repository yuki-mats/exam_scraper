# Original Question Pipeline Convergence

## Objective

サイト取得問題を、資格を追加しても再利用できる方法で正確に取得し、既存の公式過去問フローと共存させながら暗記プラス独自問題へ変換する。レビュー、検証、Firestore向けartifact生成まで安全に通せるよう、文書、仕様、設定、実装、テストを一貫した状態へ収束させる。

## Original Request

全てのドキュメントや実装、仕様、仕組みを整備し、整合性をあるべき品質へそろえてほしい。

## Intake Summary

- Input shape: `existing_plan`
- Audience: repository owner、今後の問題整備を担当するエージェント、暗記プラス利用者
- Authority: `requested`
- Proof type: `test + artifact + review`
- Completion proof: 独自問題fixtureが`00_source`から05、既存01〜04相当、Merge、Convert、upload dry-run相当まで通り、公開artifactが`isOfficial=true`、`examSource="独自問題"`、`examYear` omit、取得元原文・provenance非混入となる。公式過去問の既存回帰テストも通り、全SSOT・設定・READMEに意味上の矛盾が残らない。
- Goal oracle: 対象fixtureのend-to-endテスト、公式過去問回帰テスト、ドキュメント構造・意味整合チェックをまとめた検証結果
- Likely misfire: 文書だけを更新して未実装のまま完了とすること、又は既存の公式過去問を壊して独自問題だけ通すこと
- Blind spots considered: 現在のupload pipelineは`examYear`を実質必須としている、GUI工程SSOTに05がない、Mergeは05を読まない、取得元原文の漏えい検査がない、既存のガス主任技術者成果物が未コミットである、repaso側schemaとの互換を実データ形で確認する必要がある
- Existing plan facts: 取得元URLは松田さんが事前確認する、問題ごとのorigin分類は追加しない、`contentOriginType`は使わない、公開区分は`isOfficial`のみ、独自問題は`examYear` omitかつ`examSource="独自問題"`、取得元1問から原則1問、`source_question_id`と既存`listGroupId`を再利用、取得元原文は`00_source`だけ、05のみ新しいpatch層として追加、類似率閾値は設けず完全一致を拒否する

## Goal Oracle

The oracle for this goal is:

`Ping-t CLF-C02 547問の取得集合一致 + 再利用可能なsite adapter + 独自問題fixtureのend-to-end成功 + 公式過去問回帰成功 + repo内SSOT/設定/READMEの意味整合監査成功`

PMは各receiptをこのoracleへ照合する。個別unit test、文書追加、05ディレクトリ作成のいずれか一つだけでは完了としない。最終Judge又はPM監査が`full_outcome_complete: true`を記録した場合だけ完了する。

## Goal Kind

`existing_plan`

## Current Tranche

現在の設計判断を維持したまま、次を連続して完了する。

1. repo全体の現行契約、実装経路、テスト、repaso互換、未整合箇所を証拠付きで確定する。
2. Ping-tを資格非依存のsite adapterとして実装し、AWS CLF-C02の547問を`00_source`へ正確に取得して、一覧ID集合との一致と再実行no-opを証明する。
3. 05のpatch契約、作成prompt、工程設定、projection・Mergeを一つの実行可能な縦切りとして実装する。
4. `examYear`条件付き必須、`examSource`、`isOfficial`、原文非混入、完全一致拒否をConvert・quality gate・uploadへ接続する。
5. GUI、CLI、README、AGENTS、operations、reference、promptを実装済みの現在形へそろえる。
6. 独自問題fixtureと公式過去問fixtureの両方でend-to-end検証し、最終監査を行う。

## Non-Negotiable Constraints

- `00_source`の既存ファイルを編集、削除、改名しない。
- 既存の`questionId`、`originalQuestionId`、公開済みFirestore IDを理由なく変更しない。
- `contentOriginType`、承認field、出典サイト別公開fieldなどを追加しない。
- 公開区分は`isOfficial`の二択だけとする。
- 独自問題では`examYear`を空文字や`null`にせずfield自体をomitする。
- 取得元の問題文、選択肢、解説、URL、`source_question_id`をFirestoreへ入れない。
- 公式過去問の既存動作と年度manifestを維持する。
- 現在のガス主任技術者の未コミット成果物を変更、stage、commit、破棄しない。
- 新しい管理項目、台帳、ディレクトリは、既存契約で表現できない必須責務に限る。
- Ping-t認証情報はリポジトリや生成artifactへ保存しない。
- 547問の完了は件数だけでなく、一覧ID集合と保存ID集合の一致、必須内容、画像参照、再実行時のhash不変で判定する。
- 本番Firestoreへの書き込みや外部サイトの実取得は、ローカルfixtureとdry-runの完了証拠に含めない。

## Stop Rule

最終監査が元の依頼全体を満たすと証明するまで停止しない。

文書整理、Scout調査、単一Worker package、独自問題だけの成功、公式過去問だけの成功では停止しない。安全なローカル作業が残る場合は、次の最大の可逆な作業単位へ進む。

本番資格データ又は外部認証が必要な最終確認だけが残った場合も、ローカルfixture、dry-run、read-only互換確認を完了し、残る外部確認を正確にreceiptへ記録する。

## Slice Sizing

最初のScoutは、文書、設定、05の入力形、Merge順序、Convert、upload、quality gate、レビューUI、repaso schema、既存テストをまとめて地図化する。

Workerは、単なるhelper追加ではなく、利用可能な縦切りを完了する。05契約からMergeまで、公開field条件からupload dry-runまで、又はdocs/UI/CLI収束から意味監査までを一つのpackageとして扱う。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.0/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/original-question-pipeline-convergence
```

## Canonical Board

Machine truth lives at:

`docs/goals/original-question-pipeline-convergence/state.yaml`

## Run Command

```text
/goal Follow docs/goals/original-question-pipeline-convergence/goal.md.
```

## PM Loop

1. このcharterと`state.yaml`を読む。
2. GoalBuddyの`references/goal-execution.md`を読む。
3. active taskだけを実行し、receiptを残す。
4. 既存の未コミット成果物を保護する。
5. 各Worker後にoracleへ照合し、安全な次taskがあれば継続する。
6. 最終監査でのみ`full_outcome_complete: true`を記録する。
