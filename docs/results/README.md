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
- `formal-counterexample-repair-postcondition-semantics-2026-08-15.md` — a
  separate, explicitly-labelled follow-up that clarified postcondition
  semantics. It retained the same Qwen 1.5B `nonnegative` failure twice, so it
  documents a rejected prompt intervention rather than a product claim.
- `formal-nonnegative-directive-2026-08-15.md` — six-repeat, one-task paired
  diagnosis of the targeted nonnegative directive. Both variants completed
  6/6; the directive removes the observed regression but uses 16 additional
  guided tokens and does not establish a general success-rate gain.
- `formal-counterexample-repair-nonnegative-directive-2026-08-15.md` and
  `formal-counterexample-repair-nonnegative-directive-followup-2026-08-15.md`
  — two independent three-repeat returns of that narrow directive to the
  complete eight-task corpus. Both variants completed 8/8 in every
  repetition; the guided path used 6.78% and 6.52% more mean tokens,
  respectively. This confirms removal of the known failure rather than a
  general counterexample-guided advantage.
- `formal-counterexample-repair-diverse-2026-08-15.md` — three-repeat,
  11-task follow-up that adds ordering, text-normalization, and loop fixtures.
  Baseline completed 10.00/11 while guided completed 9.33/11 and used 7.08%
  more mean tokens. All task-level failures, prompts, candidates, and
  CrossHair witnesses remain in the raw JSON.
- `formal-counterexample-repair-general-directive-2026-08-16.md` —
  three-repeat comparison of narrow repair guidance versus a single general
  verifier-aligned directive on that same 11-task corpus. The general
  directive averaged 6.67/11 versus 9.00/11 for narrow guidance, used 16.10%
  more mean tokens, and was slower; it is retained as negative evidence.
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
