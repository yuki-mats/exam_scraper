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

- AWSサービス名、機能名、コンポーネント名、略称、AWSが用いる日本語表記は、技術的に正しい限り維持する。一般名詞への言い換えや、別サービスへの置換で差を作らない。
- セキュリティ、レジリエンス、パフォーマンス、コストのうち、元の問題が指定した要件と優先順位を維持する。独自化のために別の非機能要件や制約を追加し、正答の判断軸を変えない。
- 現在構成、障害条件、データ特性、アクセス経路、運用上の制約など、候補を絞るために必要な情報を落とさない。一方、元の問題にない企業背景や担当者の物語を加えて長文化しない。
- 図中の名称、接続関係又は構成要素の対応を問う問題は、独自に作り直した図を使う図表問題のまま維持する。図表問題を一般的なアーキテクチャ選択へ変換しない。
- 選択肢は、AWS試験で比較対象となる構成と誤答のもっともらしさを原則維持する。独自化した問題文との整合又は事実訂正が必要な場合だけ、必要最小限を変更する。

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
