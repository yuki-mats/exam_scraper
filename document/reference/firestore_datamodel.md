# データモデル

=========================================
【重要】Firestore スキーマ設計思想（Architecture Decision Record）
=========================================
本アプリの「ユーザーごとの学習データ・記憶状況（UserStats、FSRS パラメータなど）」は、原則として `users/{userId}/...` 配下ではなく、対象となるエンティティ（`folders` や `questionSets`）配下のサブコレクションとして持たせています。
例: `/folders/{folderId}/folderSetUserStats/{userId}`

この設計は以下の3つの理由（ベストプラクティス）に基づくものです。今後の改修・AI 開発においてもこの思想を維持してください：

1. セキュリティルールと権限管理の直感性
   対象エンティティ（フォルダ等）に対するアクセス権を定義すれば、その配下にあるユーザーの学習データも自然に保護されます。`users` 配下に置くと「他人の学習状況を読む」権限管理が非常に複雑になります。
2. 指導者/管理者ダッシュボードのクエリ効率化
   「このフォルダを利用している全ユーザーの学習進捗を取得する」というユースケースにおいて、現在の設計であれば1つのサブコレクションに対するシンプルな `get()` で一括取得できます（Collection Group Query や全ユーザーのフルスキャンが不要）。
3. データライフサイクルのカプセル化
   「フォルダ」という一つの塊の中に、指導内容と生徒の進捗が凝集されています。フォルダを削除・エクスポートする際、関連データが全てその階層下にあるため、不整合が起きないクリーンな設計となります。

補足: questionType 設計方針（MECE）
- questionType は回答体験の分類。下記のカテゴリは互いに排他的。
- 設問文だけでは解けず、全選択肢を並べて比較しないと答えが出ない過去問がある。
  true_false（各肢の正誤判定）や flash_card（問題文だけで回答可能）では表現できず、
  単体出題だと回答不能になるため、グループ出題専用の型（group_choice）を用意する。
- true_false: 設問文に対して正誤を答える。選択肢は "正しい"/"間違い" の2択。
- single_choice: 複数選択肢から1つを選ぶ。（ユーザー作成問題）
- fill_in_blank: 本文の空欄を埋める（空欄定義は fillInBlanks）。
- flash_card: 問題文だけでも解答可能な想起型。デフォルトでは選択肢を表示し、設定で非表示モードに切替可能。
- group_choice: 同一 originalQuestionId の選択肢群を並べ、比較して1つだけ選ぶグループ出題専用。
  単体出題は不可で、共通設問文 + 各選択肢本文を同時提示することを前提とする。
  選択肢非表示モード時でも、この型は常に選択肢を表示する。
  統計対象の本体1件 + 表示用の選択肢（isChoiceOnly=true）で構成する運用とする。
=========================================

