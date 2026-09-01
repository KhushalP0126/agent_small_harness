# Architecture Map

## Runtime flow

```text
Rust TUI -> JSONL bridge -> approved TaskGraph -> deterministic scheduler
                                      |              |
                               event journal     isolated workspaces
                                      |              |
                                  replay       trusted validation
                                                     |
                                           serialized merge queue
```

The Rust UI presents state and sends typed commands. The Python bridge decodes
those commands, but permission and orchestration decisions belong to the kernel.
Every graph revision is immutable and must be approved by hash before dispatch.

## Ownership boundaries

| Need | Start here | Owns |
| --- | --- | --- |
| Graph schema | `harness_kernel/task_graph.py` | Nodes, revisions, hashes, DAG/path validation |
| Session execution | `harness_kernel/orchestration_runtime.py` | Ready ordering, concurrency, attempts, pause/retry/cancel |
| Persistence/replay | `harness_kernel/event_journal.py`, `orchestration_store.py` | Sanitized events, content-addressed blobs, resume state |
| Roles/permissions | `harness_kernel/roles.py`, `governance.py` | Typed role manifests and centralized capability checks |
| Project gates | `harness_kernel/project_validation.py`, `language_adapters.py` | Five-language profiles, trusted builds/tests, capability status |
| Reviewed changes | `harness_kernel/merge_queue.py`, `checkpoints.py` | Staleness checks, validation, serialized merge proposals |
| Terminal protocol | `harness_kernel/tui_bridge.py`, `terminal_bridge/commands.py` | JSONL events and readable command dispatch |
| Public integration | `routing/` | Stable lazy bridge/tool imports without circular dependencies |
| Terminal client | `rust_tui/` | Input/state loop, protocol models, presentation helpers |
| Generation/repair | `agents/` | Planning, bounded worker attempts, diagnostics, artifacts |
| Deterministic analysis | `engines/`, `validation/` | Parsing, compilation, lint, behavior, formal and policy gates |

## Mutation boundary

Read-only roles receive immutable snapshots. Editing roles receive temporary
workspaces, never the shared checkout. A successful workspace produces a diff;
it does not produce a merge. Merge review is single-threaded and verifies the
original hashes, path ownership, checkpoint, and trusted validation immediately
before applying an approved proposal.

Independent nodes may execute concurrently (three by default, configurable from
one to eight). Dispatch follows stable topological order, and provider routing is
assigned at dispatch so completion timing cannot alter later assignments.

## Persistence boundary

The JSON/JSONL journal is the source of truth. Events carry monotonic sequence,
session/revision/node/attempt identity, parent event, type, and SHA-256 artifact
references. Large payloads live in verified blobs. Replay consumes recorded
events only and performs no model, tool, network, or repository action.

## Compatibility boundary

New callers should import bridge and repository-tool entry points from
`routing`. Tested legacy imports under `harness_kernel` remain adapters for
existing scripts. Legacy `TaskIR` becomes a one-node graph, and the Rust decoder
continues to accept protocol-v6 events. `TUI/`, `benchmarker.py`, and
`history.json` are retained compatibility surfaces rather than new extension
points.

Generated caches and private `.env` data are ignored. Historical benchmark
evidence remains under `docs/results/` with raw provenance and is not used as a
substitute for current capability checks.
