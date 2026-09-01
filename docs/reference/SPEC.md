# Rust TUI and Engine Integration Specification

The Rust TUI is the primary interactive interface. Python remains authoritative
for model calls, repair policy, validation, checkpoints, and research evidence.
The two processes communicate through protocol-versioned JSON lines.

## Session architecture

The unified session is a resumable state machine:

```text
clarify/spec -> contract queue -> Qwen generation -> validation -> Qwen repair
  -> constrained transform or typed symbol patch -> DeepSeek escalation
  -> validation -> reviewed-diff approval -> completed/manual review
```

Every transition is checkpointed and emitted as a typed
`repair_session_snapshot`. Resume continues the stored strategy and worker;
model output never bypasses deterministic gates. Repository mutations remain
behind explicit approval.

## Terminal interface

Ratatui renders standard terminal cells only. Native widgets show:

- the active repair stage, worker, strategy, and attempt budget;
- the primary failure, stable location, concrete witness, and edit ratio;
- static, behavior, profiling, and formal gate status;
- local/API context usage, accumulated API cost, and pending approvals;
- research-readiness score, evidence categories, and blockers;
- typed repository layers, files, symbols, variables, and dependency trees.

The interface does not require a browser, image protocol, Node process, or
diagram compiler.

## Protocol and safety

- Protocol version 6 adds secure settings, contribution, permission, context,
  checkpoint/rewind, branching, and extension-status events while protocol
  version 5 repair-session and research-readiness events remain decodable.
  preserving tolerant decoding of older events.
- Commands, subprocess output, and inherited controller events remain isolated
  so human-readable logs cannot corrupt structured messages.
- Qwen 1.5B is the default local worker. DeepSeek handles specification and
  final different-angle escalation.
- Typed patches replace exactly one existing module-level symbol, are parsed and
  applied in memory, and require successful validation plus user approval.
- Cancellation, checkpoint resume, tool diffs, and action approvals remain
  explicit typed commands.

## Validation

Python tests cover the controller, bridge, readiness evaluator, repository
renderers, and artifact compatibility. Rust tests cover protocol round trips,
state reduction, and native context rendering. `make research-readiness-record`
runs both suites and records their result for the authoritative readiness gate.
