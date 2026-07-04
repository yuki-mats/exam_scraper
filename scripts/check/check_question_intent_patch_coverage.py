#!/usr/bin/env python3
"""Backward-compatible wrapper for the question-bank checker."""

from __future__ import annotations

import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "tools" / "question_bank" / "checks" / "check_question_intent_patch_coverage.py"

runpy.run_path(str(TARGET), run_name="__main__")
