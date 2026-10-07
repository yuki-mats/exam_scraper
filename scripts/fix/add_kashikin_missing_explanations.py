#!/usr/bin/env python3
"""
Adds the 10 missing explanations to 21_explanationText_added patches for kashikin.
Each explanation is written following:
- prompt/03_prompt_add_explanationText.md
- 文化庁「公用文作成の考え方」
- Exact choice length (4 items for 4 choices)
- Grounded in official law and exam answer
"""
import json
from pathlib import Path

ADDITIONS = {
    # 1. 93001 question_93001_2.json#6 (問32) 連帯債務
    ("93001", "question_93001_2_merged_explanationText_added.json", "question_93001_2.json#6"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "民法", "article": "436条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "437条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "440条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "439条1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "民法第436条、第437条、第439条第1項、第440条。連帯債務者の一人が相殺を援用したときは、債権はすべての連帯債務者の利益のために消滅する（絶対的効力）。",
        "explanationReferences": [
            {"title": "民法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "適切でない。数人が連帯債務を負担するときは、債権者は、その連帯債務者の一人に対し、又は同時に若しくは順次に全ての連帯債務者に対し、全部又は一部の履行を請求することができる（民法第436条）。したがって、すべての連帯債務者に対して同時に請求しなければならないとする記述は誤りである。",
            "適切でない。連帯債務者の一人について法律行為の無効又は取消しの原因があっても、他の連帯債務者の債務は、その効力を妨げられない（民法第437条）。したがって、他の連帯債務者の債務も無効となり又は取り消され得るとする記述は誤りである。",
            "適切でない。連帯債務者の一人と債権者との間に混同があったときは、その連帯債務者は、弁済をしたものとみなされる（民法第440条）。混同の効果は他の連帯債務者にも及ぶため、他の連帯債務者に対してその効力を生じないとする記述は誤りである。",
            "適切である。連帯債務者の一人が債権者に対して債権を有する場合において、その連帯債務者が相殺を援用したときは、債権は、すべての連帯債務者の利益のために消滅する（民法第439条第1項）。記述のとおり相殺は絶対的効力を生じるため正しい。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 2. 93004 question_93004_1.json#1 (問2) 登録拒否事由
    ("93004", "question_93004_1_merged_explanationText_added.json", "question_93004_1.json#1"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法", "article": "6条1項2号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項5号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項3号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項4号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第6条第1項各号（登録の拒否）。cのみが登録拒否事由に該当するため、該当する記述の個数は1個（選択肢1）である。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "正しい。a〜dの記述のうち、貸金業法第6条第1項の登録拒否事由に該当するものはcの1個であるため、適切な選択肢である。\na（該当しない）: 破産手続開始の決定を受けた者であっても、復権を得ていれば登録拒否事由には該当しない（貸金業法第6条第1項2号）。復権を得てから5年を経過していないことのみを理由に拒否されることはない。\nb（該当しない）: 禁錮以上の刑に処せられた場合でも、刑の執行猶予の言渡しを受けたときは、猶予期間が満了すれば刑の言渡し自体が効力を失うため（刑法第34条の2）、猶予期間満了から5年を経過していなくても登録拒否事由に該当しない。\nc（該当する）: 禁錮以上の刑（道路交通法違反による懲役刑を含む）に処せられ、その刑の執行を終わり、又は刑の執行を受けることがなくなった日から5年を経過しない者は、登録拒否事由に該当する（貸金業法第6条第1項4号）。\nd（該当しない）: 法人が監督処分により登録を取り消された場合、取消しの日前30日以内に役員であった者が欠格事由の対象となる（貸金業法第6条第1項3号）。本肢の者は取消しの日の50日前に退任しており、「取消しの日前30日以内」の役員に該当しないため、登録拒否事由に該当しない。",
            "誤り。登録拒否事由に該当するものはcの1個のみであるため、2個とする本肢は誤りである。",
            "誤り。登録拒否事由に該当するものはcの1個のみであるため、3個とする本肢は誤りである。",
            "誤り。登録拒否事由に該当するものはcの1個のみであるため、4個とする本肢は誤りである。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 3. 93004 question_93004_1.json#23 (問24) 帳簿の記載事項
    ("93004", "question_93004_1_merged_explanationText_added.json", "question_93004_1.json#23"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法", "article": "19条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "16条1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "16条1項14号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "16条1項6号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第19条、貸金業法施行規則第16条第1項第14号。帳簿に記載すべき交渉経過は「貸付けの契約の締結後における顧客との交渉の経過」であり、契約締結前の申込み検討段階の記録は含まれない。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"},
            {"title": "貸金業法施行規則（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "適切である。貸金業者は、内閣府令で定めるところにより、その営業所又は事務所ごとに、その業務に関する帳簿を備え付けなければならない（貸金業法第19条前段）。記述のとおり正しい。",
            "適切である。貸金業者は、電磁的記録に記録し、これを営業所等において電子計算機の映像面に直ちに表示することができるようにして保存する方法により、帳簿の備付けに代えることができる（貸金業法施行規則第16条第3項）。記述のとおり正しい。",
            "適切でない。帳簿に記載すべき交渉の経過は、「貸付けに係る契約の締結後において、債務者等と交渉したときは、その日時、相手方の氏名、交渉の内容その他交渉の経過」とされている（貸金業法施行規則第16条第1項第14号）。契約締結前の申込みを検討している段階における交渉経過を記載することまでは義務付けられていないため、本肢は誤りである。",
            "適切である。貸金業者が帳簿に記載すべき事項には、「貸付けに係る契約に基づく債権を他人に譲渡したときは、その者の商号、名称又は氏名及び住所、譲渡年月日並びに当該債権の額」が含まれている（貸金業法施行規則第16条第1項第6号）。記述のとおり正しい。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 4. 93004 question_93004_2.json#1 (問27) 保証料の制限
    ("93004", "question_93004_2_merged_explanationText_added.json", "question_93004_2.json#1"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "利息制限法", "article": "8条1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "利息制限法", "article": "8条2項2号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "利息制限法", "article": "8条7項1号イ", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "利息制限法", "article": "8条4項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "利息制限法第8条第2項第2号。特約上限利率が主たる債務者に通知されなかったときは、法定上限額（年18%相当＝9万円）の2分の1（4万5,000円）が保証料の上限となるため、1万5,000円とする記述は誤り。",
        "explanationReferences": [
            {"title": "利息制限法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "適切である。保証料の契約は、その保証料が主たる債務の法定上限額から支払うべき利息の額を減じて得た金額を超えるときは、その超過部分について無効となる（利息制限法第8条第1項）。記述のとおり正しい。",
            "適切でない。変動利率の営業的金銭消費貸借において特約上限利率が主たる債務者に通知されなかった場合、保証業者が受け取ることができる保証料の上限額は、主たる債務の元本に係る法定上限額の2分の1に相当する金額となる（利息制限法第8条第2項第2号）。元本50万円の法定上限利率は年18％であり、1年の法定上限額は9万円であるから、その2分の1である45,000円が上限となる。記述の15,000円は誤りである。",
            "適切である。保証契約に関し保証業者が受ける金銭のうち、契約締結費用であって公租公課の支払に充てられるべきものは、保証料とみなされない（利息制限法第8条第7項第1号イ）。記述のとおり正しい。",
            "適切である。保証料契約の締結後に利息を増額変更したことにより、利息と保証料の合算額が法定上限額を超えることとなる場合、当該変更後の利息の約定は、変更前の利息を超える部分について無効となる（利息制限法第8条第4項）。元本30万円の法定上限は年18％であり、保証料年4％（12,000円）があるため、利息の上限は年14％となる。したがって、年14％を超える部分に限り無効となるとする記述は正しい。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 5. 93005 question_93005_1.json#6 (問7) 年収証明書の要否
    ("93005", "question_93005_1_merged_explanationText_added.json", "question_93005_1.json#6"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法", "article": "13条3項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "13条3項1号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "10条の26第1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "13条の3第5項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第13条第3項、第13条の3第5項。住宅資金貸付契約に係る貸付けの残高は極度方式個人顧客合算額から除外されるため、極度額50万円＋他貸付30万円＝80万円となり、100万円を超えないため書面の提出は不要である。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"},
            {"title": "貸金業法施行規則（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "適切でない。貸金業者は、貸付けの金額が50万円を超える貸付けに係る契約を締結しようとする場合には、原則として年収証明書の提出又は提供を受けなければならない（貸金業法第13条第3項第1号）。過去1年以内に源泉徴収票の提出を受けていたとしても、新たな契約締結に際しては資力を確認する書面の提出等を改めて受ける必要があるため、受ける必要はないとする本肢は誤りである。",
            "適切でない。個人顧客に対する自社の貸付けの金額が50万円を超えるか、又は他の貸金業者の貸付残高と合算して100万円を超える場合に年収証明書の提出が必要となる（貸金業法第13条第3項）。本肢では貸付けの金額が50万円であり50万円を超えておらず、また保証を行っている残高は「貸付けの金額」に算入されない。したがって年収証明書の提出等を受ける必要はなく、受けなければならないとする本肢は誤りである。",
            "適切でない。極度方式基本契約の定期調査において、極度方式個人顧客合算額が100万円を超える場合であっても、過去3年以内に資力を明らかにする書面の提出等を受け、その後資力に変更がないことを確認したときは、書面の提出等を改めて受ける必要はない（貸金業法施行規則第10条の26第1項第2号）。本肢は1年前に受けて資力に変更がないことを確認しているため受ける必要はなく、受けなければならないとする本肢は誤りである。",
            "適切である。基準額超過極度方式基本契約に該当するかどうかの調査において、極度方式個人顧客合算額が100万円を超えるときは年収証明書等の提出等を受ける必要がある（貸金業法第13条の3第3項）。しかし、住宅資金貸付契約に係る貸付けの残高は極度方式個人顧客合算額の計算から除外される（同条第5項第2号）。したがって、本肢の合算額は極度額50万円と他の貸付残高30万円を合わせた80万円となり、100万円を超えないため、年収証明書等の提出等を受ける必要はない。記述のとおり正しい。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 6. 93005 question_93005_2.json#15 (問41) 相続
    ("93005", "question_93005_2_merged_explanationText_added.json", "question_93005_2.json#15"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "民法", "article": "889条2項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "909条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "923条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "民法", "article": "887条2項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "民法第923条。相続人が数人あるときは、限定承認は、共同相続人の全員が共同してのみこれをすることができる。Bが単独で行うことはできない。",
        "explanationReferences": [
            {"title": "民法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/129AC0000000089?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "適切である。兄弟姉妹の代襲相続は、被相続人の甥・姪（兄弟姉妹の子）までに限られ、甥・姪の子（孫）は代襲相続人とならない（民法第889条第2項、第887条第2項ただし書準用）。本肢のDは弟Cの孫であるため代襲相続人にならず、Aの相続人とならないとする記述は正しい。",
            "適切である。金銭債務は相続開始と同時に共同相続人にその法定相続分に応じて当然に分割承継される。共同相続人間の遺産分割協議により一人の相続人に債務を承継させても、債権者の承諾がない限り債権者に対抗できず、第三者の権利を害することはできない（民法第909条ただし書）。したがって、債権者DはB及びCに対してそれぞれの法定相続分の割合に応じた弁済を請求できるため正しい。",
            "適切でない。相続人が数人あるときは、限定承認は、共同相続人の全員が共同してのみこれをすることができる（民法第923条）。BとCが共同相続人である場合、BがCの同意を得ることなく単独で限定承認をすることはできないため、本肢は誤りである。",
            "適切である。配偶者Bの法定相続分は2分の1であり、直系卑属全体の相続分は2分の1である（民法第900条第1号）。子Eが先に死亡しているため、Eの子である孫CとDが代襲相続人としてEの相続分（2分の1）を均等に分ける（民法第887条第2項、第901条第1項）。したがって、Cの相続分は4分の1（1/2 × 1/2）となり記述のとおり正しい。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 7. 93006 question_93006_1.json#1 (問2) 登録等の手続
    ("93006", "question_93006_1_merged_explanationText_added.json", "question_93006_1.json#1"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法施行令", "article": "1条の2", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358CO0000000040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "2条", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "4条1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行令", "article": "1条の5第3項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358CO0000000040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第4条第1項、施行令第1条の2、施行規則第2条。適切な記述はaとcの2個であるため、正答は「2個」（選択肢2）である。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"},
            {"title": "貸金業法施行令（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358CO0000000040?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "誤り。a〜dの記述のうち、適切な記述はaとcの2個であるため、1個とする本肢は誤りである。",
            "正しい。a〜dの記述のうち、適切な記述はaとcの2個であるため、適切な選択肢である。\na（適切）: 政令で定める使用人とは、貸金業に関し営業所又は事務所の業務を統括する者その他これに準ずる者をいう（貸金業法施行令第1条の2）。記述のとおり正しい。\nb（不適切）: 貸金業の登録の更新を受けようとするときは、現に受けている登録の有効期間満了の日の2月前までに申請書を提出しなければならない（貸金業法施行規則第2条第2項）。「有効期間満了の日までに」とする記述は誤りである。\nc（適切）: 登録申請書の記載事項には、商号・名称、役員の氏名、営業所等の名称・所在地のほか、「営業所又は事務所ごとに置かれる貸金業務取扱主任者の氏名及び登録番号」が含まれる（貸金業法第4条第1項第7号）。記述のとおり正しい。\nd（不適切）: 営業所等と同一敷地内（隣接地を含む）に設置される「現金自動設備」は営業所等から除外されるが、「自動契約受付機」は除外されず営業所等に該当する（貸金業法施行規則第1条の5第3項）。いずれも該当しないとする記述は誤りである。",
            "誤り。適切な記述はaとcの2個であるため、3個とする本肢は誤りである。",
            "誤り。適切な記述はaとcの2個であるため、4個とする本肢は誤りである。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 8. 93007 question_93007_1.json#14 (問15) 登録拒否事由（非該当を選ぶ）
    ("93007", "question_93007_1_merged_explanationText_added.json", "question_93007_1.json#14"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法", "article": "6条1項1号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項2号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項5号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "6条1項3号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第6条第1項各号。選択肢2の復権を得た者は第2号の登録拒否事由に該当しない（復権していれば直ちに欠格事由から外れる）。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "該当する（正答ではない）。精神の機能の障害により貸金業に係る職務を適正に執行するに当たって必要な認知、判断及び意思疎通を適切に行うことができない者は、貸金業法第6条第1項第1号の登録拒否事由に該当する。",
            "該当しない（正答）。破産手続開始の決定を受けて復権を得ていない者は登録拒否事由に該当するが（貸金業法第6条第1項第2号）、すでに復権を得ている者は拒否事由に該当しない。復権後に5年を経過する必要はないため、本肢の事由は登録拒否事由のいずれにも該当せず、本問の正答となる。",
            "該当する（正答ではない）。貸金業法の規定に違反して罰金の刑に処せられ、その刑の執行を終わり、又は刑の執行を受けることがなくなった日から5年を経過しない者は、役員だけでなく政令で定める使用人であっても登録拒否事由に該当する（貸金業法第6条第1項第5号、第13号）。",
            "該当する（正答ではない）。監督上の処分により貸金業の登録を取り消された法人において、取消しの日前30日以内に役員であった者で取消しの日から5年を経過しないものは、政令で定める使用人であっても登録拒否事由に該当する（貸金業法第6条第1項第3号、第13号）。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 9. 93009 question_93009_1.json#4 (問5) 返済能力調査・年収証明書
    ("93009", "question_93009_1_merged_explanationText_added.json", "question_93009_1.json#4"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "貸金業法", "article": "13条3項1号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法施行規則", "article": "10条の17第1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358M50000040040?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業者向けの総合的な監督指針", "article": "II-2-2(2)", "role": "current_basis", "scope": "choice", "source": "https://www.fsa.go.jp/common/law/guide/kashikin/02.html", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "貸金業法", "article": "13条の2第1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "貸金業法第13条第3項第1号、第13条の2第1項、施行規則第10条の17第1項。適切な記述はcとdの組み合わせ（選択肢4）である。",
        "explanationReferences": [
            {"title": "貸金業法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/358AC0000000032?asof=2026-09-21", "referenceDate": "2026-09-21"},
            {"title": "貸金業者向けの総合的な監督指針", "sourceUrl": "https://www.fsa.go.jp/common/law/guide/kashikin/02.html", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "誤り。aは不適切である。新規の貸付けの金額が60万円であり50万円を超えるため、他社借入との合算が100万円未満であっても年収証明書の提出又は提供を受けなければならない（貸金業法第13条第3項第1号）。提出を受ける必要はないとする記述は誤りである。",
            "誤り。aは不適切であるため、aを含む本肢は誤りである。",
            "誤り。bは不適切である。給与支払明細書を年収証明書とする場合は、直近2か月分以上の連続したものでなければならず（貸金業法施行規則第10条の17第1項第8号）、「任意の2か月分以上」とする記述は誤りである。",
            "正しい。適切な記述はcとdであるため、本肢が正答となる。\nc（適切）: 監督指針によれば、地方公共団体が行政サービスの一環として交付する所得・課税証明書は、地方税法等に明文の発行根拠がなくても、年収証明書のうちの所得証明書に含まれるとされている。\nd（適切）: 監督指針によれば、個人顧客から年収証明書の提出を受けられず年収を把握できないときは、返済能力を確認できないことから、過剰貸付けの禁止（法第13条の2第1項）により契約を締結できないことに留意する必要があるとされている。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    },

    # 10. 93009 question_93009_1.json#14 (問15) 出資法・利息制限法・保証料
    ("93009", "question_93009_1_merged_explanationText_added.json", "question_93009_1.json#14"): {
        "isLawRelated": True,
        "lawGroundedExplanationNotNeeded": False,
        "lawReferences": [
            [{"lawTitle": "出資法", "article": "5条2項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000195?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "出資法", "article": "5条の2第1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000195?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "利息制限法", "article": "1条3号", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
            [{"lawTitle": "利息制限法", "article": "8条1項", "role": "current_basis", "scope": "choice", "source": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21", "verificationStatus": "verified"}],
        ],
        "lawContextForExplanation": "出資法第5条第2項、第5条の2第1項、利息制限法第1条第3号、第8条第1項。適切な記述はaの1個のみであるため、正答は「1個」（選択肢1）である。",
        "explanationReferences": [
            {"title": "出資の受入れ、預り金及び金利等の取締りに関する法律（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000195?asof=2026-09-21", "referenceDate": "2026-09-21"},
            {"title": "利息制限法（e-Gov法令検索）", "sourceUrl": "https://laws.e-gov.go.jp/api/2/law_file/xml/329AC0000000100?asof=2026-09-21", "referenceDate": "2026-09-21"}
        ],
        "explanationText": [
            "正しい。a〜dの記述のうち、内容が適切なものはaの1個のみであるため、適切な選択肢である。\na（適切）: 業として金銭の貸付けを行う者が年20％を超える割合による利息の契約をしたときは刑事罰の対象となる（出資法第5条第2項）。本問の利息は年18％であり年20％を超えていないため、出資法上の刑事罰の対象とならない。\nb（不適切）: 業として保証を行う者が貸付けの利息と合算して年20％を超える割合となる保証料の契約をしたときは刑事罰の対象となる（出資法第5条の2第1項）。本問では利息年18％と保証料年3％を合算すると年21％となり年20％を超えるため、刑事罰の対象となる。「刑事罰の対象とならない」とする記述は誤りである。\nc（不適切）: 元本100万円以上の金銭消費貸借の利息制限法上の上限利率は年15％である（利息制限法第1条第3号）。約定利率年18％のうち年15％を超える部分（年3％）は無効となるため、「その全部について有効」とする記述は誤りである。\nd（不適切）: 主たる債務の利息が年18％でありすでに法定上限年15％に達している（超過している）ため、保証料の有効限度額（年15％－利息分）は0円となる（利息制限法第8条第1項）。したがって保証料の約定は全部無効となり、「その全部について有効」とする記述は誤りである。",
            "誤り。内容が適切な記述はaの1個のみであるため、2個とする本肢は誤りである。",
            "誤り。内容が適切な記述はaの1個のみであるため、3個とする本肢は誤りである。",
            "誤り。内容が適切な記述はaの1個のみであるため、4個とする本肢は誤りである。"
        ],
        "questionLearningPatternId": "legal_elements",
        "suggestedQuestionDetailsByChoice": [],
        "lawRevisionFacts": []
    }
}


def main():
    root = Path("output/kashikin/questions_json")
    for (gid, patch_filename, source_ref), patch_info in ADDITIONS.items():
        patch_path = root / gid / "21_explanationText_added" / patch_filename
        source_stem = source_ref.split("#")[0]
        source_idx = int(source_ref.split("#")[1])
        source_path = root / gid / "00_source" / source_stem

        with open(source_path, encoding="utf-8") as sf:
            src_data = json.load(sf)
        q_src = src_data["question_bodies"][source_idx]

        with open(patch_path, encoding="utf-8") as pf:
            patch_list = json.load(pf)

        # Check if already present
        if any(item.get("sourceRecordRef") == source_ref for item in patch_list):
            print(f"Already present: {gid} {patch_filename} {source_ref}")
            continue

        # Get questionType patch info
        qtype_path = root / gid / "10_questionType_fixed" / f"{source_stem.replace('.json', '')}_questionType_fixed.json"
        with open(qtype_path, encoding="utf-8") as qf:
            qtype_list = json.load(qf)
        qtype_item = [x for x in qtype_list if x.get("sourceRecordRef") == source_ref][0]

        # Build full 21 patch entry
        entry = {
            "questionBodyText": q_src["questionBodyText"],
            "examLabel": q_src["examLabel"],
            "questionLabel": q_src["questionLabel"],
            "choiceTextList": q_src["choiceTextList"],
            "originalQuestionChoiceImageUrls": q_src.get("originalQuestionChoiceImageUrls", [[], [], [], []]),
            "category": q_src.get("category"),
            "examYear": q_src["examYear"],
            "examOccurrenceId": q_src["examOccurrenceId"],
            "list_group_id": q_src["list_group_id"],
            "question_url": q_src["question_url"],
            "public_question_id": q_src["public_question_id"],
            "original_question_id": q_src["original_question_id"],
            "source_question_id": q_src["source_question_id"],
            "source_public_question_id": q_src["source_public_question_id"],
            "questionSourceSite": q_src["questionSourceSite"],
            "canonical_question_key": q_src["canonical_question_key"],
            "question_id_policy_key": q_src["question_id_policy_key"],
            "question_id_policy_version": q_src["question_id_policy_version"],
            "question_id_source_key_description": q_src["question_id_source_key_description"],
            "sourceUniqueKeys": q_src["sourceUniqueKeys"],
            "questionImageStorageUrls": q_src.get("questionImageStorageUrls", []),
            "questionIntent": qtype_item.get("questionIntent", q_src.get("questionIntent")),
            "correctChoiceText": q_src.get("correctChoiceText"),
            "explanation_common_prefix": q_src.get("explanation_common_prefix", []),
            "explanation_common_prefix_inferred_correct_choice": q_src.get("explanation_common_prefix_inferred_correct_choice"),
            "explanation_common_summary": q_src.get("explanation_common_summary", []),
            "explanation_choice_snippets": q_src.get("explanation_choice_snippets", []),
            "explanation_choice_correctness": q_src.get("explanation_choice_correctness", []),
            "answer_result_text": q_src.get("answer_result_text"),
            "answer_result_inferred_correct_choice_numbers": q_src.get("answer_result_inferred_correct_choice_numbers", []),
            "questionType": qtype_item.get("questionType", "true_false"),
            "isCalculationQuestion": qtype_item.get("isCalculationQuestion", False),
            "isLawRelated": patch_info["isLawRelated"],
            "lawGroundedExplanationNotNeeded": patch_info["lawGroundedExplanationNotNeeded"],
            "lawReferences": patch_info["lawReferences"],
            "lawContextForExplanation": patch_info["lawContextForExplanation"],
            "sourceQuestionKey": qtype_item["sourceQuestionKey"],
            "reviewQuestionId": qtype_item["reviewQuestionId"],
            "sourceRecordRef": source_ref,
            "explanationReferences": patch_info["explanationReferences"],
            "explanationText": patch_info["explanationText"],
            "questionLearningPatternId": patch_info["questionLearningPatternId"],
            "suggestedQuestionDetailsByChoice": patch_info["suggestedQuestionDetailsByChoice"],
            "lawRevisionFacts": patch_info["lawRevisionFacts"],
        }

        patch_list.append(entry)
        with open(patch_path, "w", encoding="utf-8") as pf:
            json.dump(patch_list, pf, ensure_ascii=False, indent=2)
        print(f"Added {gid} {patch_filename} {source_ref} ({q_src['questionLabel']}), new total: {len(patch_list)}")


if __name__ == "__main__":
    main()
