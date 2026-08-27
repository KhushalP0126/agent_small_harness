# Rust TUI

This is the default terminal interface. Python remains the source of truth for
the harness, model calls, validation engines, and artifacts. The TUI consumes
the bridge's JSONL events, renders normal assistant Markdown (including tables)
in the stream, and can open the repository README with `/readme`.

Repair timelines, validation gates, context usage, repository maps, and research
readiness are rendered directly with Ratatui widgets. No browser, image
protocol, or diagram compiler is required.

From the repository root:

```bash
make test-rust
make start REPO_ROOT=.
```

Equivalent direct commands use this manifest explicitly:

```bash
cargo test --manifest-path rust_tui/Cargo.toml
cargo run --manifest-path rust_tui/Cargo.toml -- .
```

Use the legacy Textual interface only for artifact review with `make tui`.
`make test` does not require a Rust toolchain.

The interface uses standard terminal cells and should behave consistently in
Apple Terminal, Kitty, iTerm2, and WezTerm.
