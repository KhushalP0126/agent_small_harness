# Architecture Map

Start with the directory that matches the question you are answering:

| Need | Start here | Owns |
| --- | --- | --- |
| Terminal interaction | `rust_tui/` | Ratatui UI, input handling, JSONL protocol, and native session visualizations |
| Model-driven work | `agents/` | Chat/spec orchestration, planning, repair, history, artifacts, and worker control |
| Code correctness | `engines/` | Static analysis, compilation, linting, structural IR, and validation gates |
| Runtime routing | `routing/` | Public entry points for the bridge, repository tools, path guard, sandbox, and tool registry |
| Installation | `setup/` | Bootstrap instructions, environment variables, and container prerequisites |
| Evidence | `docs/results/` | Published experiments and benchmark reports |

`scripts/` contains thin command-line entry points. `tests/` mirrors the
feature areas above. `data/` is versioned fixtures and task corpora; generated
artifacts remain ignored.

The current execution flow and ownership boundaries are maintained in this
document and rendered from typed runtime state in the TUI.

## Compatibility boundary

The older `harness_kernel/`, `backends/`, `prompt/`, `validation/`, `api/`, and
`TUI/` paths remain during migration so existing imports and external scripts
continue to run. New integration work should use the `routing/` entry points,
and new terminal work belongs in `rust_tui/`.
