# Lawzilla practical review: gas-shunin-kou 2020 問13

- reviewedAt: `2026-07-09T21:39:39+09:00`
- machineReadable: `output/gas-shunin-all/review/lawzilla_mcp_feedback/20260709_213939_kou2020_remaining_gas_shunin_kou_2020_q13_lawzilla_false_positive_review.jsonl`
- verdict: Lawzilla candidates were false positives for this calculation item.

## Finding

- The question is a pipe-thickness calculation using `t=PD0/(2fη+0.8P)+C`.
- `P=1`, `D0=200`, `f=62`, `η=1.00`, `C=1` gives `t=2.602mm`, so `2.6mm` remains correct.
- High Pressure Gas Safety Act candidates returned by Lawzilla do not support the calculation and should not be attached as lawReferences.
