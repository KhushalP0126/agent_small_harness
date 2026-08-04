# Rust TUI and image renderer

This crate contains the optional Ratatui client. Python remains the source of
truth for the harness, model calls, validation engines, and artifacts.

The `src/mermaid_view.rs` module owns the image-processing path: Mermaid text
is rendered to SVG, rasterized to PNG pixels, and emitted through Kitty,
iTerm2, or the portable quadrant-block fallback selected from terminal
environment markers.

From the repository root:

```bash
make test-rust
make rust-tui REPO_ROOT=.
```

Equivalent direct commands use this manifest explicitly:

```bash
cargo test --manifest-path rust/Cargo.toml
cargo run --manifest-path rust/Cargo.toml -- .
```

The Rust preview is optional; `make test` and the Textual TUI do not require a
Rust toolchain.