/users/{userId}
  ├─ name: string
  ├─ profileImageUrl: string
  ├─ tags: array<string>
  ├─ selectedLicenseNames: array<string>
  ├─ createdAt: timestamp
  ├─ updatedAt: timestamp
  ├─ totalLikesReceived　: number 
  ├─ availableLikes　: number 
  │  
  ├─ settings: map
  │    ├─ examDate: timestamp
  │    ├─ answerUi: map                  # 回答画面の表示設定
  │    │    ├─ hideChoicesInSingleQuestionMode: boolean  # true で選択肢非表示モード（flash_cardのみ適用）
  │    ├─ chartTargets: map             # 棒グラフの目標設定
  │    │    ├─ countMax: number         # 目標回答数（例: 30）
  │    │    └─ accuracyTarget: number   # 目標正答率（%）
  │    └─ learningNow: map              # 「今すぐ学習」リスト
  │         ├─ <itemId1>: map
  │         │    ├─ type: string        # "questionSet" or "studySet"
  │         │    ├─ refId: string       # 対象 ID
  │         │    ├─ folderId: string?   # questionSet の場合のフォルダ参照
  │         │    ├─ order: number       # 並び順（昇順で表示）
  │         │    ├─ createdAt: timestamp
  │         │    └─ updatedAt: timestamp
  │         └─ <itemId2>: { … }
  │  
  ├─ /dailyStudyStats/{dateKey}
  │    ├─ dateTimestamp : timestamp  # YYYY-MM-DD 0:00:00+JST
  │    ├─ answerCount   : number     # 回答数（累積）
  │    ├─ correctCount  : number     # 正答数（累積）
  │    ├─ studyTime     : number     # 学習時間 (秒)  optional
  │    ├─ updatedAt     : timestamp
  │ 
  ├─ /likesReceived/{likeId}  # もらった「いいね」のサブコレクション
  │    ├─ fromUserId: string  # いいねを送ったユーザー
  │    ├─ memoId: string (optional)  # いいねを付けたメモのID
  │    ├─ replyId: string (optional)  # いいねを付けた返信のID
  │    ├─ createdAt: timestamp  # いいねをもらった日時
  │    ├─ isActive: boolean  # いいねが有効か（削除された場合 false）
  ├─ /studySets/{studySetId}  # 学習セット
  │    ├─ name: string  # 学習セット名
  │    ├─ isDeleted: bool
  │    ├─ requiresPro: bool        # PRO 機能を 1 つでも使えば true
  │    ├─ correctRateRange
  │    │    ├─ start: number
  │    │    └─ end: number
  │    ├─ isFlagged: boolean
  │    ├─ numberOfQuestions: number
  │    ├─ questionSetIds: array<string>  # 選択された問題集のID
  │    ├─ selectedQuestionOrder: string  # 選択された出題順
  │    ├─ selectedMemoryLevels: array<string>  # 選択された記憶度
  │    │    ├─ "again"
  │    │    ├─ "hard"
  │    │    ├─ "good"
  │    │    ├─ "easy"
  │    ├─ memoryLevelStats: map<string, number>  # 学習結果での記憶度カウント
  │    │    ├─ again: number
  │    │    ├─ hard: number
  │    │    ├─ good: numbe
  │    │    └─ easy: number
  │    ├─ memoryLevelRatios: map<string, number>  # 学習結果での記憶度比率 (%)
  │    │    ├─ again: number
  │    │    ├─ hard: number
  │    │    ├─ good: number
  │    │    └─ easy: number
  │    ├─ totalAttemptCount: number  # 学習セットの総試行回数
  │    ├─ studyStreakCount: number  # 連続学習日数
  │    ├─ lastStudiedDate: string  # 最後の学習日 (YYYY-MM-DD)
  │    ├─ createdAt: timestamp  # 作成日時
  │    ├─ updatedAt: timestamp  # 更新日時
  │    ├─ /studySetDailyStats/{dateKey}  # 連続学習履歴のサブコレクション
  │    │    ├─ isStudied: boolean  # その日に学習したか (true: 学習済み, false: 未学習)
  │    │    ├─ studyDuration: number  # その日の学習時間 (秒)
  │    │    ├─ correctRate: number  # その日の正答率 (%)
  │    │    ├─ attemptCount: number  # その日の試行回数
  │    │    ├─ again: number  # "Again"のカウント
  │    │    ├─ hard: number  # "Hard"のカウント
  │    │    ├─ good: number  # "Good"のカウント
  │    │    ├─ easy: number  # "Easy"のカウント
  │    │    ├─ createdAt: timestamp  # 記録日時
  │    │    ├─ updatedAt: timestamp  # 更新日時

/userPrivateData/{userId}  # ユーザー非公開情報
  ├─ birthdate: string (optional)  # 生年月日（非公開情報）
  ├─ residence: string (optional)  # 居住地（非公開情報）
  ├─ createdAt: timestamp  # 作成日時
  ├─ updatedAt: timestamp  # 更新日時

/config/08zYvCuKUcvGTNYqehrm
  ├─ maintenance_enabled: bool
  ├─ maintenance_title: string
  ├─ maintenance_message: string
  ├─ ios_force_app_version: string
  │
  └─ officialDataUpdatedAt: map
       ├─ "ガス主任技術者": timestamp
       ├─ "管理業務主任者": timestamp
       ├─ "公認心理師": timestamp
       └─ "二級建築士": timestamp


/folders/{folderId} 
  ├─ name: string
  ├─ isDeleted: bool
  ├─ isPublic: bool
  ├─ isOfficial: bool
  ├─ aggregatedQuestionTags: array<string> (optional)
  ├─ licenseName: string
  ├─ questionCount: number
  ├─ createdById: string
  ├─ createdByRef: reference(/users/{userId})
  ├─ createdAt: timestamp
  ├─ updatedById: string
  ├─ updatedByRef: reference
  ├─ updatedAt: timestamp
  ├─ permissions/{userId}
  │    ├─  userRef: reference #削除予定
  │    ├─  userId: string
  │    ├─  role: string　# "owner" | "editor" | "viewer"
  │    ├─  isHidden: bool #削除予定
  │    ├─  isDeleted: boolean
  │    ├─  createdById: string
  │    ├─  updatedById: string
  │    ├─  createdAt: timestamp
  │    ├─  updatedAt: timestamp
  ├─ /folderSetUserStats/{userId}
  │    ├─ userId: string
  │    ├─ memoryLevels: map<string, string>
  │    │    ├─ {questionId1}: "easy"
  │    │    ├─ {questionId2}: "hard"
  │    │    ├─ {questionId3}: "again"
  │    │    └─ ...
  │    ├─ lastStudiedAt: timestamp 今後updatedAtに集約予定
  │    ├─ fsrsParameters: array<number>        # FSRS最適化済み19パラメータ
  │    ├─ fsrsLastOptimizedAt: timestamp       # 最終最適化日時
  │    ├─ fsrsOptimizationReviewCount: number  # 最適化に使用した回答数
  │    ├─ createdAt: timestamp
  │    └─ updatedAt: timestamp

