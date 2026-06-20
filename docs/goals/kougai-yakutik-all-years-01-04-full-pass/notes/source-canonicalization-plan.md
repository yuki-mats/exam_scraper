# Source Canonicalization Plan

## Current Local Finding

- Target years: 2010-2025
- Current yaku-tik files: 96
- Current yaku-tik question count: 2,160
- Current yaku-tik per-year count: 6 files / 135 questions
- Current non-yaku-tik files in `00_source`: 54
- Current non-yaku-tik question count: 1,350
- Current qualification-text files/questions: 36 files / 900 questions
- Current zoron files/questions: 18 files / 450 questions

## Current Source Mix

| Year | yaku-tik files | qualification files | zoron files |
| --- | ---: | ---: | ---: |
| 2010 | 6 | 0 | 0 |
| 2011 | 6 | 3 | 0 |
| 2012 | 6 | 3 | 0 |
| 2013 | 6 | 3 | 0 |
| 2014 | 6 | 3 | 0 |
| 2015 | 6 | 3 | 0 |
| 2016 | 6 | 3 | 0 |
| 2017 | 6 | 3 | 0 |
| 2018 | 6 | 3 | 0 |
| 2019 | 6 | 3 | 3 |
| 2020 | 6 | 3 | 3 |
| 2021 | 6 | 3 | 3 |
| 2022 | 6 | 3 | 3 |
| 2023 | 6 | 0 | 3 |
| 2024 | 6 | 0 | 3 |
| 2025 | 6 | 0 | 0 |

## Canonical Target

For every year from 2010 through 2025, `<year>/00_source` should contain only:

- `question_<year>_yakutik_1.json`
- `question_<year>_yakutik_2.json`
- `question_<year>_yakutik_3.json`
- `question_<year>_yakutik_4.json`
- `question_<year>_yakutik_5.json`
- `question_<year>_yakutik_6.json`

The canonical count must be exactly 135 questions per year and 2,160 questions overall.

## Retention Rule

Do not delete the current multi-source evidence. Before canonicalization, preserve the mixed-state files in a reversible location such as:

- `output/kougai/questions_json/<year>/00_source_raw/`
- `output/kougai/source_audit/<year>/`

The Worker should record the exact move/copy action in a receipt before modifying any `00_source`.

## Quality Gate Before 01

Do not start 01 prompt work until all of these are true:

- Each target year has exactly 6 json files in canonical `00_source`.
- Every canonical source file name starts with `question_<year>_yakutik_`.
- The summed `question_bodies` count is 135 per year and 2,160 overall.
- No file matching `*zoron*`, `*qualification*`, or `*qtext*` remains in any canonical `00_source`.
- The raw evidence retention path exists for every affected year.
