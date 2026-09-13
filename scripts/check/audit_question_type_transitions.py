"""Git上の01 patchの形式変更を抽出する。正誤判定・patch・本番更新はしない。"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


PATCH_PATHSPEC = ":(glob)output/*/questions_json/*/10_questionType_fixed/*.json"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def index_records(payload: Any) -> dict[str, dict[str, Any]]:
    if isinstance(payload, dict) and "question_bodies" in payload:
        payload = payload["question_bodies"]
    if isinstance(payload, dict):
        rows = [
            {"original_question_id": key, **value}
            if isinstance(value, dict)
            else {"original_question_id": key, "questionType": value}
            for key, value in payload.items()
        ]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("01 patchの形式を認識できません")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("01 patchのrecordがobjectではありません")
        record_id = str(row.get("original_question_id") or row.get("public_question_id") or row.get("reviewQuestionId") or "")
        if not record_id or record_id in result:
            raise ValueError(f"01 patchのIDが欠落又は重複しています: {record_id}")
        result[record_id] = row
    return result


def type_transitions(before: dict, after: dict) -> list[dict[str, Any]]:
    return [
        {"recordId": record_id, "beforeType": "true_false", "afterType": "group_choice"}
        for record_id in sorted(before.keys() & after.keys())
        if before[record_id].get("questionType") == "true_false"
        and after[record_id].get("questionType") == "group_choice"
    ]


def audit(repo: Path, revision: str) -> dict[str, Any]:
    revision = git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()
    paths = [path for path in git(repo, "ls-tree", "-r", "--name-only", revision, "output").splitlines()
             if len(Path(path).parts) == 6
             and Path(path).parts[2] == "questions_json"
             and Path(path).parts[4] == "10_questionType_fixed"
             and path.endswith(".json")]
    current = {}
    errors = []

    def read(commit, path):
        try:
            return index_records(json.loads(git(repo, "show", f"{commit}:{path}")))
        except (ValueError, subprocess.CalledProcessError) as exc:
            errors.append({"revision": commit, "path": path, "error": str(exc)})
            return {}

    for path in paths:
        current[path] = read(revision, path)
    commits = git(
        repo, "log", "--first-parent", "--format=%H", "-G", "questionType.*group_choice",
        revision, "--", PATCH_PATHSPEC,
    ).splitlines()
    transitions = []
    for commit in commits:
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(parents) < 2:
            continue
        parent = parents[1]
        changed_paths = git(
            repo, "diff", "--name-only", "--diff-filter=M", parent, commit,
            "--", PATCH_PATHSPEC,
        ).splitlines()
        for path in changed_paths:
            before = read(parent, path)
            after = read(commit, path)
            for transition in type_transitions(before, after):
                record_id = transition["recordId"]
                latest = current.get(path, {}).get(record_id)
                previous = after[record_id]
                transitions.append({
                    **transition,
                    "commit": commit,
                    "parentCommit": parent,
                    "qualification": path.split("/")[1],
                    "path": path,
                    "sourceQuestionKey": (latest or previous).get("sourceQuestionKey"),
                    "currentPatchType": latest.get("questionType") if latest else None,
                    "currentRecordPresent": latest is not None,
                    "contentUnchangedAtTransition": all(before[record_id].get(field) == previous.get(field) for field in ("questionBodyText", "choiceTextList")),
                })
    records = {}
    for transition in transitions:
        key = (transition["path"], transition["recordId"])
        if key not in records:
            latest = current.get(key[0], {}).get(key[1])
            records[key] = {
                **{k: v for k, v in transition.items() if k not in {"commit", "parentCommit", "beforeType", "afterType", "contentUnchangedAtTransition"}},
                "transitions": [],
                "reviewStatus": "pending_individual_review",
                "questionBodyText": (latest or {}).get("questionBodyText"),
                "choiceTextList": (latest or {}).get("choiceTextList"),
            }
        records[key]["transitions"].append(transition)
    counts = Counter((r["qualification"], r["currentPatchType"]) for r in records.values())
    current_group_counts = Counter(
        path.split("/")[1]
        for path, entries in current.items()
        for row in entries.values()
        if row.get("questionType") == "group_choice"
    )
    return {
        "schemaVersion": "question-type-transition-audit/v1",
        "revision": revision,
        "scope": "first-parent Git history; tracked 10_questionType_fixed JSON at one group depth",
        "limitations": [
            "変更履歴は誤分類の証明ではない。原問・全選択肢を一問ずつ確認する。",
            "未追跡file、履歴消失、改名時のID移動、初回追加前の変更は検出対象外。",
            "currentPatchTypeは01 patchの値。本番や25_verified_publicationとの一致は別途確認する。",
        ],
        "summary": {"patchFileCount": len(paths), "historyCommitCount": len(commits), "transitionCount": len(transitions), "recordCount": len(records), "readErrorCount": len(errors)},
        "counts": [{"qualification": q, "currentPatchType": t, "count": n} for (q, t), n in sorted(counts.items(), key=lambda item: str(item[0]))],
        "currentGroupChoiceCounts": dict(sorted(current_group_counts.items())),
        "errors": errors,
        "records": sorted(records.values(), key=lambda r: (r["qualification"], r["path"], r["recordId"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("監査出力は新規fileを指定してください")
    result = audit(args.repo, args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"summary": result["summary"], "counts": result["counts"]}, ensure_ascii=False, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
