#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from recontract import T003, verify_source
p=argparse.ArgumentParser();p.add_argument("--dry-run",action="store_true");p.add_argument("--source",type=Path,required=True);a=p.parse_args();assert a.dry_run
assert a.source.resolve()==T003.resolve()
for phase in ("screen","expand"):
    path=a.source/phase/"run.json";verify_source(path);body=json.loads(path.read_text())
    assert body["peakConcurrency"]==1 and body["usageEstimated"] is False
print("recontract ok: sealed generations reusable; no calls")
