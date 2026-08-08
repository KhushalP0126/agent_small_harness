# Frozen Compute Shield experiment — 2026-08-04

The frozen ten-task corpus in `data/compute_shield_tasks_10.json` was run with
the local `qwen2.5-coder:1.5b` model. Both sides used the same task inputs; the
shielded side used the bounded repository tool loop.

| Measure | Direct baseline | Shielded tool loop |
| --- | ---: | ---: |
| Successful tasks | 10/10 | 9/10 |
| Model tokens | 5,606 | 16,973 |
| Aggregate wall time | 262.4s | 153.4s |

Compute Shield delta is **-11,367 tokens**: the shielded loop used about 3.03x
the recorded model tokens in this run. The one shielded failure was
`fix-doc-command`; it used 6,869 tokens versus 203 for the direct baseline.
This is an honest diagnostic result, not evidence that local tool routing saves
tokens on this corpus. The report was produced by
`scripts/run_compute_shield_experiment.py`; rerun it only on hardware that can
sustain the 1.5B model.
