# Gas Shunin Goal Readiness Audit

## Purpose

ガス主任技術者試験の 00_source / 01-04 workflow を続行する前に、既存 Firestore statement-level document ID を `firestoreQuestionIds` で保持できているか、作業対象数が goal と一致しているかを確認した。

## Result

- 01-04 作業単位は 1 `00_source.question_bodies[]` = 1 review / patch row として維持されている。
- 既存 Firestore 由来の問題は 294問、statement-level doc ID は 1449件。
- `firestoreQuestionIds` の重複は 0件。
- `sourceUniqueKeys` の重複は 0件。
- 全問計画は 934問で、甲種412問、乙種522問。
- review ledger は甲種412件、乙種522件で件数一致。
- 完了済みは甲種2019問1の 1問、pending は 933問。
- 次の active task は T114、対象は gas-shunin-kou 2019 問10。

## Upload Safety

通常 upload gate は `needs_source_review` 125件により失敗する。これは意図した安全停止であり、未解決の Firestore / site 差分を確認せずに upload しないための blocker として維持する。

`--allow-source-conflicts` 付きの upload gate は通過したが、これは作業継続用の確認であり、通常 upload ready を意味しない。

## Generated Report

- `output/gas-shunin-goal-readiness-report.json`
- `output/gas-shunin-00-source-contract-final.json`
- `output/gas-shunin-upload-gate-report.json`
- `output/gas-shunin-upload-gate-allow-conflicts-report.json`

## Verification

- `scripts/check/audit_gas_shunin_goal_readiness.py --report output/gas-shunin-goal-readiness-report.json --max-issues 40`
- `scripts/check/check_gas_shunin_00_source_contract.py --report output/gas-shunin-00-source-contract-final.json --max-issues 200`
- `scripts/check/check_gas_shunin_upload_gate.py --report output/gas-shunin-upload-gate-report.json --max-samples 5`
- `scripts/check/check_gas_shunin_upload_gate.py --allow-source-conflicts --report output/gas-shunin-upload-gate-allow-conflicts-report.json --max-samples 5`
- `scripts/check/prepare_qualification_01_04_manual_review.py check output/gas-shunin-kou/review/01_04_manual_review/gas-shunin-kou_01_04_manual_review.jsonl --expected-total 412 --require-stage-files --category output/gas-shunin-kou/category/category.json --allow-pending`
- `scripts/check/prepare_qualification_01_04_manual_review.py check output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl --expected-total 522 --require-stage-files --category output/gas-shunin-otsu/category/category.json --allow-pending`
- `check-goal-state.mjs docs/goals/gas-shunin-01-04-full-pass/state.yaml`
