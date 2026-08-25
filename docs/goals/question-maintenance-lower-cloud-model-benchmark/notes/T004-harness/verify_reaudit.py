#!/usr/bin/env python3
import argparse,json,subprocess
from recontract import ARTIFACTS,REPO,file_hash
p=argparse.ArgumentParser()
for flag in ("require-no-generation-calls","require-audit-common-hash-parity","require-sealed-before-oracle","forbid-index-normalization","forbid-oracle-in-prompts","require-t003-hashes-unchanged"):p.add_argument("--"+flag,action="store_true")
p.add_argument("--require-audit-index-base");p.add_argument("--require-peak-concurrency",type=int);p.add_argument("--require-building-unscored",type=int);a=p.parse_args()
assert a.require_no_generation_calls and a.require_audit_index_base=="one" and a.require_peak_concurrency==1 and a.require_building_unscored==4
for phase in ("screen","expand"):
 root=ARTIFACTS/phase;run=json.loads((root/"run.json").read_text());oracle=json.loads((root/"oracle-after-seal.json").read_text())
 assert run["generationCalls"]==0 and run["peakConcurrency"]==1 and run["usageEstimated"] is False and oracle["createdAfterSeal"]
 assert len({tuple(v["auditCommonSha256"]) for v in run["records"].values()})==1
 source=run["source"];assert file_hash(REPO/source["run"])==source["runSha256"];assert file_hash((REPO/source["run"]).parent/"seal.json")==source["sealSha256"]
 if phase=="expand":assert run["buildingUnscored"]==4 and len(run["fixedIds"])==28
diff=subprocess.run(["git","diff","--name-only"],cwd=REPO,text=True,capture_output=True,check=True).stdout.splitlines()
outside=[x for x in diff if not x.startswith("docs/goals/question-maintenance-lower-cloud-model-benchmark/")]
if outside:raise RuntimeError("outside allowed files changed: "+str(outside))
print("reaudit verification ok")
