# Rust TUI

This is the default terminal interface. Python remains the source of truth for
the harness, model calls, validation engines, and artifacts.

The `src/mermaid_view.rs` module owns the image-processing path: Mermaid text
is rendered to SVG, rasterized to PNG pixels, and emitted through Kitty,
iTerm2, or the portable quadrant-block fallback selected from terminal
environment markers.

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
