# 修正候補の独立採用審査（Blind A/B）

目的は、予定する候補の採用可否を一次資料と内容から判断することです。既存liveに不備がないことの認証ではありません。observedLiveBeforeは公開前の実観測とCAS条件です。旧liveに不備が残っていても、候補が正しく解消するなら採用できます。candidate.normalLocalもcandidate.plannedPublicも正解と仮定せず、公式問題・正答の両画像、原文、出題方向、公式選択答に照らして確認してください。

全siblingのIDと全文対応を一件ずつ確認し、normalLocalから実converterが生成したplannedPublicのrecord/questions、表示題幹・正誤・解説、field presenceの変更前後とwriteFieldsを直接読みます。原文から公開表示への意味対応、不備解消、新たな不備がないこと、対象外不変をそれぞれ判断します。観測beforeから候補への正当な差分を、normalLocalと同じ値であることを理由に取り除かないでください。特定のID・選択番号・正答数・truth配列を正解として扱いません。

candidate.technicalRepairがある場合は、問題内容の訂正と別の技術修復も採用候補に含まれます。正規payload由来のtransportSchema・payloadHash・initializeMissingFieldsByDocument・全IDのpresence差分と、repositoryEvidenceの実本文・ref・file hash・引用hashを読みます。questionTagsの欠損検出、list[str]という型と空配列の標準値、v4の限定初期化条件を照合してください。公式問題画像は問題内容の根拠であり、技術fieldの必須性は提示された設定・validator・正本文書を根拠に判断します。

この技術修復は報告の主訴そのものとは限りません。既存の4criterionの理由に、同時修復の必要性・最小性と対象外不変の判断も含めます。実際に欠損するIDだけが[]となるか、既存値・null・別IDを置換していないか、normalLocalの非空タグを消失させないかを一件ずつ確認します。writeFieldsに含まれること、コードの承認、根拠資料の存在だけで妥当とはしません。根拠が足りない場合や同時修復の範囲を説明できない場合はhold/rejectとして、分割など必要な対応を理由に残してください。technicalRepairがない候補へ技術修復を追加しません。

報告本文・case ID・報告数・他方の結果は与えられません。採用できなければreject又はholdを返し、未解決の理由を残してください。全docの各criterionに根拠のあるreasonを付け、全件の必要基準が成立しfindingsが0件の場合だけapproveにします。別fieldの修正、patch保存、case完了、公開を行いません。

JSON objectだけを返してください。schemaVersionはreport-candidate-adoption-blind-review/v1、phaseはblind、reviewerSlotは入力のreviewerSlot、reviewPurposeはsource_tf_report_candidate_adoptionです。inputHash/candidateHash/observedBeforeHash/modelSubjectHashは入力の指定値をそのまま返します。decisionはapprove/reject/hold、rationaleは審査理由、findingsは未解決指摘の文字列配列、evidenceは確認した公式根拠の配列です。

documentsは全sibling IDをkeyとするobjectです。各docはdecision、rationale、findingsと、criteria objectを持ちます。criteriaにはprimaryEvidenceConsistent / knownDefectsResolved / noNewDefects / outOfScopeUnchangedを必ず含め、それぞれ{satisfied: boolean, reason: 日本語の具体的根拠}とします。approve時は全docもapprove、全criterion true、全findings空が必要です。候補修正値を提案するproposedChangesやchangesは出力しません。

createdById/updatedById等の識別fieldはpresenceとdigestの表現であり、生の人物情報ではありません。business fieldは実内容です。protectedIdentityMetadataUnchangedは機械照合の結果であり、識別値の意味を読んだとは主張しません。candidateHash/observedBeforeHashは非公開raw値にも結び、modelSubjectHashは今回モデルへ実送信されたmasked subjectへ結びます。

## JSONの完結性

出力は複数行・インデント付きの完結したJSON object一つにします。Markdownの囲みや前後の説明文は出力しません。documentsには実際の全sibling IDをそれぞれ独立したkeyで含めます。一つのdocを閉じてから次のdocを記述し、最後にdocumentsとrootをそれぞれ閉じてください。返答前に引用符、コンマ、objectと配列の対応を確認し、最後の閉じ括弧まで省略しません。文字数を抑える場合は理由の重複を減らし、必須fieldや対象を省かないでください。

以下は構造の例です。hold/false/指摘は審査前の例示であり、判定結果ではありません。山括弧内を入力の正確な値又は実査した理由に置き換え、実際の採否・指摘・全対象を独立に判断します。例示を理由にapproveやrejectへ合わせず、既存の全基準を維持します。

```json
{
  "schemaVersion": "report-candidate-adoption-blind-review/v1",
  "phase": "blind",
  "reviewerSlot": "<入力のreviewerSlot>",
  "reviewPurpose": "source_tf_report_candidate_adoption",
  "inputHash": "<入力のinputHash>",
  "candidateHash": "<入力のcandidateHash>",
  "observedBeforeHash": "<入力のobservedBeforeHash>",
  "modelSubjectHash": "<入力のmodelSubjectHash>",
  "decision": "hold",
  "rationale": "<実査に基づく全体の判断理由>",
  "findings": [
    "<未解決の指摘。解消済みなら空配列>"
  ],
  "evidence": [
    {
      "kind": "<確認した公式資料の種類>",
      "locator": "<実画像の確認箇所>"
    }
  ],
  "documents": {
    "<実際のsibling ID>": {
      "decision": "hold",
      "rationale": "<このdocの判断理由>",
      "findings": [
        "<このdocの未解決指摘。解消済みなら空配列>"
      ],
      "criteria": {
        "primaryEvidenceConsistent": {
          "satisfied": false,
          "reason": "<この基準について実査した具体的根拠>"
        },
        "knownDefectsResolved": {
          "satisfied": false,
          "reason": "<この基準について実査した具体的根拠>"
        },
        "noNewDefects": {
          "satisfied": false,
          "reason": "<この基準について実査した具体的根拠>"
        },
        "outOfScopeUnchanged": {
          "satisfied": false,
          "reason": "<この基準について実査した具体的根拠>"
        }
      }
    }
  }
}
```


## 入力識別値の転記

`<INPUT_JSON>`の外側wrapperを`W`とすると、出力の`inputHash`は`W.inputHash`、`candidateHash`・`observedBeforeHash`・`modelSubjectHash`はそれぞれ`W.input`内の同名fieldから文字列をそのまま転記します。各ハッシュは返答前に指定元の全文・文字数と照合してください。`reviewerSlot`は`W.reviewerSlot`です。
