"""Private save previews for explanation improvements to verified gas canonicals.

This is a preparation tool, not an approval, formal-save or publication gateway.
Other source formats must retain their own readers and correction contracts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from scripts.common.scoped_canonical_context import file_hash
from scripts.common.repaso_firestore_schema import _is_suggested_question_detail_list
from tools.question_review_console.projection import sha256_json
from tools.question_review_console.scoped_artifacts import write_json


EXPLANATION_FIELDS = frozenset({
    "explanationText", "suggestedQuestions", "suggestedQuestionDetails",
})
IDENTITY_FIELDS = (
    "originalQuestionId", "originalQuestionBodyText", "originalQuestionChoiceText",
    "qualificationId", "examYear", "questionType", "isChoiceOnly", "correctChoiceText",
)


def checked_json(reference):
    path = Path(reference["ref"])
    if path.is_symlink() or file_hash(path) != reference["sha256"]:
        raise ValueError("artifact hash differs")
    return json.loads(path.read_text(encoding="utf-8"))


def project_verified_bundle(bundle, updates):
    """Apply only exact explanation deltas, preserving the complete canonical set."""
    if bundle.get("schemaVersion") != "gas-shunin-verified-publication/v1":
        raise ValueError("unsupported canonical reader")
    if bundle.get("qualificationId") not in {"gas-shunin-kou", "gas-shunin-otsu"}:
        raise ValueError("unsupported canonical qualification")
    rows = bundle.get("questions")
    if not isinstance(rows, list) or bundle.get("total_count") != len(rows):
        raise ValueError("canonical count differs")
    ids = [row.get("questionId") for row in rows]
    if not ids or not all(isinstance(q, str) and q for q in ids) or len(set(ids)) != len(ids):
        raise ValueError("canonical IDs empty or duplicate")
    projected = deepcopy(bundle)
    by_id = {row["questionId"]: row for row in projected["questions"]}
    seen = set()
    for update in updates:
        qid = update["questionId"]
        if qid in seen or qid not in by_id:
            raise ValueError("update identity absent or duplicate")
        seen.add(qid)
        snapshot = checked_json(update["baseline"])
        if snapshot.get("questionId") != qid or snapshot.get("exists") is not True:
            raise ValueError("baseline identity differs")
        if snapshot.get("updateTimeExact") != update["baseline"].get("updateTimeExact"):
            raise ValueError("baseline version differs")
        row, before = by_id[qid], snapshot["fields"]
        if any(row.get(key) != before.get(key) for key in IDENTITY_FIELDS):
            raise ValueError("canonical identity or verdict differs from baseline")
        if row.get("questionType") != "true_false" or row.get("isChoiceOnly") is not False:
            raise ValueError("explanation plan requires a true-false display document")
        diffs = update.get("fieldDiffs")
        if not isinstance(diffs, list) or not diffs:
            raise ValueError("nonempty field deltas required")
        fields = set()
        for delta in diffs:
            field = delta["field"]
            if field not in EXPLANATION_FIELDS or field in fields:
                raise ValueError("unsupported or duplicate delta field")
            fields.add(field)
            if type(delta.get("beforePresent")) is not bool or delta.get("afterPresent", True) is not True:
                raise ValueError("this canonical route supports replacement fields only")
            for value in (before, row):
                if (field in value) != delta["beforePresent"] or value.get(field) != delta["before"]:
                    raise ValueError("before field value or presence differs")
            if delta["beforePresent"] and delta["before"] == delta["after"]:
                raise ValueError("no-op delta")
            row[field] = deepcopy(delta["after"])
        text = row.get("explanationText")
        if not isinstance(text, str) or not text.startswith(row["correctChoiceText"] + "。"):
            raise ValueError("explanation verdict differs")
        details, prompts = row.get("suggestedQuestionDetails"), row.get("suggestedQuestions")
        if details is not None and (not _is_suggested_question_detail_list(details)
                or len(details) > 3 or prompts != [item["question"] for item in details]):
            raise ValueError("suggestion mirror differs")
        if prompts is not None and details is None:
            raise ValueError("suggestion details absent")
    if not seen:
        raise ValueError("empty update scope")
    for old, new in zip(rows, projected["questions"], strict=True):
        if old["questionId"] not in seen and old != new:
            raise ValueError("out-of-scope canonical change")
    return projected


def prepare_plan(root, manifest_path, bindings_path, destination):
    root = Path(root).resolve()
    destination = Path(destination).absolute()
    private_root = root / "output/user_feedback_response_system/execution"
    if destination != destination.resolve() or not destination.is_relative_to(private_root):
        raise ValueError("physical private execution destination required")
    if destination.exists():
        raise ValueError("fresh destination required")
    manifest_path, bindings_path = Path(manifest_path), Path(bindings_path)
    inputs = {str(p): file_hash(p) for p in (manifest_path, bindings_path, Path(__file__).resolve())}
    manifest = json.loads(manifest_path.read_text())
    binding_rows = json.loads(bindings_path.read_text())["results"]
    bindings = {entry["questionId"]: entry for entry in binding_rows}
    if len(bindings) != len(binding_rows):
        raise ValueError("duplicate source binding")
    groups, units, remaining = {}, [], []
    for task in manifest["tasks"]:
        result = checked_json({"ref": task["artifactRef"], "sha256": task["artifactSha256"]})
        inputs[task["artifactRef"]] = task["artifactSha256"]
        if result.get("taskId") != task["taskId"] or result.get("status") != task["status"]:
            raise ValueError("task binding differs")
        if any(result.get(flag) is not False for flag in ("approvalReady", "formalPatchSaved", "FirestoreWritten")):
            raise ValueError("unapproved preparation result required")
        selected = []
        if task["status"] == "review_pending":
            for update in result["proposedUpdates"]:
                binding = bindings.get(update["questionId"], {})
                candidates = [c for c in binding.get("candidates", []) if c["sourceKind"] == "verified_25"]
                physical = [c for c in candidates if Path(c["ref"]).is_relative_to(root)]
                if binding.get("status") != "matched" or len(physical) != 1:
                    break
                source = physical[0]
                bundle = checked_json(source)
                year, qual = str(bundle["list_group_id"]), bundle["qualificationId"]
                expected = root / "output" / qual / "questions_json" / year / "25_verified_publication" / f"{year}_verified_publication.json"
                if Path(source["ref"]) != expected or expected != expected.resolve():
                    raise ValueError("source is not the physical verified canonical")
                if bundle.get("evidence", {}).get("documentIndexSha256") is None:
                    raise ValueError("official verification binding absent")
                patch = checked_json(update["preparedPatch"])
                if (patch.get("schemaVersion") != "feedback-improvement-private-patch/v1"
                        or patch.get("taskId") != task["taskId"]
                        or patch.get("questionId") != update["questionId"]
                        or patch.get("fieldDiffs") != update["fieldDiffs"]
                        or patch.get("precondition") != {"updateTimeExact": update["baseline"]["updateTimeExact"], "baselineSha256": update["baseline"]["sha256"]}
                        or any(patch.get(flag) is not False for flag in ("approvalReady", "formalPatchSaved", "FirestoreWritten", "executablePatch", "fullDocumentUploadAllowed"))):
                    raise ValueError("prepared patch differs")
                for ref in [update["baseline"], update["preparedPatch"], *result["primaryEvidence"]]:
                    if file_hash(Path(ref["ref"])) != ref["sha256"]:
                        raise ValueError("review input changed")
                    inputs[ref["ref"]] = ref["sha256"]
                selected.append((expected, bundle, update))
        if not selected or len(selected) != len(result["proposedUpdates"]):
            remaining.append({"taskId": task["taskId"], "status": task["status"], "reason": "primary_reason_unresolved" if task["status"] == "blocked" else "different_source_or_correction_reader_required"})
            continue
        for path, bundle, update in selected:
            group = groups.setdefault(str(path), {"bundle": bundle, "updates": []})
            group["updates"].append(update)
            inputs[str(path)] = file_hash(path)
            units.append({"taskId": task["taskId"], "questionId": update["questionId"], "formalPath": str(path.relative_to(root)), "fieldDiffs": update["fieldDiffs"], "maintenanceOnly": update.get("maintenanceOnly", {}), "reviewPending": True})
    artifacts = []
    for path, group in sorted(groups.items()):
        projected = project_verified_bundle(group["bundle"], group["updates"])
        year = projected["list_group_id"]
        candidate = destination / "planned-files" / Path(path).relative_to(root)
        write_json(candidate, projected)
        merged = {**deepcopy(projected), "schemaVersion": "gas-shunin-verified-merged/v1"}
        converted = {"list_group_id": year, "questions": deepcopy(projected["questions"]), "total_count": projected["total_count"]}
        for name, value in [("merged-preview.json", merged), ("converted-preview.json", converted)]:
            write_json(destination / "previews" / year / name, value)
        scope = {u["questionId"] for u in group["updates"]}
        original_ids = {r["originalQuestionId"] for r in projected["questions"] if r["questionId"] in scope}
        siblings = [r["questionId"] for r in projected["questions"] if r["originalQuestionId"] in original_ids]
        artifacts.append({"formalPath": str(Path(path).relative_to(root)), "beforeSha256": inputs[path], "candidateRef": str(candidate), "candidateSha256": file_hash(candidate), "documentCount": projected["total_count"], "changedIds": sorted(scope), "completeSiblingIds": sorted(siblings), "outsideScopeUnchanged": True})
    for path, digest in inputs.items():
        if file_hash(Path(path)) != digest:
            raise ValueError("input changed during preparation")
    plan = {"schemaVersion": "feedback-verified-explanation-save-preview/v1", "inputs": inputs, "units": units, "artifacts": artifacts, "remainingTasks": remaining,
        "requiredNext": ["independent_content_review", "fresh_masked_live_readback", "exact_diff_human_formal_save_approval", "dedicated_verified_canonical_save_receipt"],
        "formalPatchSaved": False, "FirestoreWritten": False, "approvalReady": False, "executablePatch": False, "fullDocumentUploadAllowed": False}
    plan["planHash"] = sha256_json(plan)
    write_json(destination / "save-preview-plan.json", plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = prepare_plan(Path(__file__).resolve().parents[2], args.manifest, args.bindings, args.output)
    print(json.dumps({"plannedTasks": len(plan["units"]), "remainingTasks": len(plan["remainingTasks"]), "formalPatchSaved": False, "FirestoreWritten": False}))


if __name__ == "__main__":
    main()
