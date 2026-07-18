# Ping-t AWS CLF-C02 547問取得監査

- 実施日: 2026-07-18
- 取得元: `https://mondai.ping-t.com/question_subjects/76`
- 資格コード: `aws-cloud-practitioner`
- 出力group: `ping-t-aws-clf-c02`
- Firestore反映: 未実施

## 保存先

- 問題: `output/aws-cloud-practitioner/questions_json/ping-t-aws-clf-c02/00_source/`
- 画像: `output/aws-cloud-practitioner/question_images/ping-t-aws-clf-c02/`
- generated report: `output/aws-cloud-practitioner/reports/pingt_subject_76_scrape_result.json`

## 件数と同一性

| 検査 | 結果 |
| --- | ---: |
| 一覧表示件数 | 547 |
| 一覧ページ数 | 22 |
| 一覧の一意な問題ID | 547 |
| browser exportの詳細record | 547 |
| `00_source`ファイル | 547 |
| 一意な`source_question_id` | 547 |
| 一意な`public_question_id` | 547 |
| 一意な問題URL | 547 |
| 画像ファイル | 189 |
| 空画像 | 0 |
| `examYear` field | 0 |
| 全件検証error | 0 |

一覧ID、browser export ID、保存済み問題URL由来IDの3集合は一致した。ID集合のSHA-256は`43d321fc69644d64a0ae68a18dd4fcdb0617eab4629a6266561f82389a946be4`である。

## 再実行

全547問を指定して再実行し、`new=0`、`missing=0`、`unexpected=0`、重複`source_question_id=0`、重複URL`=0`を確認した。再実行前後の全`00_source`集約SHA-256はいずれも`e9371f2ca976f6c5d3879b1a69dab6a3b496ec589d5f9d34c0044d60193c6600`で、既存sourceは変化していない。

## 全件検査

全問題で次を検査した。

- 問題文、2件以上の選択肢、1件以上の正答、解説がある。
- 正答番号が選択肢数の範囲内にあり、`correctChoiceText`と選択肢数が一致する。
- 問題ID、カテゴリ、一覧文と詳細文の対応が一致する。
- 問題・選択肢・解説のsource画像数とstorage URL数が一致し、参照画像189件がすべて保存されている。
- Cookie、credential、password、session、tokenなどの認証情報fieldを保存していない。

一覧末尾が`...`で省略される問題は、詳細文が省略前の文字列から始まることを確認して詳細全文を保存した。詳細だけに`(Nつ選択)`が付く問題は、この定型suffixだけを許容した。また、「次の問題」がID`96963`と`97069`を飛ばしたため、一覧ID集合との差分で検知し、個別URLから補完した。

## カテゴリ内訳

generated reportに保存された51カテゴリの合計は547問である。

| カテゴリ | 問題数 |
| --- | ---: |
| AWSの利点 | 16 |
| AWSの概要 | 14 |
| AWSサポート | 13 |
| Artifact | 4 |
| Backup | 3 |
| Batch | 3 |
| Cloud9/CodeCommit/CodeBuild/CodeDeploy/CodePipeline | 6 |
| CloudFormation | 4 |
| CloudFront/Global Accelerator | 5 |
| CloudTrail | 3 |
| CloudWatch | 3 |
| Comprehend/Translate/Kendra/Transcribe/Polly/Lex/Rekognition/Textract | 11 |
| Config/Control Tower | 5 |
| Cost Explorer/Budgets | 6 |
| DataSync/Snowball | 4 |
| Direct Connect/VPN | 4 |
| DynamoDB/ElastiCache/MemoryDB | 4 |
| EBS | 4 |
| EC2 | 37 |
| ECS/Fargate/EKS | 11 |
| EFS | 11 |
| ELB/Auto Scaling | 21 |
| Elastic Beanstalk/Lightsail | 3 |
| FSx | 4 |
| Glue/Athena/QuickSight | 3 |
| IAM/Organizations | 30 |
| KMS/CloudHSM | 3 |
| Kinesis/Data Firehose | 3 |
| Lambda/API Gateway | 5 |
| Macie/GuardDuty/Inspector/Security Hub | 5 |
| Migration Hub/ADS/MGN/DMS | 7 |
| Outposts/Wavelength | 4 |
| RDS/Aurora | 16 |
| Redshift/Neptune | 4 |
| Route 53 | 8 |
| S3 | 26 |
| SNS/SQS/EventBridge/Step Functions | 5 |
| STS/IAM Identity Center/Cognito | 4 |
| Secrets Manager/ACM | 3 |
| Storage Gateway | 4 |
| Systems Manager/Managed Service/Health Dashboard | 4 |
| Trusted Advisor/Compute Optimizer | 6 |
| VPC | 24 |
| WAF/Shield | 5 |
| Well-Architected Framework | 26 |
| WorkSpaces/AppStream | 4 |
| X-Ray/Amplify | 2 |
| オンプレミスとクラウド | 11 |
| クラウド導入フレームワーク | 22 |
| 複合問題 | 86 |
| 責任共有モデル | 28 |
