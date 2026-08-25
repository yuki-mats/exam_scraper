# T005 approved preflight v2 contract

## Judge decision

`approve_next`。画像7点の一時取得・承認hash照合を認める。旧法2問の差替えは、広告問の重複を避けて次の固定集合v2とする。

- remove: `dfb3fe84e07f47f9`、`1ebaca9b85c6dd6e`
- add: `4ef67113801362d9`（2020年、骨折・脱臼施術における医師同意）
- add: `ef0992b6887ec00b`（2020年、施術所構造設備基準）
- reject: `d732ddbaf0d4f522`（2025年広告問と論点・選択肢が強く重複）

法令5問は `c5167b46942fb08e`、`0eb595c2c11278f5`、`8987ec55216cbc63`、`4ef67113801362d9`、`ef0992b6887ec00b` とする。品質閾値は変更しない。

## Mandatory gates

- 2020、2024、2025年の公式試験日を、公式URL、文書名、exact locator、raw SHA-256付きで固定する。
- 各法令問について、試験時点と2026-08-25時点の一次資料を別々に解決し、revision、条・項・号又は通知位置、raw hashを記録する。
- 2025年広告問は、法第24条に加えて厚生労働大臣指定事項の告示本文を両時点で固定できなければblockedとする。
- 画像7点はrun-manifestで固定したFirebase Storage URLだけを一時取得し、host、HTTP、MIME、bytes、decoder、承認SHA-256を全て照合する。
- repositoryには画像bytes、base64、data URLを保存しない。
- preflight成果には正答番号、correctChoiceText、answer table、既存review/model結果を含めない。
- T006ではLLM、Codex App Server、Ollamaを呼ばない。

T006が `preflight.status=pass`、`failedChecks=[]`、`modelCalls=0` で閉じた場合だけ、T003実モデル評価を再開できる。
