# Exam Scraper Site Onboarding Refactor

## Objective

Clean up and refactor `exam_scraper` so future qualification sites can be added with minimal per-site instruction, while preserving the existing `document/operations/exam_pipeline_manual_and_automation.md` workflow and output contracts.

In addition, make uploads strictly conform to repaso-side Firestore schema constraints (required/allowed fields) so scraping/convert/upload outputs cannot drift from production rules.

## Original Request

今後も新しい資格サイトから過去問を取得する予定。細かい指示しなくても各サイトに合わせてスクレイピングして、`exam_pipeline_manual_and_automation.md` に従って問題を整形してアップロードできるようにしたい。無駄なファイルを削除し、全体的にリファクタリングして整理してほしい。優先順位は `2 -> 1 -> 3`、完了証拠は `1 -> 2 -> 3` の順に3まで実行。

## Intake Summary

- Input shape: `open_ended`
- Audience: repository owner and future Codex runs adding new qualification sites
- Authority: `requested`
- Proof type: `artifact + test + review`
- Completion proof: operations docs and GoalBuddy state clearly show current flow and remaining work; representative commands pass for existing flow and SG; unnecessary files are removed or explicitly retained with rationale.
- Likely misfire: only cleaning files or only fixing SG, without creating a reusable site-onboarding workflow that future scrapers can follow.
- Blind spots considered: current dirty worktree may contain owner changes; category/questionSetId ownership belongs to category workflow; source JSON must match existing `00_source` shape; deletion needs evidence before removal.
- Existing plan facts: priority order is cleanup first, new-site standardization second, SG finish third; completion evidence order is docs/state first, commands second, deletion/retention cleanup third; no visual board.

## Goal Kind

`open_ended`

## Current Tranche

Complete a full local cleanup and standardization tranche:

1. Audit current repository structure, generated artifacts, scraper variants, output shapes, and operations documentation.
2. Update the operations documentation and task state so the owner can see what remains and how future site onboarding should work.
3. Refactor or remove unnecessary local code/files only after evidence shows they are redundant, generated, obsolete, or misplaced.
4. Establish a reusable scraper onboarding pattern and verification gates.
5. Bring the SG scraper into that pattern and verify the representative pipeline.

## Non-Negotiable Constraints

- Follow `document/operations/exam_pipeline_manual_and_automation.md` as the source of operational truth.
- Preserve existing output shape under `output/<qualification>/questions_json/<list_group_id>/00_source/`.
- Do not create category data automatically for SG or future sites; category/questionSetId linkage is owned by the category workflow.
- Treat repaso-side Firestore contract as source of truth:
  - `/Users/yuki/StudioProjects/repaso/firestore.rules`
  - `/Users/yuki/StudioProjects/repaso/lib/firestore/models/folder_doc.dart`
  - `/Users/yuki/StudioProjects/repaso/lib/firestore/models/question_set_doc.dart`
  - `/Users/yuki/StudioProjects/repaso/lib/firestore/models/question_doc.dart`
- Do not revert or delete unrelated owner changes without evidence and an explicit board receipt.
- Treat `output/`, local generated files, and dirty worktree state carefully; classify before deleting.
- Keep changes useful for future qualification sites, not only SG.

## Stop Rule

Stop only when a final audit proves the full owner outcome is complete.

Do not stop after planning, discovery, or a single SG fix if safe local cleanup or standardization work remains.

Do not delete files based on guesses. A cleanup Worker must list evidence for each removed or retained path in its receipt.

## Slice Sizing

Use the largest safe useful slice:

- One Scout pass may map docs, code, outputs, generated files, and verification commands together.
- One Worker package may update docs and lightweight registry/config files together when they are part of the same onboarding contract.
- Deletion/archival should be its own Worker package if it touches many paths or generated artifacts.
- SG finishing should come after the shared pattern is clear unless Scout/Judge finds an urgent correctness blocker.

## Canonical Board

Machine truth lives at:

`docs/goals/exam-scraper-site-onboarding-refactor/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/exam-scraper-site-onboarding-refactor/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Work only on the active board task.
4. Preserve the priority order: cleanup, standardization, SG finish.
5. Write a compact task receipt and update `state.yaml`.
6. Continue with the next safe task until final audit records `full_outcome_complete: true`.
