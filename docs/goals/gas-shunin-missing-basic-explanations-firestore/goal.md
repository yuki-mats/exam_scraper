# ガス主任技術者 基本解説欠落の全件整備とFirestore反映

## Objective

Firestoreの甲種・乙種ガス主任技術者試験で、表示対象なのに`explanationText`が空の問題を一問ずつ整備し、ローカル正本・公開データ・Firestoreを一致させる。

## Original Request

`isDeleted=false`かつ`isChoiceOnly=false`の問題のうち、基本解説を一問ずつ作成して、Firestoreにアップして更新してほしい。

## Intake Summary

- Input shape: `specific`
- Audience: ガス主任技術者試験の受験者
- Authority: `requested`
- Proof type: `metric`
- Completion proof: Firestoreライブ読取で甲種・乙種の表示対象における`explanationText`欠落が0件で、全更新対象のローカルpatch・変換結果・Firestore readback・既存IDが一致する。
- Goal oracle: 甲種・乙種のFirestoreフォルダ／問題集を起点に、`isDeleted=false`かつ`isChoiceOnly=false`を再集計した欠落件数。
- Likely misfire: `qualificationId`だけで絞って旧ID混在分を対象外にする、直接Firestoreだけを直す、又は一問ごとの根拠確認なしに汎用文を一括生成する。
- Blind spots considered: 旧ID混在、選択肢専用document、法令の出題当時／現行法差分、既存ID維持、実行中job、同じmainにある先行56コミットのpush範囲。
- Existing plan facts: 一問ずつ基本解説を作成し、同じ論点・出題形式の既存類題解説を参照して品質と粒度を揃え、Firestoreへアップして更新後を確認する。サブエージェントは使わず、PMスレッドだけで実行する。

## Goal Oracle

The oracle for this goal is:

`Firestoreの甲種・乙種フォルダ配下で、isDeleted=falseかつisChoiceOnly=falseのexplanationText欠落件数が0。更新対象ごとに既存document ID、ローカルpatch、40_convert／upload payload、Firestore readbackが一致し、品質検査とupload dry-runが成功している。`

PMは各Worker packageのreceiptをこのoracleへ照合する。計画作成、ローカルpatchだけの完成、upload成功ログだけでは完了としない。最終Judge／PM監査が`full_outcome_complete: true`を記録した場合だけ完了する。

## Goal Kind

`specific`

## Current Tranche

2026-08-29のライブ監査で確認した欠落候補782件（甲種1件、乙種781件）を起点に、実行開始時の再読取で母集団を固定する。各問題は問題文・選択肢・正答・既存根拠を読み、一問の解説作成・機械検査・確定保存を閉じてから次の問題へ進む。年度単位の安全なWorker packageで継続し、全対象のmerge／convert／quality gate／upload dry-run、公開前Judge、対象限定Firestore upload、ライブreadback、commit／pushまで連続実行する。

## Non-Negotiable Constraints

- `00_source`、既存Firestore document ID、問題文、選択肢、正答を解説欠落修正の都合で変更しない。
- 甲種・乙種の所属はFirestoreのfolder／questionSet階層を正本にし、旧`qualificationId`混在分を落とさない。
- `isChoiceOnly=true`は契約上`explanationText`を持たないため、欠落修正対象に含めない。
- 問題単位で本文・全選択肢・正答を確認し、汎用文の複製や文字数合わせを行わない。
- 同じ論点・同じ出題形式の既存類題を探し、その解説内容、判断軸、用語、説明粒度を踏まえる。ただし類題の文章を機械的に転載せず、対象問題に固有の正誤理由を書く。
- サブエージェントを起動せず、Scout・Judge・Worker形の作業はPMスレッドが直接実行する。
- 初学者が一度で理解できる日本語にし、正誤、理由、判断基準、他選択肢との差を自然な順序で説明する。
- 法令問題は出題当時と現行法を分け、Lawzilla skillと公式一次法令で根拠を確認する。根拠未確定は推測で公開せず`hold`にする。
- patch責務、merge、convert、quality gate、upload dry-run、Firestore upload、live readbackの正常経路を使い、生成物やFirestoreだけを直接手編集しない。
- 実行中の問題整備job／lockを壊さない。競合時は対象packageを停止し、安全な別package又は監査を進める。
- 既存の未関連変更を破棄・巻き戻し・混在commitしない。mainのorigin/main先行56コミットは所有とpush影響を確認してから扱う。
- 今回scopeの変更は内容別に検証し、mainへcommitし、push可能性を確認して`origin/main`へpushする。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

計画、対象抽出、最初の年度、ローカルpatch、upload dry-runだけでは停止しない。安全な次packageがあれば継続する。外部資格、`isChoiceOnly=true`、削除済み問題、基本解説以外の品質改善は今回の完了条件へ拡張しない。

## Slice Sizing

年度を基本単位にするが、各Workerは年度内を一問ずつ閉じる。甲種1件と乙種2020年は最初の実装packageにまとめ、そこで保存・検証・変換契約を確立する。以後は乙種2021、2022、2023を各packageとして処理する。大量処理でも同じ解説を流用せず、問題identityと根拠を一対一にreceiptへ結び付ける。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.3/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/gas-shunin-missing-basic-explanations-firestore
```

## Canonical Board

Machine truth lives at:

`docs/goals/gas-shunin-missing-basic-explanations-firestore/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
Codex: /goal Follow docs/goals/gas-shunin-missing-basic-explanations-firestore/goal.md.
Claude Code: /goalbuddy Follow docs/goals/gas-shunin-missing-basic-explanations-firestore/goal.md.
```

## PM Loop

1. Read this charter and GoalBuddy `references/goal-execution.md`.
2. Read `state.yaml`; work only on `active_task`.
3. Run the update checker once and re-check current Firestore/repo/job reality before writes.
4. Scout fixes the exact inventory and identity mapping; Judge chooses the largest safe package.
5. Worker writes only inside `allowed_files`, processes one question at a time, and runs every `verify` command.
6. PM records compact receipts and activates the next safe task.
7. Review at first-package, prepublication, rejected-verification, ambiguity, and final boundaries.
8. Before stopping, run `check-can-stop.mjs`; continue while safe required work remains。
