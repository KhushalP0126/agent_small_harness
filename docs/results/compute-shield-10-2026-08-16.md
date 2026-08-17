# Frozen Compute Shield rerun — 2026-08-16

> Raw evidence: [`raw/compute-shield-10-2026-08-16.json`](raw/compute-shield-10-2026-08-16.json) · Qwen 1.5B only

## Reproducibility

- Commit at capture: `8fac62612a359c1007d639cc14c87437e686858f` (working tree dirty only for documentation and output capture)
- Platform: macOS arm64 · CPython 3.11.9
- Corpus: `data/compute_shield_tasks_10.json`
- Corpus SHA-256: `76ece79b62f55910e5dda7dadc8d2d5382ff8f4dbeec4606ab307b32b5d8ed4a`
- Model: local `qwen2.5-coder:1.5b`

## Result

| Measure | Direct baseline | Shielded tool loop | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 10/10 | 10/10 | +0 |
| Model tokens | 5,606 | 21,345 | +15,739 |
| Tool calls | 0 | 19 | +19 |
| Aggregate wall-clock | 333.66 s | 432.77 s | +99.11 s |

The rerun restores 10/10 shielded completion, including the prior
`fix-doc-command` failure, but it does not support an efficiency claim. The
bounded tool loop consumed 3.81x the baseline model tokens and took longer on
this fixed corpus. This is a single local-model observation retained with every
task-level record; it does not supersede the earlier frozen result.
