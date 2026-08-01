# 二級建築士 補助ドキュメント

このディレクトリには、`2nd-class-kenchikushi`の法令根拠と必要最小限の解説調整を置く。

## 使い分け

- [01_law_reference_manual_review.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-kenchikushi/01_law_reference_manual_review.md)
  - 二級建築士の法規問題について、`lawReferences` と `lawId` を一問ずつ目視監査する手順。
- [02_law_reference_scope.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-kenchikushi/02_law_reference_scope.md)
  - 二級建築士の法規問題で通常参照する対象法令、短縮表記、`lawId`、スコープ外法令を追加する条件。

## 解説の資格固有調整

- `03_prompt_add_explanationText.md`を共通の正本とする。
- 二級建築士の法規問題では、`法` / `令` / `規則` の短縮表記が原則として建築基準法系を指す。ただし、設問文脈が建築士法、長期優良住宅法、宅地造成及び特定盛土等規制法、バリアフリー法などを指す場合は、その文脈を優先する。
- `verificationStatus="verified"` は、正式な `lawId` と条番号を確認できた場合だけ使う。
- 法令問題では、まず `02_law_reference_scope.md` の範囲から確認する。全法令から無差別に探さない。
