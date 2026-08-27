# TUI Code Spec — `agent_small_harness`

> Audited 2026-07-30. This file specifies the Python Textual client. The
> Rust/Ratatui client is documented separately in `docs/reference/SPEC.md`.

Implemented Phase 1 + Phase 2 from the grounded specification. Phase 3 (extra
model providers, master-key mode, commercial tiering) is intentionally excluded
pending an explicit decision; nothing below assumes it.

---

## 0. Stack

- **Framework:** `textual` (Python)—matches the rest of the stack, with no new
  language or runtime.
- **Process model:** The TUI runs as a separate process from the harness. It
  never imports `GenerationController` or runs it in-process. It shells out to
  the existing CLI scripts (`run_coding_capability.py`, `run_worker_limit.py`,
  and `run_structured_spec.py`) as subprocesses and reads their JSON
  artifacts/checkpoints from disk. This keeps the TUI a pure consumer of
  already-tested interfaces instead of introducing a second, untested entry
  point into the control loop.
- **Data source:** JSON only (`ArtifactManager`, `Historian`)—no SQLite. If
  Storage's JSON-vs-SQLite question is revisited later, only the data-access
  module (§3) needs to change; screens do not touch storage directly.

---

## 1. Screens

### 1.1 `RunLauncherScreen` (default screen)

- Lists available spec/task entrypoints (`run_coding_capability`,
  `run_worker_limit`, and `run_structured_spec`) with their config source.
- The **Resume** action lists existing `run_id` values found under the artifact
  root (`ArtifactManager.root.iterdir()`) with a checkpoint present, and
  launches with `--resume-run <id>`.
- The model picker is scoped to what is real today: a two-item toggle,
  **Ollama (local)** / **DeepSeek (architect escalation)**—not a `/model`
  dropdown implying more providers than exist. See §5 for how this grows later
  without a rewrite.

### 1.2 `LiveRunScreen`

Two-pane layout, matching the original mockup's shape.

**Left pane—event log.** Tail the run's `checkpoint.json`, polling on a fixed
interval. Checkpoints are written after every attempt; see §4.1 for the fields
to render. Each new attempt renders as:

```text
[attempt N] draft_source_worker=<small_worker|architect_llm>
  static:   <compliant | N violations>
  behavior: <compliant | mismatch on case X>
  formal:   <compliant | skipped | N issues>
```

Do not render “static engines disabled” for the runtime-repair case. Use the
corrected copy in §1.2.1.

**Right pane—contract queue.** Only populate this pane when the run is a
`run_structured_spec` invocation. Render one row per
`ContractExecutionResult`:

```text
[✓] contract_name       (accepted)
[✕] contract_name       (validation_failed — N issues)
[…] contract_name       (dependency_blocked — waiting on: [...])
```

Source: the `contract_execution_results` list in the structured-spec
checkpoint (§4.2).

#### 1.2.1 Corrected status copy (do not deviate)

When a runtime-only failure is being repaired, the UI must not say static
checks are skipped. Use:

> Re-validating statically every attempt · repair prompt scoped to runtime failure

This is a direct, deliberate correction of the earlier draft's inaccurate
“static engines strictly disabled” language—`_scan(draft)` always runs; only
the repair-prompt content is scoped. Displaying the wrong claim would
misrepresent a safety property, so this line is specified exactly rather than
left to phrasing judgment.

### 1.3 `ArchitectureModal` (hotkey `M`)

- Calls `RepoMapAgent().map_repo(root)` and renders the typed graph directly.
  The default display groups the mapper output into human-scale top-level
  layers with module/function counts and cross-layer dependencies. A filter
  drills into a package or module. **Raw node tree** and **LLM plan context**
  remain explicit, fully offline diagnostic views.
- Root defaults to the repository itself. When viewing a completed run, default
  to the generated-output directory instead. The repo mapper has already been
  confirmed against generated output.

### 1.4 `ChangesModal` (hotkey `D`)—Phase 2

- File tree: the left pane lists files touched across the run's attempts.
- Diff view: the right pane displays a unified diff.
- The scoped backend method is:

  ```python
  diff_attempts(
      run_id: str,
      contract_name: str | None = None,
  ) -> list[DiffHunk]
  ```

  It uses `difflib.unified_diff` against successive `attempt_N.py`
  files or `repair_attempts[i]["source"]` values already written to the
  artifact directory. Do not add anything more elaborate—no AST diff and no
  semantic diff. A unified diff over stored source strings is sufficient and
  keeps this a small, contained addition.

### 1.5 `HistoryScreen`—Phase 2

- Lists past runs from `Historian` using
  `similar_past_attempts(task_signature, limit=3, minimum_score=0.25)`.
- When a new task is entered on `RunLauncherScreen`, surface matches as a
  **seen something like this before** hint panel.
