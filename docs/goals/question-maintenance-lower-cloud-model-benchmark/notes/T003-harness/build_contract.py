#!/usr/bin/env python3
import argparse
from contract import EXPAND_IDS, SCREEN_IDS, items

p = argparse.ArgumentParser(); p.add_argument("--dry-run", action="store_true"); args = p.parse_args()
assert args.dry_run
assert len(SCREEN_IDS) == 10 and len(EXPAND_IDS) == 32 and EXPAND_IDS[:2] == SCREEN_IDS[:2]
assert set(SCREEN_IDS) <= set(EXPAND_IDS)
assert [x["id"] for x in items(SCREEN_IDS)] == SCREEN_IDS
assert [x["id"] for x in items(EXPAND_IDS)] == EXPAND_IDS
print("contract ok: screen=10 expand=32 image_excluded=2")
