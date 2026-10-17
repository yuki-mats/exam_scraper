import json
import sys

with open("output/chintaikanrishi_sample_30.json", "r", encoding="utf-8") as fp:
    samples = json.load(fp)

def show_questions(start, end):
    for item in samples[start:end]:
        idx = item["sample_index"]
        yr = item["examYear"]
        lbl = item["questionLabel"]
        sid = item["questionSetId"]
        qtype = item["questionType"]
        qtext = item["questionBodyText"]
        choices = item["choiceTextList"]
        ans = item["correctChoiceText"]
        exp = item["explanationText"]
        rev = item["lawRevisionFacts"]
        refs = item["lawReferences"]

        ans_in_choices = ans in choices
        ans_idx = choices.index(ans) + 1 if ans_in_choices else -1

        print("="*80)
        print(f"Sample #{idx:02d} | {yr}年 {lbl} | Type: {qtype} | Set: {sid}")
        print(f"Ans in choices: {ans_in_choices} (肢{ans_idx})")
        print(f"LawRelated: {item.get('isLawRelated')}, References: {refs}")
        print(f"RevisionFacts: {rev}")
        print("-"*40)
        print(f"【問題文】\n{qtext}\n")
        print("【選択肢】")
        for c_idx, c in enumerate(choices, 1):
            mark = "●" if c == ans else " "
            print(f"  {mark} {c_idx}. {c}")
        print("\n【解説】")
        print(exp)
        print("\n")

if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    end = int(sys.argv[2]) if len(sys.argv) > 2 else len(samples)
    show_questions(start, end)
