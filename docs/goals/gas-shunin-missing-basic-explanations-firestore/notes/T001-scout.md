# T001 Scout receipt note

## Live baseline

- Selection: gas-shunin 甲種・乙種 folder/questionSet descendants, `isDeleted=false`, `isChoiceOnly=false`, and blank or absent `explanationText`.
- Missing: 782 documents: 甲種 1, 乙種 781.
- 甲種: 2020 = 1 (`chiefgasengineerlicense-C-10137`).
- 乙種: 2020 = 143, 2021 = 222, 2022 = 189, 2023 = 227.
- The 乙種 targets include 80 legacy `chiefgasengineerlicense-C-*` IDs and 701 `gasushunin-otsushu-*` IDs. Folder/questionSet ownership, not legacy `qualificationId`, determines 甲乙.

The fixed inventory is `T001-firestore-missing-inventory.json`. It records each target ID, year, questionSet, question text, answer, old-plan mapping, and same-questionSet explanation examples.

## Identity and explanation-source findings

- The old all-question plan maps the one 甲種 document exactly to the existing 2020 explanation patch. It does not map the 781 乙種 documents because these are legacy/previous-publication IDs outside the current source-to-public-ID plan.
- All 782 targets have at least one explanation-bearing question in the same questionSet to use as a style and concept reference.
- Hierarchical live matching (same questionSet/year/questionText/answer, then year/questionText/answer, then original body/choice/answer) finds a usable existing explanation for 561 targets, with 4 additional 2020 collisions that require topic-aware selection because stale original fields point at a different subject.
- Exact local `21_explanationText_added` text matching adds 31 targets.
- The remaining 186 targets require per-question review: 甲種 1; 乙種 2020 = 24, 2021 = 53, 2022 = 52, 2023 = 56. Several have a near-identical local explanation with spelling/body corrections; others have only a group-level explanation or a genuinely blank choice explanation.
- Source/choice order cannot be inferred from a legacy document ID alone. Some years reordered choices, and 2020 reused `gizyutsu` IDs for law questions. Matching must use the target question/choice/answer and must fail closed on ambiguity.

## Safe publication path

The normal uploader accepts a full `questions[]` artifact, preserves existing `createdAt`/`createdById`, writes only documents with differences, supports an expected live fingerprint, and uses last-update preconditions. A repair artifact can therefore be materialized from selected live fields, preserving each existing document ID and changing only `explanationText` (plus uploader-managed update audit fields).

Required sequence:

1. Keep per-document explanation decisions in a durable local repair source with source provenance.
2. Materialize a full uploader-compatible artifact from the current live document projection, excluding the malformed legacy `questionSetRef` field.
3. Validate target count, unique IDs, nonblank explanations, answer-prefix consistency, source provenance, and unchanged non-explanation fields.
4. Run `scripts/upload/upload_questions_to_firestore.py <artifact> --dry-run`.
5. Compute and fix `QUESTION_PUBLISH_EXPECTED_LIVE_HASH` from the approved live snapshot.
6. Upload the exact approved artifact and read back every target plus the global missing-count oracle.

## Safety state

- No active gas-shunin review worker or qualification lock was found. Only the GoalBuddy local board process is active.
- `00_source` remains unchanged.
- Worktree changes are limited to the new goal directory.
- Branch is `main`, 56 commits ahead of `origin/main`, 0 behind. Push scope must be inspected before the final push.
- The user prohibited subagents; all remaining Scout/Judge/Worker-shaped work will be performed by the PM thread.

