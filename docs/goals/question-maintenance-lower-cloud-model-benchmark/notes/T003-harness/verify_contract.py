#!/usr/bin/env python3
import argparse
from contract import ARTIFACTS, AUDIT_MODEL, SCREEN_MODELS

p=argparse.ArgumentParser(); p.add_argument("--expect-no-model-calls", action="store_true"); a=p.parse_args()
assert a.expect_no_model_calls
assert SCREEN_MODELS == ["gpt-5.6-luna", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"]
assert AUDIT_MODEL == "gpt-5.6-sol"
assert not any(ARTIFACTS.glob("*/run.json")), "model-call artifact already exists"
print("pre-call contract ok")
