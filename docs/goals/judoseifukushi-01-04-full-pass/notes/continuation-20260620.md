# 2026-06-20 continuation guard

This note is the compact resume surface for the 7,600-question judoseifukushi 01-04 pass.

## Current truth

- Goal root: `docs/goals/judoseifukushi-01-04-full-pass/`
- Board truth: `docs/goals/judoseifukushi-01-04-full-pass/state.yaml`
- Active task at prep time: `T042`
- Active slice: `2007/question_2007_4.json` (`問76-問100`)
- Progress at prep time: `2760 / 7600` questions complete through 01-04
- Full completion is false until final `T999` audit proves the oracle.

## Resume rule

On resume or after context compression:

1. Read `state.yaml` first and trust `active_task`.
2. Continue `T042` unless a newer active task exists.
3. Keep all work within the active Worker `allowed_files`.
4. After each slice, write the receipt immediately, update `progress`, activate the next slice, and run the GoalBuddy state checker.
5. Do not mark the goal complete after a passing slice, a passing year, or a partial batch.

## Verification

Run this after any board edit:

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.3.8/skills/goalbuddy/scripts/check-goal-state.mjs docs/goals/judoseifukushi-01-04-full-pass/state.yaml
```

The current GoalBuddy plugin is `0.3.8`. `npx goalbuddy doctor` reported that the native Codex `/goal` runtime is not ready in this environment and that `goal_judge.toml` / `goal_worker.toml` are stale. Continue through the board/PM workflow unless the operator asks to update the local GoalBuddy agents.

## Starter command

```text
/goal Follow docs/goals/judoseifukushi-01-04-full-pass/goal.md.
```
