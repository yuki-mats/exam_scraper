# 二級建築士 補助ドキュメント

このディレクトリは、`2nd-class-kenchikushi` の `explanationText` / `suggestedQuestions` / `lawReferences` を作る際の補助資料である。

## 使い分け

- [01_law_reference_manual_review.md](/Users/yuki/development/exam_scraper/prompt/qualification_docs/2nd-class-kenchikushi/01_law_reference_manual_review.md)
  - 二級建築士の法規問題について、`lawReferences` と `lawId` を一問ずつ目視監査する手順。

## 前提

- `03_prompt_add_explanationText.md` を正本とし、このディレクトリは資格固有の補助資料として読む。
- 二級建築士の法規問題では、`法` / `令` / `規則` の短縮表記が原則として建築基準法系を指す。ただし、設問文脈が建築士法、長期優良住宅法、宅地造成及び特定盛土等規制法、バリアフリー法などを指す場合は、その文脈を優先する。
- `verificationStatus="verified"` は、正式な `lawId` と条番号を確認できた場合だけ使う。