/questionSets/{questionSetId}  # 問題集
  ├─ name: string
  ├─ isDeleted: boolean
  ├─ isOfficial: bool
  ├─ folderId: string
  ├─ questionCount: number 
  ├─ createdById: string
  ├─ createdAt: timestamp
  ├─ updatedById: string
  ├─ updatedAt: timestamp
  ├─ lastStudiedAt: timestamp
  ├─ /questionSetUserStats/{userId}
  │    ├─ userId: string
  │    ├─ memoryLevels: map<string, string>
  │    │    ├─ {questionId1}: "easy"
  │    │    ├─ {questionId2}: "hard"
  │    │    ├─ {questionId3}: "again"
  │    │    └─ ...
  │    ├─ lastStudiedAt: timestamp
  │    ├─ fsrsParameters: array<number>        # FSRS最適化済み19パラメータ
  │    ├─ fsrsLastOptimizedAt: timestamp       # 最終最適化日時
  │    ├─ createdAt: timestamp
  │    └─ updatedAt: timestamp

/questions/{questionId}
  ├─ questionSetId: string
  ├─ listGroupId: string (optional)
  ├─ originalQuestionId: string (optional) # isofficial が true の場合、選択式切替のため必須にする。
  ├─ originalQuestionBodyText: string (optional)  # isofficial が true の場合、選択式切替のため必須にする。
  ├─ originalQuestionChoiceText: string (optional)  # isofficial が true の場合、選択式切替のため必須にする。
  ├─ questionBodyText: string (optional)  # 正誤問題用に語尾を"下記の正誤を答えよ"としている。
  ├─ questionText: string
  ├─ questionType: string (enum) # 詳細は上記「Firestore スキーマ設計思想」の questionType 定義を参照
  ├─ questionImageUrls: array<string> (optional)
  ├─ originalQuestionChoiceText: string (optional)  
  ├─ originalQuestionChoiceImageUrls: array<string> (optional) 
  ├─ correctChoiceText: string (optional)
  ├─ correctChoiceImageUrls: array<string> (optional)
  ├─ incorrectChoice1Text: string (optional)
  ├─ incorrectChoice2Text: string (optional)
  ├─ incorrectChoice3Text: string (optional)
  ├─ incorrectChoice4Text: string (optional)
  ├─ knowledgeText: string (optional)
  ├─ explanationText: string (optional)
  ├─ explanationImageUrls: array<string> (optional)
  ├─ hintText: string (optional)
  ├─ hintImageUrls: array<string> (optional)
  ├─ examYear: number (optional)
  ├─ examSource: string (optional)
  ├─ questionTags: array<string> (optional)
  ├─ isOfficial: boolean
  ├─ isDeleted: boolean
  ├─ isChoiceOnly: boolean
  ├─ isGroupable: boolean
  ├─ importKey: string (optional) // ファイルから読み込んだ元の問題IDやキー
  ├─ fillInBlanks: array<object> (optional) // questionType が "fill_in_blank" の場合に利用
  │    ├─ blankIndex: number // プレースホルダの番号 // 各オブジェクトは1つの穴埋め箇所に対応
  │    ├─ correctChoiceText: string
  │    ├─ incorrectChoice1Text: string (optional)
  │    ├─ incorrectChoice2Text: string (optional)
  │    ├─ incorrectChoice3Text: string (optional)
  ├─ createdById: string
  ├─ updatedById: string
  ├─ createdAt: timestamp
  ├─ updatedAt: timestamp
  ├─ /questionUserStats/{userId}
  │    ├─ userId: string
  │    ├─ isFlagged: boolean
  │    ├─ memoryLevel
  │    ├─ attemptCount: number
  │    ├─ correctCount: number
  │    ├─ correctRate: number
  │    ├─ memoryLevelStats: map<string, number>  # 記憶度統計
  │    │    ├─ again: number
  │    │    ├─ hard: number
  │    │    ├─ good: number
  │    │    └─ easy: number
  │    ├─ memoryLevelRatios: map<string, number>  # 記憶度比率 (%)
  │    │    ├─ again: number
  │    │    ├─ hard: number
  │    │    ├─ good: number
  │    │    └─ easy: number
  │    ├─ lastStudiedAt: timestamp
  │    ├─ createdAt: timestamp
  │    └─ updatedAt: timestamp
  │    ├─ fsrs: map                      # FSRS カード状態
  │    │    ├─ due: timestamp            # 次回復習予定日時
  │    │    ├─ stability: number         # 安定性
  │    │    ├─ difficulty: number        # 難易度 (1-10)
  │    │    ├─ state: number             # 1=Learning, 2=Review, 3=Relearning
  │    │    ├─ step: number (optional)   # Learning/Relearning ステップ
  │    │    ├─ lastReview: timestamp     # 最終レビュー日時
  │    │    └─ updatedAt: timestamp
  │    ├─ /dailyStats/{dateKey}
  │    │    ├─ dateTimestamp: timestamp
  │    │    ├─ attemptCount: number
  │    │    ├─ correctCount: number
  │    │    ├─ incorrectCount: number
  │    │    ├─ memoryLevelStats: map<string, number>  # 記憶度統計
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ memoryLevelRatios: map<string, number>  # 記憶度比率 (%)
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ totalStudyTime: number
  │    │    ├─ createdAt: timestamp
  │    │    └─ updatedAt: timestamp
  │    ├─ /weeklyStats/{weekKey}
  │    │    ├─ weekStartTimestamp: timestamp
  │    │    ├─ weekEndTimestamp: timestamp
  │    │    ├─ attemptCount: number
  │    │    ├─ correctCount: number
  │    │    ├─ incorrectCount: number
  │    │    ├─ memoryLevelStats: map<string, number>
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ memoryLevelRatios: map<string, number>
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ totalStudyTime: number
  │    │    ├─ createdAt: timestamp
  │    │    └─ updatedAt: timestamp
  │    ├─ /monthlyStats/{monthKey}
  │    │    ├─ monthStartTimestamp: timestamp
  │    │    ├─ monthEndTimestamp: timestamp
  │    │    ├─ attemptCount: number
  │    │    ├─ correctCount: number
  │    │    ├─ incorrectCount: number
  │    │    ├─ memoryLevelStats: map<string, number>
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ memoryLevelRatios: map<string, number>
  │    │    │    ├─ again: number
  │    │    │    ├─ hard: number
  │    │    │    ├─ good: number
  │    │    │    └─ easy: number
  │    │    ├─ totalStudyTime: number
  │    │    ├─ createdAt: timestamp
  │    │    └─ updatedAt: timestamp

