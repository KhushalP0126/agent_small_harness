# agent_small_harness — Rust TUI + Engine Expansion Spec

> Audited against the implementation on 2026-07-30. This is an implemented
> subsystem specification; rollout language is retained as design context.

## Implementation status

Implemented in this tree with two architecture-grounded clarifications:

- The real engine registry lives in `agents/engine_registry.py`, so the strict
  C/C++ compilation gate is registered there before optional tree-sitter
  structural engines. Profiling stays an opt-in behavioral comparison and
  Compute Shield stays an experiment metric; neither masquerades as a default
  AST gate.
- `mermaid-rs-renderer` 0.3.1 returns SVG. The Rust modal therefore uses its
  real `render` API, rasterizes the SVG with `usvg`/`resvg`/`tiny-skia`, then
  directly emits Kitty/iTerm2 graphics or converts the decoded image to
  colored Unicode quadrant cells. No browser, Node process, protocol query, or
  `ratatui-image` dependency is required.
- `GenerationController` now owns the profiling integration seam and persists
  its result with each attempt. `ArtifactManager` retains profiling and
  execution evidence, the Textual console displays profiling state, and a
  dedicated inherited event descriptor carries typed controller events to the
  Rust bridge without mixing them into CLI stdout.
- Compute Shield consumes paired `ArtifactManager` telemetry through
  `scripts/run_compute_shield.py`; it does not rerun models or become an
  acceptance gate.
- The Rust prompt surface is a five-state workflow: `Chat`, `Questionnaire`,
  `DraftingSpec`, `SpecReview`, and `Executing`. Chat and clarification are
  non-mutating, drafting produces a reviewable markdown spec, and only explicit
  `y` approval can launch workers.
  A bounded ignored JSON file stores only explicit non-secret preferences.
- Terminal raw mode and alternate-screen ownership are protected by an RAII
  drop guard plus panic hook, so both ordinary errors and unwinding panics
  restore the user's terminal.

The Textual TUI remains supported. Use `make rust-tui` for the Rust preview and
`make test-rust` for its automated checks.

## 1. Scope

Two independent workstreams, sharing one integration point (the JSON-lines
event protocol):

1. **Rust TUI** — replaces the Textual TUI as the primary interface, adds
   native terminal-graphics rendering of function/variable Mermaid diagrams.
2. **Engine expansion** — Compilation Gate, Algorithmic Profiling Engine,
   Compute Shield experiment. Shared utilities live in `harness_kernel`; the
   compilation gate lives with the other engines and is registered through the
   real `agents.engine_registry` boundary.

The existing Textual TUI stays running as the interim driver until the Rust
TUI reaches parity (see §5, Rollout).

---

## 2. Rust TUI Architecture

### 2.1 Process model

- Harness stays Python (`harness_kernel`), run as a subprocess.
- Rust owns the terminal; Python owns engine logic, LLM calls, and IR.
- Commands and bridge output use JSON-lines over stdin/stdout. Controller
  events use a separate inherited JSON-lines file descriptor so human CLI logs
  cannot corrupt typed events. No HTTP server or port is introduced.

### 2.2 Crate stack

| Component               | Crate                    | Purpose |
|--------------------------|---------------------------|---------|
| TUI core                 | `ratatui` + `crossterm`   | Immediate-mode rendering, input |
| Async runtime             | `tokio`                   | Non-blocking event loop, subprocess I/O |
| Terminal image rendering  | local renderer + `base64` | Direct Kitty/iTerm2 output, cached 2×2 quadrant fallback |
| Mermaid rendering          | `mermaid-rs-renderer` (`mmdr`) | In-process `.mmd` → PNG, no Node/browser |
| Serialization              | `serde` / `serde_json`   | Event (de)serialization |
| Error handling              | `anyhow`                 | Propagation across async tasks |

### 2.3 Event loop

`tokio::select!` across three sources, each independent so none can block
the others:

- **Terminal input** (`crossterm::EventStream`) — keys/mouse, handled
  immediately.
