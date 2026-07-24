# AWS Certified Cloud Practitioner (CLF-C02) 試験概要

## 正本

- [AWS Certified Cloud Practitioner (CLF-C02) 公式試験ガイド](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02.html)
- [分野1：クラウドのコンセプト](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02-domain1.html)
- [分野2：セキュリティとコンプライアンス](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02-domain2.html)
- [分野3：クラウドテクノロジーとサービス](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02-domain3.html)
- [分野4：請求、料金、サポート](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02-domain4.html)
- [対象のAWSサービス](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/cloud-practitioner-02/clf-02-in-scope-services.html)

確認日: 2026-07-18

## 試験の位置付け

CLF-C02は、特定の職務に限定せず、AWSクラウドに関する総合的な理解を確認するFoundationalレベルの試験である。受験対象者は、AWSクラウドの設計、実装、運用に触れた経験がおおむね6か月以下の初学者を想定している。

試験では、コーディング、クラウドアーキテクチャの設計、トラブルシューティング、実装、負荷・性能テストを実行できることまでは求めない。問題と解説は、サービスの詳細設定よりも、クラウドの価値、責任の境界、主要サービスの用途、料金・サポートの選び分けを中心に扱う。

## 独自問題化で維持するAWSらしさ

元の出題意図と、英語から翻訳されたAWS公式試験の日本語の語感を保ち、問題文、選択肢又は選択肢の順番のうち必要な部分だけを変える。AWSサービス名、カタカナの技術用語、判断条件、直接問題又は図表問題という出題形式を、一般的な日本語へ直したり別のシナリオへ作り替えたりしない。

独自問題化の前に[公式日本語サンプル問題と独自問題化サンプル](../aws_official_japanese_sample_questions.md)を読み、公式問題の文体と、後半に示した変更前・変更後の差分を参照する。サンプルの誤記や脱字は模倣しない。

## 問題形式

- 択一選択問題
- 複数選択問題
- 採点対象50問、採点対象外15問
- 合格スコアは1,000点中700点

採点対象外の問題は試験中に識別できない。アプリ上では、採点対象かどうかを推測して分類を変えない。

## 公式コンテンツ分野

| 分野 | 比率 | 公式タスク |
| --- | ---: | --- |
| 分野1：クラウドのコンセプト | 24% | 1.1 クラウドの利点、1.2 設計原則、1.3 移行、1.4 クラウドエコノミクス |
| 分野2：セキュリティとコンプライアンス | 30% | 2.1 責任共有、2.2 セキュリティ・ガバナンス・コンプライアンス、2.3 アクセス管理、2.4 セキュリティのコンポーネントとリソース |
| 分野3：クラウドテクノロジーとサービス | 34% | 3.1 デプロイと運用、3.2 グローバルインフラ、3.3 コンピューティング、3.4 データベース、3.5 ネットワーク、3.6 ストレージ、3.7 AI/ML・分析、3.8 その他のサービスカテゴリ |
| 分野4：請求、料金、サポート | 12% | 4.1 料金モデル、4.2 請求・予算・コスト管理、4.3 技術リソース・サポート |

## 分類での扱い

公式4分野は、出題比率と学習成果を集計する軸として保持する。アプリで選ぶ`folder`は、進捗や苦手分野を見つけやすくするため、公式分野の範囲内で10件に分割する。

公式の「対象サービス一覧」は網羅的ではなく、変更される可能性がある。`questionSet`はサービスごとに増設せず、サービスの主用途と比較判断を学ぶ単位にまとめる。公式タスクの追加・削除又は試験コードの変更があった場合は、表示名だけで吸収できるかを確認し、永続IDの変更はmigrationとして別に扱う。
