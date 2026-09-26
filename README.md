# Agent Coder Structure

An approval-first research workbench for planning, generating, validating, and
reviewing code changes. It supports Python, C, C++, Rust, and JavaScript
projects without allowing model workers to edit the shared repository directly.

The default interface is a Rust terminal application backed by a Python
orchestration kernel. Work is represented as an immutable task graph: you
approve the graph, independent nodes run in isolated workspaces, and successful
changes wait in a serialized merge-review queue.

## Quick start

Requirements: Python 3.11+, Rust/Cargo, and optionally Docker or Podman for
container validation.

```bash
make setup
make test
make start
```

Optional verification and documentation-search dependencies are installed
through `make setup-formal`, `make setup-browser`, or `make setup-all`.

Inside the terminal UI, enter a normal request or use `/orchestrate <goal>` to
propose a supervised multi-agent graph. The graph does not run until its exact
revision hash is approved.

Useful commands:

| Command | Purpose |
| --- | --- |
| `make help` | Show the maintained command surface. |
| `make start` | Build and open the Rust terminal UI. |
| `make test` | Run the Python test suite. |
| `make test-peer-consultation` | Run unit and controller tests for bounded peer consultation. |
| `make test-rust` | Run Rust protocol and rendering tests. |
| `make test-coding-capability-peer PEER_PROVIDER=architect` | Run a capability experiment with one advisory peer. |
| `make check FILE=path` | Validate one source or project path. |
| `make clean-cache` | Remove generated local caches and fixture builds. |

Configuration and troubleshooting live in [setup/README.md](setup/README.md).
Copy `.env.example` to `.env` only when a provider needs local credentials;
`.env` and credential values are never versioned.

## What happens to a request

```text
goal
  -> candidate TaskGraph
  -> human approval of graph hash
  -> deterministic ready-node scheduling (default concurrency: 3)
  -> isolated read-only snapshot or editing workspace
  -> trusted validation
  -> serialized diff review and checkpointed merge
  -> append-only journal for resume or action-free replay
```

Important guarantees:

- Models and subagents do not write to the shared checkout.
- Graph revisions, commands, network use, and merges pass through centralized
  permission checks.
- Provider selection is fixed at dispatch; failures never trigger silent
  fallback.
- Tests written by an agent are diagnostic until separately approved as trusted.
- Events and artifacts are redacted before persistence; replay verifies hashes
  and performs no external actions.

### Bounded peer consultation

When the local `small_worker` makes no semantic or diagnostic progress, an
explicitly configured peer can provide one advisory memo before the next retry:

```bash
make test-coding-capability-peer PEER_PROVIDER=architect
```

The peer is a diagnostician, not an implementation worker: it receives a
redacted source excerpt capped at **3,200 characters**, may return at most
**3,000 characters**, and is asked for no more than **three** repair
hypotheses. Code-like replies are rejected, the peer receives no repository
tools, and the original worker remains responsible for any subsequent change.
The consultation record is included in the attempt journal so a reviewer can
inspect why the retry changed direction.

### What I learned building this

- A second opinion helps only when its authority is bounded: keeping the peer
  advisory preserves one accountable worker and the existing approval path.
- Stagnation needs observable evidence. The controller triggers consultation
  only after diagnostic or semantic progress stops, rather than on every
  failed attempt.
- Safety claims need executable checks. The feature has **4 focused boundary
  tests** and **2 controller-flow tests** covering packet limits, credential
  redaction, code-response rejection, and a successful advice-guided retry.

This is a mechanism-level result, not a claim of model-quality improvement.
Any resume statement should describe the implemented, tested controls rather
than an unmeasured increase in repair success.

## Verified result

The current local acceptance run passed **633 automated tests**: **601 Python**
tests (`make test`) and **32 Rust** terminal-protocol/UI tests (`make
test-rust`). The peer-consultation addition is covered by the six tests noted
above; this count is a reproducible engineering-quality metric, not a benchmark
claim about a provider's coding ability.

## Language support

`LanguageProfile` is the shared registry for aliases, file types, project
detection, build/test/lint commands, parsers, import analysis, and container
capabilities.

| Stack | Project marker | Standard validation |
| --- | --- | --- |
| Python | `pyproject.toml` | pytest, Pylint, AST/formal/behavior engines when installed |
| C | `CMakeLists.txt` | strict C11 build, CTest, structural/hazard checks |
| C++ | `CMakeLists.txt` | strict C++20 build, CTest, structural/hazard checks |
| Rust | `Cargo.toml` | `cargo check`, test, Clippy, structural checks |
| JavaScript | `package.json` | `node --check`, npm test, declared lint script |

Validation reports missing compilers, parsers, containers, lockfiles, or
dependencies as unavailable capabilities—not passing gates. Dependency download
and network access require separate approval.

## Repository map

| Path | Plain-language responsibility |
| --- | --- |
| `agents/` | Plans work and controls bounded generation/repair attempts. |
| `harness_kernel/` | Task graphs, scheduling, permissions, journals, validation, and merge review. |
| `routing/` | Stable public imports for bridge and repository tools. |
| `engines/`, `validation/` | Static, compiler, behavior, import, and policy checks. |
| `rust_tui/` | Interactive UI, protocol types, and rendering. |
| `scripts/` | Thin command-line entry points for experiments and maintenance. |
| `tests/fixtures/projects/` | Multi-language projects used by trusted tests. |
| `docs/results/` | Historical research evidence and its raw provenance. |

Start with [ARCHITECTURE.md](ARCHITECTURE.md) for execution boundaries and
[docs/FILE_INDEX.md](docs/FILE_INDEX.md) when locating a specific module.

## Orchestration and debugging

The terminal protocol includes graph/node state, attempts, artifacts, routing,
costs, merge review, breakpoints, and replay. Common slash commands include:

```text
/orchestrate <goal>   /agents      /trace       /attempts
/routing              /cost        /why         /events
/artifacts            /replay      /step        /break
```

Sessions can be paused, resumed, cancelled, revised, retried with an explicitly
selected provider, or replayed from their sanitized journal. Legacy `TaskIR`
requests compile to a one-node graph, and protocol-v6 events remain decodable.

## Development

Run the complete local acceptance set:

```bash
python3 -m pytest -q
cargo test --manifest-path rust_tui/Cargo.toml
cargo clippy --manifest-path rust_tui/Cargo.toml -- -D warnings
python3 -m compileall -q agents api backends engines harness_kernel prompt routing scripts validation
git diff --check
```

The suite is intentionally offline by default. Live provider, container, Linux,
and manual terminal checks are separate certification steps and must not be
inferred from unit-test success.

Public code should import bridge/tool entry points from `routing`. The older
`harness_kernel.tui_bridge` and `harness_kernel.tool_handlers` imports remain
as tested compatibility adapters for existing callers.

## Research archive

Published benchmark reports remain in place under [docs/results](docs/results/README.md).
They are immutable evidence, not current product claims. Each report links to
raw inputs and records provider health, failures, task corpus, and provenance.
Research procedures are documented in [docs/RESEARCH.md](docs/RESEARCH.md).

## Status

The workbench is supervised research software. Deterministic scheduling,
approval hashes, isolated proposals, trusted-test separation, persistence, and
replay are covered by automated tests. Hardware/toolchain availability and live
provider behavior vary by machine and are reported explicitly at runtime.
