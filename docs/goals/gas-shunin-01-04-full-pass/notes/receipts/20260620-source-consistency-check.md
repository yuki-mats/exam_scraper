# Gas Shunin Source Consistency Check

Generated: 2026-06-20

## Scope

- Checked current production `00_source` files for `gas-shunin-kou` and `gas-shunin-otsu`.
- Compared Firestore-derived `00_source` records against the latest local Firestore snapshot.
- Compared gassyunin.com-derived production `00_source` records against archived full site scrape records where archived records exist.
- Compared Firestore-derived records and gassyunin.com records by normalized natural statement key.

## Commands

```bash
.venv/bin/python scripts/check/check_gas_shunin_source_consistency.py --report output/gas-shunin-source-consistency-final.json --max-samples 1000
.venv/bin/python scripts/check/check_gas_shunin_00_source_contract.py --report output/gas-shunin-00-source-contract-final.json --max-issues 200
```

## Result

- `00_source` contract check passed: 49 source files, 934 questions, 4638 sourceUniqueKeys, duplicate sourceUniqueKeys 0, issueCount 0.
- Firestore snapshot to production Firestore `00_source` check passed: referenced Firestore IDs 1449, unreferenced snapshot docs 0, issueCount 0.
- Production site `00_source` to archived site records had no normalized mismatches for comparable statements.
- Cross-source comparison found discrepancies between Firestore-derived records and gassyunin.com records for the same normalized natural keys.

## Counts

- productionQuestionCount: 934
- productionStatementCount: 4638
- productionFirestoreStatementCount: 1449
- productionSiteStatementCount: 3189
- archiveSiteQuestionCount: 348
- archiveSiteStatementCount: 1405
- issueCount: 450
- warningCount: 417

## Issues

- production Firestore vs archived site normalized mismatches: 441
  - choiceText mismatches: 263
  - correctChoiceText mismatches: 72
  - originalQuestionBodyText/questionBodyText mismatches: 106
- unexpected canonical statement duplicates: 6 statement records / 3 duplicate groups
  - `gas-shunin:kou:2023:law:q02:s01`
  - `gas-shunin:kou:2023:law:q02:s02`
  - `gas-shunin:kou:2023:law:q02:s03`
- statement source status issues: 3
  - `output/gas-shunin-kou/questions_json/2023/00_source/question_2023_gassyunin_site_1.json`, sourceIndex 2, statements 1-3
  - `firestoreRegisteredStatementNumbers` says 1-3 are Firestore registered, but `statementSourceStatuses` marks them as siteOnly.

## Warnings

- Non-canonical stored subject statements: 399
  - Firestore-derived law records store `hourei` in `sourceQuestionKey/sourceUniqueKeys`; canonical comparison normalizes this to `law`.
- Known legacy canonical duplicate statement records: 18
  - These are handled as legacy source-key conflicts and are not counted as unexpected duplicates.

## Report

- Full report: `output/gas-shunin-source-consistency-final.json`
- Contract report: `output/gas-shunin-00-source-contract-final.json`

## Interpretation

- Firestore extraction itself is not corrupted in `00_source`.
- Adopted site source records are internally consistent with archived site records where comparison is possible.
- The remaining blocker is cross-source disagreement and mixed Firestore/site metadata, especially 2023 kou law q02. Do not treat the current cross-source state as fully reconciled for upload until these issues are resolved or explicitly accepted by policy.
