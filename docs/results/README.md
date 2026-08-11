# Results

This directory holds published, reproducible experiment reports. They are
evidence, not product claims: each report records its model, task corpus,
commands, dates, and failures.

The full protocol, repeat-run command, and publication checklist are in
[`../RESEARCH.md`](../RESEARCH.md).

`raw/` holds compact, versioned benchmark JSON and approval-reviewed session
receipts. Use `make research-report REPORT_INPUT=... REPORT_OUTPUT=...` to
render a dated Markdown summary without hiding failed task rows.

- `compute-shield-10-2026-08-04.md` — frozen 1.5B Compute Shield comparison.
- `qwen-capability-results-2026-07-18.md` — local Qwen capability runs.
- `gemma-deepseek-capability-results-2026-07-19.md` — Gemma and DeepSeek runs.
- `snake-pong-execution-report-2026-07-19.md` — structured-spec execution
  evidence.
- `additional-harness-results-2026-07-24.md` and
  `structured-spec-repo-map-results-2026-07-24.md` — follow-up benchmark and
  repository-map evidence.
