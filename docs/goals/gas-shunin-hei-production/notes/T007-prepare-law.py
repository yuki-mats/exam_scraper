"""一問ずつ直接執筆した判定と根拠locatorへ、実取得の本文束を添付する。判断は生成しない。"""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
sys.path.insert(0, str(RUN / "tool_snapshot"))
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.primary_law_evidence import PrimaryLawEvidenceResolver, locator_parts

path = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
if target.exists():
    raise ValueError("固定候補を上書きしない")
rows = json.loads(path.read_text())
if len(rows) != 1:
    raise ValueError("一問だけを扱う")
q = copy.deepcopy(rows[0])
key = q["sourceQuestionId"]
year = key.split(":")[0]
packet = json.loads((RUN / "records.json").read_text())[key]
projection = QuestionInventory(ROOT).projected_input("gas-shunin-hei", year, packet["sourceIdentity"]["sourceRecordRef"])
if projection.errors:
    raise ValueError(projection.errors)
current = projection.record
choices = current["choiceTextList"]
if not all(len(q[name]) == len(choices) for name in ("correctChoiceText", "examTimeDecision", "currentLawDecision", "lawReferences", "primaryChoiceLegalJudgments")):
    raise ValueError("同一問の全肢の直接判定が必要")
if q["correctChoiceText"] != q["currentLawDecision"]:
    raise ValueError("現行法判定と正答を独立に再確認する")
evidence = PrimaryLawEvidenceResolver(ROOT).resolve({**current, **q}, current_as_of=q["currentAsOf"],
    qualification="gas-shunin-hei", list_group_id=year)
if evidence["status"] != "complete":
    raise ValueError(evidence)
q["primaryLawEvidence"] = evidence
q["lawRevisionFacts"] = []
for index, choice in enumerate(choices):
    authored = q["primaryChoiceLegalJudgments"][index]
    fact = {"auditStatus": authored["auditStatus"], "reviewState": "needs_secondary_review", "reconciliationStatus": "pending",
        "differenceFacts": authored["differenceFacts"], "answerImpactFacts": authored["answerImpactFacts"],
        "evidenceSummary": {"verdict": q["currentLawDecision"][index], "explanationText": authored["basisSummary"],
            "differenceSummary": authored["differenceSummary"], "promptContext": choice, "refs": [], "displayRefIds": []}}
    for output, source, decision in [("examTime", "examSnapshot", "examTimeDecision"), ("current", "currentSnapshot", "currentLawDecision")]:
        fact[output] = {"correctChoiceText": q[decision][index]}
        if authored["auditStatus"] == "not_law_related":
            continue
        reference = q["lawReferences"][index][0]
        if reference.get("verificationStatus") != "verified" or not reference.get("lawTitle"):
            raise ValueError("主担当による公式根拠の確認記録が必要")
        item = next(x for x in evidence["items"] if x["lawId"] == reference["lawId"] and x["choiceIndex"] == index
            and x["declaredArticle"] == reference["article"])
        snapshot = item[source]
        fact[output].update(lawId=reference["lawId"], lawTitle=reference["lawTitle"], lawRevisionId=snapshot["revisionId"],
            referenceDate=snapshot["asOf"], article=reference["article"], verificationStatus="verified",
            sourceUrl=snapshot["sourceUrl"], articleTextHash=snapshot["textHash"])
    q["lawRevisionFacts"].append(fact)
q.update(reviewState="needs_secondary_review", reconciliationStatus="pending", holdReason="", reviewNotes="",
    verificationSummary="主担当が公式冊子と試験時・現行の公式本文を全肢照合した。独立二次監査は未実施。",
    lawGroundedExplanationNotNeeded=False, lawContextForExplanation=q["sourceSummary"], suggestedQuestionDetailsByChoice=[])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps([q], ensure_ascii=False, indent=2) + "\n")
print("公式本文添付", key, len(evidence["items"]), "根拠")
