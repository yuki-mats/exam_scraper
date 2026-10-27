"""保存済みの直接整備対象だけを既存projectionと3checkerで検証する。"""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "output/gas-shunin-hei/direct_publication/20261027"
TOOLS = RUN / "tool_snapshot"
sys.path.insert(0, str(TOOLS))
from scripts.common.question_identity import load_source_record_inventory
from tools.question_review_console.inventory import QuestionInventory

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
work = RUN / "checks" / stamp
work.mkdir(parents=True, exist_ok=False)
selected = {}
for receipt in (RUN / "saved").glob("*.json"):
    for item in json.loads(receipt.read_text())["candidates"]:
        selected[item["sourceQuestionId"]] = item["sourceIdentity"]
inventory = QuestionInventory(ROOT)
groups = {}
for key, binding in selected.items():
    year = key.split(":")[0]
    result = inventory.projected_input("gas-shunin-hei", year, binding["sourceRecordRef"])
    if result.errors:
        raise ValueError(result.errors)
    groups.setdefault(year, []).append(result.record)
base = work / "gas-shunin-hei/questions_json"
for year, rows in groups.items():
    path = base / year / "30_merged_2" / f"question_{year}_merged_direct.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"question_bodies": rows}, ensure_ascii=False, indent=2) + "\n")
commands = [
    [sys.executable, str(TOOLS / "scripts/check/check_choice_text_alignment.py"), "--base-dir", str(base), "--stage", "merged"],
    [sys.executable, str(TOOLS / "scripts/check/check_required_fields.py"), "--base-dir", str(base), "--stage", "merged"],
]
for year in groups:
    directory = ROOT / f"output/gas-shunin-hei/questions_json/{year}"
    stems = {Path(value["sourceRecordRef"].split("#")[0]).stem for key, value in selected.items() if key.startswith(year + ":")}
    for stem in sorted(stems):
        patch = directory / "23_correctChoiceText_fixed" / (stem + "_merged_correctChoiceText_fixed.json")
        legacy = directory / "23_correctChoiceText_fixed" / (stem + "_correctChoiceText_fixed.json")
        if legacy.exists():
            if patch.exists():
                raise ValueError("正答patchの現行名と既存名が重複している")
            patch = legacy
        commands.append([sys.executable, str(TOOLS / "scripts/check/check_correct_choice_patch_coverage.py"),
            "--source", str(directory / "00_source" / (stem + ".json")),
            "--patch", str(patch),
            "--question-type-patch", str(directory / "10_questionType_fixed" / (stem + "_questionType_fixed.json"))])
results = []
for index, command in enumerate(commands):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    log = work / f"check-{index:02}.log"
    log.write_text(result.stdout + result.stderr)
    results.append({"argv": command, "exitCode": result.returncode,
                    "log": str(log.relative_to(ROOT)), "logSha256": hashlib.sha256(log.read_bytes()).hexdigest()})
    print(Path(command[1]).name, "exit", result.returncode, result.stdout.strip())
baseline = json.loads((ROOT / "output/gas-shunin-hei/production/20261027/source-baseline.json").read_text())
source_changed = [name for name, expected in baseline.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
code = json.loads((RUN / "execution-baseline.json").read_text())["workingCodeUnchanged"]
paused_code_changed = [name for name, expected in code.items() if name != "scripts/convert/convert_merged_to_firestore.py"
                       and hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
observation_path = RUN / "workspace-code-observation.json"
observation = json.loads(observation_path.read_text()) if observation_path.exists() else {}
observations = observation.get("observations", [observation])
observed_changes = []
for name in paused_code_changed:
    for item in observations:
        if name != item.get("path") or item.get("executedForHei") is not False:
            continue
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != item.get("observedSha256"):
            continue
        diff = ROOT / item["diffPath"]
        if hashlib.sha256(diff.read_bytes()).hexdigest() == item.get("diffSha256"):
            observed_changes.append(name)
            break
unreviewed_changes = sorted(set(paused_code_changed) - set(observed_changes))
# 実行するsnapshot自体をGit objectと照合する。並行checkoutの状態とは分ける。
commit = json.loads((RUN / "execution-baseline.json").read_text())["toolCommit"]
tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", commit, "--", "scripts", "tools", "config", "prompt", "document"], cwd=ROOT)
snapshot_changed = []
snapshot_files = 0
for entry in tree.split(b"\0"):
    if not entry:
        continue
    metadata, raw_name = entry.split(b"\t", 1)
    mode, kind, expected = metadata.decode().split()
    name = raw_name.decode()
    path = TOOLS / name
    if kind != "blob":
        continue
    snapshot_files += 1
    data = path.read_bytes() if path.exists() else b""
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if not path.exists() or actual != expected:
        snapshot_changed.append(name)
summary = {"generatedAt": stamp, "selectedCount": len(selected), "byYear": {year: len(rows) for year, rows in groups.items()},
           "commands": results, "sourceFilesChecked": len(baseline), "sourceChanged": source_changed,
           "pausedCodeChanged": paused_code_changed, "reviewedConcurrentCodeChanges": observed_changes,
           "unreviewedCodeChanges": unreviewed_changes, "snapshotFilesChecked": snapshot_files,
           "snapshotChanged": snapshot_changed, "wholeSystemTests": "not_run_data_only",
           "pass": all(row["exitCode"] == 0 for row in results) and not source_changed and not unreviewed_changes and not snapshot_changed}
(work / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print("target checks", summary["pass"], "questions", len(selected), "receipt", work)
sys.exit(0 if summary["pass"] else 1)
