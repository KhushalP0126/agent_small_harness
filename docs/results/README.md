# Results

This directory holds published, reproducible experiment reports. They are
evidence, not product claims: each report records its model, task corpus,
commands, dates, and failures.

The full protocol, repeat-run command, and publication checklist are in
[`../RESEARCH.md`](../RESEARCH.md).

`raw/` holds compact, versioned benchmark JSON and approval-reviewed session
receipts. Use `make research-report REPORT_INPUT=... REPORT_OUTPUT=...` to
render a dated Markdown summary without hiding failed task rows.

- `formal-counterexample-repair-2026-08-15.md` — three-repeat Qwen 1.5B
  counterexample-guided repair study over eight Python contracts. The guided
  variant completed 7.67/8 tasks versus 8.00/8 baseline, with 6.73% more mean
  model tokens; the raw JSON retains the one guided failure.
- `compute-shield-10-2026-08-04.md` — frozen 1.5B Compute Shield comparison.
- `local-model-comparison-2026-08-11.md` — controlled Qwen 1.5B versus 3B
  comparison on the same frozen ten-task corpus. Both completed 10/10
  shielded tasks; retain the linked raw JSON rather than inferring a scaling
  law from the two-model observation.
- `qwen-capability-results-2026-07-18.md` — local Qwen capability runs.
- `gemma-deepseek-capability-results-2026-07-19.md` — Gemma and DeepSeek runs.
- `snake-pong-execution-report-2026-07-19.md` — structured-spec execution
  evidence.
- `additional-harness-results-2026-07-24.md` and
  `structured-spec-repo-map-results-2026-07-24.md` — follow-up benchmark and
  repository-map evidence.
