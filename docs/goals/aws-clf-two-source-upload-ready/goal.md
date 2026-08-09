# AWS CLF全件 fast整備・upload-ready収束

## Objective

現在ローカルにあるAWS Certified Cloud Practitioner（CLF-C02）の全問題を、既存の問題整備システム経由でfastモード実行し、未完了工程を収束させて、Firestoreへ書き込まずにupload-ready生成とupload dry-run合格まで完了する。

## Original Request

「aws過去問を整備しておいて」「現在ローカルにあるCLF-C02全件」「Firestore反映直前まで」「fastモードで最速」「問題整備システム経由で実行してね」

## Intake Summary

- Input shape: `recovery`
- Audience: AWS Certified Cloud Practitionerを学習する受験者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: 問題整備システムが現在ローカルにあるCLF-C02全対象を終端状態まで処理し、upload-ready、quality gate、upload dry-runが合格し、Firestore書き込み0件と`00_source`不変をreceiptで確認できる。
- Goal oracle: CLF-C02全対象の問題整備システムrun、manifest、artifact sync、upload-ready、quality gate、upload dry-runが同じ対象とhashを支持し、blocking/hold/pendingが0、Firestore writeが0である。
- Likely misfire: UIを介さず個別スクリプトで成果物だけ生成する、前回03完了だけを全整備完了と誤認する、片方の取得元だけで完了扱いする、fastモードを理由に工程・検証を省略する、又はFirestoreへ反映する。
- Blind spots considered: 既存runとlock、前回03成果物の現行性、未完了04、独自画像gate、両取得元、hold再開、artifact sync、公開版互換、Firestore書き込み境界。
- Existing plan facts: 現在ローカルにあるCLF-C02全件を対象にする。問題整備システム経由だけを使う。fastモードで最速化する。Firestore反映直前のupload-ready・dry-runまでで止める。既存の`aws-clf-explanation-learning-pattern-refresh`は03整備を完了済みだが、今回のScoutが現行状態を再確認する。

## Goal Oracle

The oracle for this goal is:

`問題整備システムのCLF-C02全対象runが終端し、全対象の現行成果物からupload-readyが生成され、quality gateとupload dry-runが合格し、Firestore writes=0、00_source hash不変、blocking/hold/pending=0を最終receiptで確認できること。`

The PM must keep comparing task receipts to this oracle. Planning、対象件数の確認、単一工程の成功、又は片方の取得元だけの成功では完了しない。最終Judge/PM監査が`full_outcome_complete: true`を記録した場合だけ完了する。

## Goal Kind

`recovery`

## Current Tranche

前回整備済み成果物を保持し、問題整備システムで現在の未完了・hold・古い工程だけを選定してfastモードで再開する。両取得元を同じ資格runで収束させ、artifact sync、merge、convert、quality gate、upload-ready、upload dry-runまで連続実行する。安全な作業が残る限り、単一工程の終了では止まらない。

## Non-Negotiable Constraints

- 実行・再開・対象選定は既存の問題整備システム経由だけで行う。個別スクリプトで工程を飛ばさない。
- fastモードを使い、問題間の安全な並列性と既存の最大同時実行設定を活用する。品質工程、保存確認、機械検証は省略しない。
- 現在ローカルにあるCLF-C02の全取得元・全対象を含め、片方だけで完了扱いにしない。
- 確定済み工程を再生成せず、未完了・hold・古い工程から再開する。ただし入力fingerprint変更時は影響工程を正しく再実行する。
- `00_source`を変更・削除・改名しない。
- 既存IDを維持し、merged、convert、upload-readyを手編集しない。
- Firestoreへのupload・writeは行わない。完了点はupload dry-run合格までとする。
- upload-ready生成を妨げる画像gate、04分類、holdは問題整備システムの通常フローで解消し、推測で通過させない。
- 実行中の問題整備システムを不用意に再起動せず、既存runとrepository lockを破壊しない。
- Gitは`main`だけを使い、関連成果物とGoalBuddy receiptsを内容別に検証・コミットして`origin/main`へpushする。

## Fast Mode Policy

- 問題整備システムが提供するfastモードと設定済み並列数を使う。
- 全件を一問ずつ直列実行しない。同じ問の工程順は守り、問同士は安全に並列化する。
- 中断時は新規全件runを重ねず、同じ対象の未完了だけを再開する。
- 進捗はparent manifest、progress、heartbeat、validated/pending/blocked、artifact syncをreadbackする。
- rate limitやwriter backlogが発生した場合は、品質を落とさず既存の自動backoffと再開を使う。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

問題整備システムの全対象run終端、artifact sync、upload-ready、quality gate、upload dry-run、Firestore非書込、`00_source`不変、Git readbackが揃うまで止まらない。

同じ外部障害で3回連続して進めない場合だけ、正確なblocking receiptを残す。承認不要の安全なローカル作業が残る場合は続行する。

## Slice Sizing

最大の安全な単位を使う。対象確認とrun選定は一つのScout、全未完了工程のfast収束は一つのWorker package、公開前artifact収束は一つのWorker package、最終証明は一つのJudgeで扱う。問題やファイルごとに細切れtaskを作らない。

## Canonical Board

Machine truth lives at:

`docs/goals/aws-clf-two-source-upload-ready/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status、active task、receipts、verification freshness、completion truth。

## Run Command

```text
Codex: /goal Follow docs/goals/aws-clf-two-source-upload-ready/goal.md.
Claude Code: /goalbuddy Follow docs/goals/aws-clf-two-source-upload-ready/goal.md.
```

## PM Loop

1. GoalBuddy execution contract、`goal.md`、`state.yaml`を読む。
2. active taskだけを実行し、receiptとboardを更新する。
3. 問題整備システムの現行run・lock・manifestをreadbackしてから操作する。
4. fastモードで安全な最大scopeを連続実行する。
5. phase、risk、検証失敗、最終完了の境界だけJudgeを使う。
6. 終了前にGoalBuddy stop checkerを通し、oracleへreceiptを対応付ける。
