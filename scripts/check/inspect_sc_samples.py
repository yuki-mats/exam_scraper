import json
import re
import sys
from pathlib import Path

def load_samples():
    with open("output/sc_sample_100.json", "r", encoding="utf-8") as fp:
        return json.load(fp)

def audit_single_sample(item):
    """機械的に異常・懸念点を検出する"""
    issues = []
    
    qtext = item.get("questionText", "") or ""
    ctext = item.get("originalQuestionChoiceText", "") or ""
    ans = item.get("correctChoiceText", "") or ""
    exp = item.get("explanationText")  # None の可能性あり
    qtype = item.get("questionType", "")
    qimgs = item.get("questionImageUrls", [])
    
    # 1. リテラル \n の検出
    if exp and "\\n" in exp:
        issues.append("解説にリテラル '\\n' が含まれている")
    if "\\n" in qtext:
        issues.append("問題文にリテラル '\\n' が含まれている")
        
    # 2. ブログ調・不適切表現
    blog_words = ["ですね", "捨て問題", "丸暗記", "過去問道場", "クリック", "リンク先", "参照してください"]
    if exp:
        for w in blog_words:
            if w in exp:
                issues.append(f"解説に不適切・ブログ調表現 '{w}' が含まれている")
            
    # 3. プレースホルダ
    placeholders = ["TODO", "FIXME", "undefined", "NaN"]
    for p in placeholders:
        if (exp and p in exp) or p in qtext:
            issues.append(f"プレースホルダ '{p}' が含まれている")
            
    # 4. 正答値チェック
    if qtype == "true_false":
        if ans not in ["正しい", "間違い"]:
            issues.append(f"true_false の正答値が不正: '{ans}'")
        if exp is None:
            issues.append("true_false なのに解説が None")
    elif qtype == "flash_card":
        if not ans:
            issues.append("flash_card の正答が空")
        if ans == "正しい" and (exp is None or len(exp.strip()) == 0):
            issues.append("flash_card の正答ドキュメントなのに解説が空")
            
    # 5. 「間違い」の解説で理由が薄い（true_falseの場合のみ）
    if qtype == "true_false" and ans == "間違い":
        if exp and (exp.strip() == "間違い。" or len(exp.strip()) < 10):
            issues.append("「間違い」判定なのに理由の説明が不足している")
        
    # 6. 画像URLの検証
    for img in qimgs:
        if not img.startswith("https://firebasestorage.googleapis.com/"):
            issues.append(f"画像URLが正規のStorage URLでない: {img}")
            
    return issues

def show_sample(idx, item):
    issues = audit_single_sample(item)
    status_str = "【要確認】" if issues else "【良好】"
    
    print("=" * 80)
    print(f"サンプル #{idx:02d} {status_str} | 回: {item.get('listGroupId')} | Type: {item.get('questionType')} | Set: {item.get('questionSetId')}")
    print(f"Original QID: {item.get('originalQuestionId')}")
    if item.get("questionImageUrls"):
        print(f"画像あり ({len(item.get('questionImageUrls'))}点): {item.get('questionImageUrls')}")
    if issues:
        print(f"⚠️ 検出事項: {issues}")
    print("-" * 40)
    print(f"【問題文】\n{item.get('questionText')}\n")
    if item.get("originalQuestionChoiceText"):
        print(f"【対象肢】 {item.get('originalQuestionChoiceText')}\n")
    print(f"【正答】 {item.get('correctChoiceText')}\n")
    print(f"【解説】\n{item.get('explanationText')}")
    print("=" * 80)
    return issues

if __name__ == "__main__":
    samples = load_samples()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--scan-all":
        all_issues = []
        for idx, s in enumerate(samples, 1):
            iss = audit_single_sample(s)
            if iss:
                all_issues.append((idx, s, iss))
        print(f"=== 全100問自動スキャン結果: 検出あり {len(all_issues)} 件 ===")
        for idx, s, iss in all_issues:
            print(f"サンプル #{idx:02d} ({s.get('listGroupId')} {s.get('originalQuestionId')}): {iss}")
    else:
        start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
        end = int(sys.argv[2]) if len(sys.argv) > 2 else start
        for i in range(start, end + 1):
            if 1 <= i <= len(samples):
                show_sample(i, samples[i - 1])
