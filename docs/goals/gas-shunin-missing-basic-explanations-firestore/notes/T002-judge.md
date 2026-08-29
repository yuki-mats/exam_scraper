# T002 Judge decision

## Decision

`approved` for the first package: 甲種2020の1件と乙種2020の143件。

The safe repair unit is the existing Firestore document ID, not the current scraper public ID. The implementation must create a durable per-document decision ledger and materialize an uploader-compatible full-document artifact from a selected-field live snapshot. Only `explanationText` may differ from the approved live snapshot; uploader-managed update audit fields are the only other permitted live changes.

## Source priority

For every target, choose exactly one of these sources and record it in the ledger:

1. `firestore_same_question`: same target question text and answer, with the candidate explanation semantically referring to the target statement.
2. `local_21_patch`: exact or clearly corrected spelling/format variant of the same body and choice in `21_explanationText_added`.
3. `authored_from_similar`: a target-specific explanation authored after reading the full statement, answer, and at least one same-topic explanation.

Candidate selection must be hierarchical and fail closed. Stale `originalQuestion*` fields cannot override a different current `questionText`. The four 2020 conflicts must be decided from the current question content and the matching modern source/topic, not by array order or candidate count.

## First-package acceptance

- Exact target IDs: 144, with 甲1 and 乙2020=143.
- Every decision has a nonblank explanation and provenance.
- `正しい`/`間違い` explanation opening agrees with the target answer; calculation/flash-card explanations state the result and method.
- Law explanations use existing verified law evidence when present. Any newly authored legal explanation requires the Lawzilla workflow and official primary-law verification.
- The generated upload artifact has the same 144 IDs, no duplicate IDs, and no non-explanation field drift from the selected-field live snapshot.
- `00_source` hash is unchanged.
- Uploader dry-run passes.

## Stop conditions

- A target cannot be matched to its statement and answer uniquely.
- Current law and exam-time law cannot be separated where needed.
- A required document field cannot be reconstructed without changing the existing document meaning.
- Materialization reports any non-explanation drift or target-set mismatch.

