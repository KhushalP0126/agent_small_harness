# Changelog

## v0.1.0 — 2026-07-31

Initial tagged release of the generalized code-generation and repair harness.

### Added

- Rust Ratatui workflow with chat, typed clarification questionnaires, spec
  drafting, an explicit review gate, and approved execution.
- Number-key questionnaire choices with a mandatory free-text `Other` path.
- Validated DeepSeek JSON execution sheets rendered into deterministic
  planner-compatible specifications, with a safe sheet-derived queue fallback
  when optional contract ordering is malformed.
- Bottom-following execution output with keyboard and mouse-wheel history
  scrolling, plus an automatically opened, reusable validated-source code view.
- DeepSeek configuration visibility and bounded local preference memory without
  displaying or persisting credentials.
- Navigable repository map, per-file details, run history, and cross-platform
  Mermaid rendering with native terminal protocols plus a quadrant fallback.
- Defense-in-depth local execution for generated Python using sanitized child
  environments, disposable working directories, bounded output, process-group
  timeouts, and supported POSIX resource limits.
- A bounded Qwen/DeepSeek repository tool loop with typed search, bounded file
  reads, review-only search/replace diffs, isolated script execution, shared
  path-escape protection, and stale-diff checks at explicit approval time.
- Rust TUI integration for repository tool tasks, streamed tool-call status,
  assistant results, and a blocking `y`/`n` diff approval modal.
- Fail-closed Docker/Podman generated-source execution, typed Python/C/C++/Rust/
  JavaScript adapters, and an explicit trusted-local fallback.
- A 20-task paired coding-agent benchmark for measuring outcomes, tool calls,
  retries, duration, and local-agent versus baseline token consumption.
- `execute_script` now uses the hardened Docker sandbox by default; local mode
  requires an explicit caller selection.
- Structured contract planning, sequential small-worker execution, repair and
  architect escalation, checkpoint artifacts, and validation evidence.

### Security boundary

The local generated-code runner is intended for trusted local development. It
does not deny absolute host filesystem paths or network access. Use a hardened
container or OS sandbox before executing adversarial code or deploying the
harness as a shared service.
