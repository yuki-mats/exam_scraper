#!/usr/bin/env python3
import argparse, json, subprocess
from contract import ARTIFACTS, ORACLE_WORDS, REPO

p=argparse.ArgumentParser()
for flag in ("require-generation-prompt-parity","require-audit-common-hash-parity","require-sealed-before-oracle","require-peak-concurrency","forbid-oracle-in-prompts","forbid-production-writes"): p.add_argument("--"+flag,action="store_true")
a=p.parse_args()
for phase in ("screen","expand"):
    root=ARTIFACTS/phase; run=json.loads((root/"run.json").read_text()); seal=json.loads((root/"seal.json").read_text()); oracle=json.loads((root/"oracle-after-run.json").read_text())
    assert run["peakConcurrency"]==1 and run["usageEstimated"] is False and oracle["createdAfterSeal"] is True
    assert len({v["candidatePromptSha256"] for v in run["records"].values()})==1
    assert len({tuple(v["auditCommonSha256"]) for v in run["records"].values()})==1
    assert seal["sealedAt"] <= (root/"oracle-after-run.json").stat().st_mtime
diff=subprocess.run(["git","diff","--name-only"],cwd=REPO,text=True,capture_output=True,check=True).stdout.splitlines()
allowed="docs/goals/question-maintenance-lower-cloud-model-benchmark/"
outside=[x for x in diff if not x.startswith(allowed)]
if outside: raise RuntimeError("outside allowed files changed: "+str(outside))
print("benchmark verification ok")
