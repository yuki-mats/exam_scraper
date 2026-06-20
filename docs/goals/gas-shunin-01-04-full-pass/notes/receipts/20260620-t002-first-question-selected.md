# T002 first question selected

result: done

## Selection

- reviewId: `2019:question_2019_firestore_1:firestore:chiefgasengineerlicense-A-80-1567,chiefgasengineerlicense-A-80-1568,chiefgasengineerlicense-A-80-1569,chiefgasengineerlicense-A-80-1570,chiefgasengineerlicense-A-80-1571`
- reviewQuestionId: `firestore:chiefgasengineerlicense-A-80-1567,chiefgasengineerlicense-A-80-1568,chiefgasengineerlicense-A-80-1569,chiefgasengineerlicense-A-80-1570,chiefgasengineerlicense-A-80-1571`
- qualification: `gas-shunin-kou`
- year: `2019`
- question: `問17`
- source: `output/gas-shunin-kou/questions_json/2019/00_source/question_2019_firestore_1.json`
- correctChoice patch: `output/gas-shunin-kou/questions_json/2019/15_correctChoiceText_fixed/question_2019_firestore_1_correctChoiceText_fixed.json`
- questionSet patch: `output/gas-shunin-kou/questions_json/2019/22_questionSetId_linked/question_2019_firestore_1_questionSetId_linked.json`

## Decision

- 01 questionType: `true_false` を維持。
- 02 questionIntent: 問題文が「誤っているものはいくつあるか」なので `select_incorrect`。
- 02 correctChoiceText: Firestore/source上の5選択肢すべて `間違い` を維持。
- 04 questionSetId: `chiefgasengineerlicense-A-80-193`（導管の維持管理（甲種））。
- 03 explanationText: 既存解説は全選択肢にあるが、suggested系未生成のため保留。