- **Harness events** (`mpsc` channel fed by a background task reading the
  subprocess's stdout line-by-line) — engine progress, logs, results.
- **Redraw tick** (`tokio::time::interval`, ~30fps) — bounds repaint rate so
  the UI doesn't redraw on every single event.

### 2.4 Mermaid / function-graph view

- `RepoMapAgent` emits `.mmd` source (flowchart/classDiagram syntax — stick
  to these two, `mmdr`'s most mature coverage).
- `mermaid-rs-renderer` renders SVG in-process; `usvg`/`resvg`/`tiny-skia`
  rasterize it to PNG without a subprocess.
- Environment markers select Kitty graphics, iTerm2 inline images, or the
  universal quadrant-block renderer. Native protocol encoders write the PNG
  directly; the fallback downsamples it to two horizontal and two vertical
  samples per terminal cell and renders a two-color quadrant glyph.
- The same `m` request eagerly returns typed per-file records. `Up`/`Down`
  changes a Rust-owned selection without IPC; `t` and `r` switch the detail
  pane between summary/symbol and import/variable data.
- Regenerate on: `m` hotkey press, or after a draft is accepted and the
  function/variable graph changes.

### 2.5 Chat, specification, and execution state machine

- `Chat`: `c`/`p` opens input and Enter sends a conversational architect
  request. No subprocess execution is reachable from this transition.
- `Questionnaire`: a project-concept response carries 2–4 typed clarification
  questions. Each contains 2–4 worker-provided choices plus an application-added
  `Other` option. `1`–`5` records answers in Rust; `Other` accepts free text.
  Completing the last question sends all answers once and automatically enters
  `DraftingSpec` without starting the execution pipeline.
- `DraftingSpec`: `s` asks the architect to fill a strict JSON execution sheet
  from the bounded conversation, questionnaire answers, and saved preferences.
  The bridge validates every required field and renders canonical Markdown
  sections for files, components, entrypoints, dependencies, state rules,
  interfaces, constraints, acceptance examples, and validation. Model-authored
  free-form Markdown never crosses into the planner. This mode is also entered
  automatically after questionnaire completion.
- `SpecReview`: the generated specification is shown in a blocking overlay.
  `y` approves it; `n`/`Esc` returns to chat without executing.
- `Executing`: the approved spec alone is written to the temporary spec file
  and passed to `run_structured_spec.py`.

The primary output pane preserves typed role prefixes and renders user,
assistant, memory, warning, and error traffic with separate colors. Active panes
use a cyan border/title accent; inactive utility panes are dimmed. The 33 ms UI
tick advances a status-strip spinner while asynchronous chat, drafting, or
execution work is active, so backend work never blocks terminal input.

The output viewport follows the newest physical log line by default and owns a
Rust-side offset for keyboard and mouse-wheel navigation. `Up`/`Down` and
`PageUp`/`PageDown` move through retained output, `Home` reaches the oldest
retained line, and `End` restores live following. New events preserve the
current history position when the user is not following the tail.

On a completed run only, `run_structured_spec.py` emits a typed
`validated_source` event after all engine, formal, structured-spec, import, and
integration-smoke decisions are finalized. The TUI automatically opens the
line-numbered source modal and retains it for `v` to reopen. Failed or
manual-review candidates are never presented as validated source.

Typed questionnaire options use `1`–`5` as direct local selections; only the
final answer crosses the JSONL boundary. Plain assistant responses expose
quick-reference selection only when at least two lines use an explicit numbered
prefix (`1.`, `2)`, etc.). Numbers remain ordinary characters for all other
responses and while typing general chat text.

Bridge startup emits a typed configuration status naming the environment or
`.env` key source without its value. Local `.tui_memory.json` contains at most
50 explicit preferences and is excluded from git. Conversation history is
bounded to 24 messages and remains process-local.

The rendered sheet guarantees that `PlanModeAgent` can derive a local contract
queue before approval. DeepSeek may refine its ordering and dependency notes;
if that optional compact planner response is invalid, the harness retains the
validated sheet-derived queue and reports a warning instead of failing the run.

### 2.6 Deliverables from this repo turn

- `Cargo.toml` — dependency manifest
- `src/main.rs` — event loop, subprocess spawn, harness event enum, base UI
- `src/mermaid_view.rs` — Mermaid modal widget
- `src/protocol.rs` — typed command/event schema and resilient line reader
- `harness_kernel/tui_bridge.py` — CLI launcher and protocol bridge
- `harness_kernel/event_stream.py` — inherited controller-event sink

`mermaid-rs-renderer` is pinned to 0.3.1 and the implementation uses its
verified `render(&str) -> Result<String>` API.

---

## 3. Engine Expansion

All three live in `harness_kernel`'s `EngineRegistry`, independent of the
TUI. Each reports results as a new JSON-lines event type so either TUI
(Textual now, Rust later) can consume them.

### 3.0 Local Generated-Code Boundary

- Generated Python never receives the parent shell environment or repository
  `.env`; the child receives a small allowlist plus explicit smoke-test flags.
- Behavior cases, structured-contract examples, and assembled-program smoke
  tests use disposable working directories rather than the repository root.
- The local runner uses Python isolated mode, bounded stdout/stderr capture,
  wall-clock and process-group termination, and POSIX CPU/file/core/address-space
  limits when supported.
- This is defense in depth for trusted local generation, not a hardened boundary:
  a container or OS sandbox is still required to deny absolute host filesystem
  and network access for adversarial code.

### 3.0.1 Repository Tool Boundary

- `search_directory`, `read_file`, `apply_search_replace`, and
  `execute_script` are typed `ToolHandler` registrations rather than direct
  model access to Python or the filesystem.
- Every requested root and path resolves through one repository-root guard.
  Absolute paths, traversal, and symlinks that resolve outside that root fail
  with the stable `path_escape` error kind.
- Search results, file reads, script source, captured output, execution time,
  and model tool turns are bounded. Script imports use a standard-library
  allowlist before the disposable local runner is invoked.
- `apply_search_replace` only returns a unified diff and proposed content. The
  model-facing loop cannot write it. A separate host/TUI approval call may
  apply it, but only if the file's SHA-256 still matches the reviewed version;
  otherwise it fails as `stale_diff`.
- Qwen is the default tool selector and DeepSeek is optional. Both receive the
  same four-tool contract and feed each typed result into the next bounded
  decision turn.

This boundary reduces accidental repository and secret exposure but remains a
trusted-local-development control. It is not a hardened kernel, container, or
network sandbox for adversarial model-generated Python.

### 3.1 C/C++ Compilation Gate

- Wires a strict `gcc`/`clang` subprocess pass into the existing gate
  sequence, ahead of the behavior sandbox.
- Rejects drafts that fail compilation before they reach behavioral
  validation — cheaper failure, earlier in the pipeline.
- Event: `{"type": "compile_gate_result", "status": "pass"|"fail", "errors": [...]}`

### 3.2 Algorithmic Profiling Engine

- New behavioral gate (not structural) — measures runtime and cache
  efficiency of generated logic rather than just AST shape.
- Target use case: hardware-simulation/scheduling tasks (e.g. the "RISCA"
  project) where loop order matters for performance, not just correctness.
- Explicitly evaluates MKN vs NKM loop orderings; rejects drafts that pass
  functional checks but pick the slower order, forcing a rewrite.
- Event: `{"type": "profiling_result", "loop_order": "MKN"|"NKM", "runtime_ns": ..., "cache_misses": ...}`
- Integration: opt-in `GenerationController(profiling_runner=...)`; disabled
  attempts remain compliant and unchanged, while enabled failures enter the
  existing repair/manual-review path.

### 3.3 Compute Shield Experiment

Three-phase token-savings measurement, not a gate — an evaluation harness
around the existing pipeline.

| Phase | Description |
|-------|-------------|
| 1 — Baseline | Route 10 structural tasks directly to a DeepSeek-v4-pro architect; sum total API token consumption. |
| 2 — Harness Shield | Route the same 10 tasks through a local Qwen2.5-Coder worker first, using deterministic gates (Math, Hazards, Bounds, Lint) to auto-repair drafts locally before any architect escalation. |
| 3 — Metrics & Margin | Compute the exact token delta between phases 1 and 2 — proves how much computational load the local model absorbed before escalating to the paid architect. |

Event: `{"type": "compute_shield_metrics", "phase": 1|2|3, "tokens_baseline": ..., "tokens_shielded": ..., "delta": ...}`

Phase-three aggregation is implemented against recorded artifact telemetry via
`make compute-shield COMPUTE_SHIELD_ARGS="..."`. Running the live ten-task
phase-one/phase-two experiment remains an explicit, potentially paid action.

---

## 4. Testing Plan

### 4.1 Rust TUI

- **Unit** — `AppState` transitions given a fixed sequence of `HarnessEvent`
  values (no terminal, no subprocess); pure state-machine tests.
- **Protocol contract** — round-trip every event variant through
  `serde_json` (serialize → deserialize → equality) so a schema change on
  either side of the stdio boundary fails loudly.
- **Subprocess integration** — spawn a stub Python script that emits a
  canned sequence of JSON lines (including malformed lines) and assert the
  reader task parses valid lines and skips/logs invalid ones without
  crashing.
- **Mermaid rendering** — feed `mermaid_view::set_diagram()` a fixed set of
  `.mmd` fixtures (small flowchart, small classDiagram, one deliberately
  unsupported diagram type) and assert success/graceful-failure
  respectively — don't assert on rendered pixel output, that's brittle.
- **Terminal-graphics compatibility** — manual pass across at least Xterm
  (Sixel), Kitty (Kitty protocol), and one terminal with no graphics
  support (confirm halfblock fallback renders something, not a crash).
- **Responsiveness regression** — with a synthetic harness stub that emits
  events on a tight loop, confirm keypresses (e.g. quit) are handled within
  one redraw tick even under event flood — this is the actual metric that
  matters for the "make it more responsive" goal.

### 4.2 Compilation Gate

- Fixture drafts: valid C/C++, syntax error, type error, valid-but-warns.
- Assert `status`/`errors` shape matches the event schema exactly.
- Confirm gate runs *before* behavior sandbox in the pipeline order (an
  ordering test, not just a correctness test).

### 4.3 Algorithmic Profiling Engine

- Known-loop-order fixtures where MKN vs NKM has a measurable, reproducible
  runtime/cache difference on the target hardware profile — avoid fixtures
  where the difference is noise-level, since flaky perf tests erode trust
  in the gate.
- Assert the engine rejects the slower-but-correct variant and accepts the
  faster one.
- Track measurement variance across repeated runs (e.g. 5 runs, report
  spread) — a profiling gate needs a documented noise floor or it will
  intermittently reject good drafts.

### 4.4 Compute Shield

- Freeze the same 10 structural tasks across phases 1 and 2 (identical
  inputs) so the token delta reflects the shield's effect, not task
  variance.
- Log raw token counts per task per phase, not just the aggregate delta —
  needed to debug outlier tasks where the shield underperforms.
- Re-run periodically as gates/models change, since the delta is only
  meaningful relative to the current gate set and worker model version —
  treat old Compute Shield numbers as stale once either changes.

---

## 5. Rollout

1. Build engines (§3) against the existing Textual TUI first — they're
   UI-independent, so this validates them without waiting on the Rust side.
2. Build the Rust TUI (§2) in parallel, using a stub harness event stream
   for early UI testing before wiring the real subprocess.
3. Extend the JSON-lines bridge to include the three new engine event
   types as they land.
4. Cut over to the Rust TUI once it reaches parity: subprocess bridge
   stable, Mermaid modal working, dashboard showing engine + Compute Shield
   results.
5. Retire the Textual TUI only after cutover — don't remove it while the
   Rust TUI is still catching up, or you lose the ability to drive the
   harness interactively in the gap.
