#!/usr/bin/env python3
import argparse
from recontract import ARTIFACTS
p=argparse.ArgumentParser();p.add_argument("--expect-no-model-calls",action="store_true");p.add_argument("--require-audit-index-base");p.add_argument("--require-screen-count",type=int);p.add_argument("--require-scored-expand-count",type=int);p.add_argument("--require-building-unscored",type=int);a=p.parse_args()
assert a.expect_no_model_calls and a.require_audit_index_base=="one" and a.require_screen_count==10 and a.require_scored_expand_count==28 and a.require_building_unscored==4
assert not any(ARTIFACTS.glob("*/run.json"))
print("pre-call reaudit contract ok")
