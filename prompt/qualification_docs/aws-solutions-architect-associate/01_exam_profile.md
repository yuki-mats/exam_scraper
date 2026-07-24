# AWS Certified Solutions Architect - Associate (SAA-C03) 試験概要

## 正本

- [AWS Certified Solutions Architect - Associate (SAA-C03) 公式試験ガイド](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03.html)
- [分野1：セキュアなアーキテクチャの設計](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03-domain1.html)
- [分野2：レジリエントなアーキテクチャの設計](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03-domain2.html)
- [分野3：高パフォーマンスなアーキテクチャの設計](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03-domain3.html)
- [分野4：コストを最適化したアーキテクチャの設計](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03-domain4.html)
- [テクノロジーと概念](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/saa-technologies-concepts.html)
- [対象のAWSサービス](https://docs.aws.amazon.com/ja_jp/aws-certification/latest/solutions-architect-associate-03/saa-03-in-scope-services.html)

確認日: 2026-07-18

## 試験の位置付け

SAA-C03は、ソリューションアーキテクトの役割を担う受験者が、AWS Well-Architectedフレームワークに基づいてソリューションを設計できるかを確認するAssociateレベルの試験である。AWSサービスを使ったクラウドソリューション設計の実務経験が1年以上ある受験者を想定している。

試験では、サービスの単独機能を覚えているだけでなく、現在の要件と将来の需要を満たし、安全性、レジリエンス、パフォーマンス、コストのトレードオフを判断できることが求められる。問題と解説では、要件から候補を絞り、正解を選ぶ決定要因と不正解との違いを明確にする。

## 独自問題化で維持するAWSらしさ

元の出題意図と、英語から翻訳されたAWS公式試験の日本語の語感を保ち、問題文、選択肢又は選択肢の順番のうち必要な部分だけを変える。AWSサービス名、カタカナの技術用語、設計判断に必要な条件と優先順位、直接問題又は図表問題という出題形式を、一般的な日本語へ直したり別のシナリオへ作り替えたりしない。

独自問題化の前に[公式日本語サンプル問題と独自問題化サンプル](../aws_official_japanese_sample_questions.md)を読み、公式問題の文体と、後半に示した変更前・変更後の差分を参照する。サンプルの誤記や脱字は模倣しない。

## 問題形式

- 択一選択問題
- 複数選択問題
- 採点対象50問、採点対象外15問
- 合格スコアは1,000点中720点

採点対象外の問題は試験中に識別できない。アプリ上では、採点対象かどうかを推測して分類を変えない。

## 公式コンテンツ分野

| 分野 | 比率 | 公式タスク |
| --- | ---: | --- |
| 分野1：セキュアなアーキテクチャの設計 | 30% | 1.1 セキュアなアクセス、1.2 セキュアなワークロードとアプリケーション、1.3 データセキュリティ管理 |
| 分野2：レジリエントなアーキテクチャの設計 | 26% | 2.1 スケーラブルな疎結合、2.2 高可用性・耐障害性 |
| 分野3：高パフォーマンスなアーキテクチャの設計 | 24% | 3.1 ストレージ、3.2 コンピューティング、3.3 データベース、3.4 ネットワーク、3.5 データ取り込み・変換 |
| 分野4：コストを最適化したアーキテクチャの設計 | 20% | 4.1 ストレージ、4.2 コンピューティング、4.3 データベース、4.4 ネットワーク |

## 分類での扱い

公式4分野は、出題比率と学習成果を集計する軸として保持する。アプリで選ぶ`folder`は、進捗や苦手分野を見つけやすくするため、公式分野の範囲内で17件に分割する。

同じAWSサービスが複数の公式分野に登場するため、サービス名だけで分類しない。例えばS3の公開防止・暗号化は分野1、レプリケーションと復旧は分野2、リクエスト性能は分野3、ストレージクラスとライフサイクルの費用は分野4として扱う。

公式の「対象サービス一覧」は網羅的ではなく、変更される可能性がある。サービスの追加・名称変更だけで永続IDを変えず、公式タスク又は学習上の判断軸が変わった場合に分類の見直しを行う。
