# Development Workstreams

The project keeps one integration baseline on `main` and two focused Git
branches. They begin from the same verified commit because the Rust TUI, bridge,
and engine registry share typed protocol code. Branches guide ownership and
review; they do not duplicate or delete those runtime dependencies.

## `main-tui`

Owns the terminal product surface:

- `rust_tui/` rendering, input, protocol presentation, and terminal support.
- `harness_kernel/tui_bridge.py` JSONL integration and approval UX.
- TUI-specific tests, setup, and user documentation.

It consumes stable APIs through `routing/` rather than reaching into engine
implementations.

## `main-research`

Owns deterministic analysis and routing experiments:

- `engines/` static, compilation, state-flow, and tree-sitter analysis.
- `agents/engine_registry.py` language-to-engine routing.
- `routing/` public bridge/tool entry points and research-facing adapters.
- evaluator cases, benchmarks, artifacts, and research documentation.

Changes to the shared protocol or routing facade should land here first with
tests, then be merged into `main-tui` and `main` deliberately.

## Merge discipline

1. Keep each branch focused on its owning surface.
2. Run the relevant Python and Rust tests before merging.
3. Merge through `main` only after the shared JSONL protocol remains compatible.
