"""丙種の直接整備用の読取packet。判断・正答・解説の自動生成は行わない。"""
from pathlib import Path
import hashlib
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
TOOLS = RUN / "tool_snapshot"
sys.path.insert(0, str(TOOLS))
from scripts.common.question_identity import load_source_record_inventory
from tools.question_review_console.inventory import QuestionInventory


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def prepare():
    inventory = QuestionInventory(ROOT)
    records = {}
    for year in range(2017, 2026):
        packets = json.loads((ROOT / f"output/gas-shunin-hei/pdf_review/{year}/packet.json").read_text())
        pdfs = {row["sourceQuestionId"]: row for row in packets["questions"]}
        for source in load_source_record_inventory(
            ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source",
            qualification="gas-shunin-hei", list_group_id=str(year),
        ):
            binding = source.identity.binding
            result = inventory.projected_input("gas-shunin-hei", str(year), binding.source_record_ref)
            if result.errors:
                raise ValueError(result.errors)
            row = result.record
            key = source.record["source_question_id"]
            packet = pdfs[key]
            records[key] = {"sourceIdentity": binding.as_mapping(), "record": row,
                            "sourcePath": str(source.path), "sourceSha256": hashlib.sha256(source.path.read_bytes()).hexdigest(),
                            "recordHash": digest(json.dumps(row, ensure_ascii=False, sort_keys=True)),
                            "questionPages": packet["candidateQuestionPages"],
                            "officialAnswerNumberDraft": packet["officialAnswerNumberDraft"],
                            "officialQuestionPdf": packet["officialQuestionPdf"],
                            "officialAnswerPdf": packet["officialAnswerPdf"]}
    candidates = {}
    for qual in ("gas-shunin-kou", "gas-shunin-otsu"):
        for path in sorted((ROOT / "output" / qual / "questions_json").glob("*/25_verified_publication/*_verified_publication.json")):
            for row in json.loads(path.read_text()).get("questions", []):
                text = row.get("originalQuestionChoiceText")
                if not isinstance(text, str) or not text.rstrip().endswith("。"):
                    continue
                key = digest("".join(text.split()))
                candidates.setdefault(key, []).append({"path": str(path), "questionId": row.get("questionId"),
                    "choiceText": text, "bodyText": row.get("originalQuestionBodyText"),
                    "correctChoiceText": row.get("correctChoiceText"), "explanationText": row.get("explanationText"),
                    "lawReferences": row.get("lawReferences"), "lawRevisionFacts": row.get("lawRevisionFacts"),
                    "questionLearningPatternId": row.get("questionLearningPatternId")})
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    (RUN / "reference-candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n")
    print("fixed records", len(records), "reference statement keys", len(candidates))


def show(year, section, first, last):
    records = json.loads((RUN / "records.json").read_text())
    candidates = json.loads((RUN / "reference-candidates.json").read_text())
    for key, item in sorted(records.items(), key=lambda value: int(value[0].split("問")[-1])):
        if not key.startswith(f"{year}:{section}:") or not first <= int(key.split("問")[-1]) <= last:
            continue
        q = item["record"]
        print("\n###", key, item["sourceIdentity"]["reviewQuestionId"], q.get("questionType"), "PDF", item["questionPages"], "official", item["officialAnswerNumberDraft"])
        print(q["questionBodyText"])
        for text in q["choiceTextList"]:
            key_hash = digest("".join(text.split()))
            print("CHOICE", key_hash[:12], text)
            hits = candidates.get(key_hash, [])
            distinct = {}
            for hit in hits:
                signature = (hit.get("correctChoiceText"), hit.get("explanationText"))
                distinct.setdefault(signature, hit)
            for hit in list(distinct.values())[:2]:
                print("REFERENCE", hit["questionId"], hit.get("correctChoiceText"), hit.get("explanationText"), hit.get("lawReferences"))
        if q.get("explanationText"):
            print("CURRENT EXPLANATIONS", json.dumps(q["explanationText"], ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare()
    elif sys.argv[1] == "show":
        show(int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
