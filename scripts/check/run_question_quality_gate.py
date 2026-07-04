#!/usr/bin/env python3
"""Backward-compatible wrapper for the unified question-bank CLI."""

from __future__ import annotations

import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "tools" / "question_bank" / "question_bank.py"

runpy.run_path(str(TARGET), run_name="__main__")
