# Source Canonicalization Plan

## Current Local Finding

- Target: `output/kougai/questions_json/2024/00_source/`
- Current yaku-tik files: 6
- Current yaku-tik question count: 135
- Current yaku-tik per-file counts: `25, 25, 25, 25, 25, 10`
- Current non-yaku-tik files in the same `00_source`: 3 zoron files
- Current zoron question count: 75

## Canonical Target

`2024/00_source` should contain only:

- `question_2024_yakutik_1.json`
- `question_2024_yakutik_2.json`
- `question_2024_yakutik_3.json`
- `question_2024_yakutik_4.json`
- `question_2024_yakutik_5.json`
- `question_2024_yakutik_6.json`

The canonical count must be exactly 135 questions.

## Retention Rule

Do not delete the current multi-source evidence. Before canonicalization, preserve the mixed-state files in a reversible location such as:

- `output/kougai/questions_json/2024/00_source_raw/`
- `output/kougai/source_audit/2024/`

The Worker should record the exact move/copy action in a receipt before modifying `00_source`.

## Quality Gate Before 01

Do not start 01 prompt work until all of these are true:

- `00_source` has 6 json files.
- Every file name starts with `question_2024_yakutik_`.
- The summed `question_bodies` count is 135.
- No file matching `*zoron*`, `*qualification*`, or `*qtext*` remains in canonical `00_source`.
- The raw evidence retention path exists.