- This is not a blocking gate. It is a shown hint consistent with the
  human-review-preserving design: informative, not decision-making.

---

## 2. Footer / hotkeys

```text
Q  Quit
R  Resume selected run
M  Architecture modal
D  Changes/diff modal
H  History screen
```

No **S: Settings** modal is specified yet. Nothing in the current config
surface (`config.yaml`'s `RoutingConfig`, `EnginesConfig`, and `FormalConfig`)
needs a live-editable UI. Treat it as Phase 3 if in-TUI configuration editing
is wanted later.

---

## 3. Data-access module (`TUI/data_source.py`)

All screens go through one module. No screen reads `artifacts/` or shells to a
script directly. This is the seam that makes a later JSON-to-SQLite migration,
if ever chosen, a one-file change.

```python
class HarnessDataSource:
    def list_runs(self) -> list[RunSummary]: ...
    def load_checkpoint(self, run_id: str) -> dict | None: ...
    def similar_past_attempts(
        self,
        task_signature: str,
    ) -> list[dict]: ...
    def repo_map(self, root: Path, fmt: str) -> str: ...
    def diff_attempts(
        self,
        run_id: str,
        contract_name: str | None = None,
    ) -> list[DiffHunk]: ...
    def launch_run(
        self,
        entrypoint: str,
        args: dict,
    ) -> subprocess.Popen: ...
    def resume_run(
        self,
        entrypoint: str,
        run_id: str,
    ) -> subprocess.Popen: ...
```

Responsibilities:

- `list_runs()` is the only new Phase 1 storage method (§4.3).
- `load_checkpoint()` wraps `ArtifactManager.load_checkpoint`.
- `similar_past_attempts()` wraps `Historian.similar_past_attempts`.
- `repo_map()` wraps `RepoMapAgent`.
- `diff_attempts()` is the small Phase 2 unified-diff addition from §1.4.
- `launch_run()` and `resume_run()` own subprocess construction so screens
  never assemble shell commands themselves.
- A future conversational or TaskIR authoring action must write its compiled
  input to a file and launch one of these existing CLI entrypoints. It must
  never import or call `GenerationController.run()` inside the TUI process.

---

## 4. Real interfaces this depends on (verified, not assumed)

### 4.1 Checkpoint payload

`ArtifactManager.checkpoint(session, paths)` writes `checkpoint.json`. The
session dictionary includes, at minimum, `attempts: list[dict]`, with each
attempt carrying:

- `draft_source_worker`
- `validation` (static findings + compliance)
- `behavior_validation`
- `execution_trace`
- `formal_validation`

These fields are present through the `session.attempts.append(attempt)` call
sites in `generation_controller.py`.

### 4.2 Structured-spec checkpoint

The same `checkpoint()` call is used, but the session payload additionally
carries the contract-queue result list. Each `ContractExecutionResult` contains:

- `name`
- `status`
- `source`
- `issues`
- `prompt_size`
- `dependencies`
- `repair_attempts`

This data is available once `run_structured_spec.py`'s `--resume-run` support is
in play.

### 4.3 `list_runs()`—implemented Phase 1 addition

`ArtifactManager` has `load_checkpoint(run_id)`, which requires knowing the ID
already, but has no enumeration method. Add:

```python
def list_runs(self) -> list[str]:
    """Run IDs with a checkpoint present, most recent first."""
    return sorted(
        (
            path.name
            for path in self.root.iterdir()
            if (path / "checkpoint.json").is_file()
        ),
        key=lambda name: (
            self.root / name / "checkpoint.json"
        ).stat().st_mtime,
        reverse=True,
    )
```

This method is implemented on `ArtifactManager` and consumed only through
`HarnessDataSource`.

---

## 5. Growth path for `/model` (Phase 3, not built here)

`HarnessDataSource` should expose model selection as:

```python
available_providers() -> list[str]
```

Initially it returns:

```python
["ollama", "deepseek_architect"]
```

Adding GLM, KLM, OpenAI, or Anthropic later means adding entries to this one
method plus a corresponding `backends/` client. The TUI screens do not need to
change because they render whatever this method returns. This is deliberate:
Phase 3 providers can be added without touching the TUI code, so there is no
reason to speculatively build UI for providers that do not exist yet.

---

## 6. Explicitly out of scope

- SQLite migration—not needed for any Phase 1/2 screen above; JSON is
  sufficient.
- Master Key / research mode—no gating logic appears anywhere in this spec;
  all panels are visible by default.
- Commercial tiering—no billing, authentication, or usage metering appears
  anywhere in this document.

If any of these three are approved, they are new specifications rather than
additions to this one. Keeping them separate is what makes this specification
buildable without waiting for decisions about them.
