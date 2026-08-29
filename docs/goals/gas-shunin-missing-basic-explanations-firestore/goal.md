# ガス主任技術者 基本解説欠落の全件整備とFirestore反映

## Objective

Firestoreの甲種・乙種ガス主任技術者試験で、当初`explanationText`が空だった全782問を一問ずつ再監査し、問題文・対象選択肢・正答・解説が同じ命題を指す状態へ整備して、ローカル正本・公開データ・Firestoreを一致させる。

## Original Request

`isDeleted=false`かつ`isChoiceOnly=false`の問題のうち、基本解説を一問ずつ作成してFirestoreへ更新し、問題文・選択肢・正解・解説に矛盾がないか確認したうえで、矛盾が判明した全対象を改めて一問ずつ整備する。

## Intake Summary

- Input shape: `specific`
- Audience: ガス主任技術者試験の受験者
- Authority: `requested`
- Proof type: `metric`
- Completion proof: Firestoreライブ読取で甲種・乙種の表示対象における`explanationText`欠落が0件であることに加え、当初対象782問すべてに一問別の根拠台帳があり、対象選択肢・正答・解説の意味対応とFirestore readbackが一致する。
- Goal oracle: 当初対象782問の一問別台帳を起点に、問題本文、引用対象選択肢、正答、解説、根拠資料、Firestore readbackを照合した意味整合検査。
- Likely misfire: 欠落0件や正答ラベル接頭辞だけを合格条件にして、古い`originalQuestionChoiceText`又は別問題の解説が残ったまま完了する。
- Blind spots considered: 旧ID混在、選択肢専用document、法令の出題当時／現行法差分、既存ID維持、実行中job、同じmainにある先行56コミットのpush範囲。
- Existing plan facts: 一問ずつ基本解説を作成し、同じ論点・出題形式の既存類題解説を参照して品質と粒度を揃え、Firestoreへアップして更新後を確認する。サブエージェントは使わず、PMスレッドだけで実行する。

## Goal Oracle

The oracle for this goal is:

`当初対象782問の全件について、問題本文・引用対象選択肢・正答・解説・根拠資料が一問別台帳上で一致し、Firestore readbackでも同じfieldが一致する。加えてisDeleted=falseかつisChoiceOnly=falseのexplanationText欠落件数が0である。`

PMは各Worker packageのreceiptをこのoracleへ照合する。計画作成、ローカルpatchだけの完成、upload成功ログだけでは完了としない。最終Judge／PM監査が`full_outcome_complete: true`を記録した場合だけ完了する。

## Goal Kind

`specific`

## Current Tranche

2026-08-29のライブ監査で確認した当初対象782件（甲種1件、乙種781件）を固定母集団とする。前回の欠落0件という完了判定は、問題・選択肢・正答・解説の意味対応を検証していなかったため無効化する。各問題は問題文・対象選択肢・正答・既存根拠・類題を読み、一問別台帳、機械検査、確定保存を閉じてから次へ進む。全782問の再監査後に限定artifact、公開前監査、Firestore更新、ライブreadback、commitまで連続実行する。

## Non-Negotiable Constraints

- `00_source`、既存Firestore document ID、現在の問題文と選択肢を変更しない。正答又は`originalQuestionChoiceText`が原問題と矛盾する場合は、根拠を一問別に確定した対象だけを修正する。
- 甲種・乙種の所属はFirestoreのfolder／questionSet階層を正本にし、旧`qualificationId`混在分を落とさない。
- `isChoiceOnly=true`は契約上`explanationText`を持たないため、欠落修正対象に含めない。
- 問題単位で本文・全選択肢・正答を確認し、汎用文の複製や文字数合わせを行わない。
- `true_false`は`questionText`内の引用文と`originalQuestionChoiceText`が一致し、解説がその引用文の真偽理由を説明していることを必須とする。正答ラベルの接頭辞一致だけでは合格にしない。
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