/answerHistories/{historyId}
  ├─ userId: string
  ├─ questionId: string
  ├─ startedAt: timestamp
  ├─ answeredAt: timestamp
  ├─ answerTime: number
  ├─ nextStartedAt: timestamp
  ├─ postAnswerTime: number
  ├─ isCorrect: boolean
  ├─ selectedChoice: string
  ├─ correctChoice: string
  ├─ memoryLevel: string
  ├─ fsrsRating: number                  # FSRS Rating (1=Again, 2=Hard, 3=Good, 4=Easy)
  ├─ createdAt: timestamp

/memos/{memoId}
  ├─ questionId: string
  ├─ visibility: string                # 'public' or 'private'
  ├─ isAIGenerated: boolean
  ├─ licenseName: string
  ├─ isDeleted: boolean
  ├─ title: string (optional)
  ├─ content: string
  ├─ contentFormat: string (optional)　 # 'plain_text', 'markdown'
  ├─ memoType: string (optional)
        # 'notice'(気づき), 'explanation'(解説), # 'knowledge' (知識・用語), 'question'(疑問)
  ├─ attachedImages: array<object> (optional)
  ├─ likeCount: number (optional)      # メモへのいいね数
  ├─ replyCount: number (optional)     # メモへの返信数
  ├─ isResolved: boolean (optional)
  ├─ createdById: string               # メモ投稿者
  ├─ createdAt: timestamp
  ├─ updatedById: string (optional)
  └─ updatedAt: timestamp
  
     └─ /likes/{userId}   # メモに対するいいね
         ├─ fromUserId: string
         ├─ toUserId: string
         ├─ isActive: boolean
         ├─ hasRewarded: boolean
         ├─ createdAt: timestamp
         └─ updatedAt: timestamp

     └─ /replies/{replyId}
         ├─ parentReplyId: string (nullable)
             # null or "" → メモへの直接の返信
             # 値あり → 「このreplyId」に対する返信
         ├─ isAIGenerated: boolean
             # AI 自動生成の返信なら true、ユーザー返信なら false,undefined → false
         ├─ content: string
         ├─ createdById: string          # 返信投稿者
         ├─ createdAt: timestamp
         ├─ updatedById: string (optional)
         ├─ updatedAt: timestamp
         ├─ isDeleted: boolean
         ├─ likeCount: number (optional) # 返信へのいいね数
         └─ /likes/{userId} # 返信に対するいいね
             ├─ fromUserId: string
             ├─ toUserId: string
             ├─ isActive: boolean
             ├─ hasRewarded: boolean
             ├─ createdAt: timestamp
             └─ updatedAt: timestamp
