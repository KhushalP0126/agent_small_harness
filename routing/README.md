# Routing

Use this small package as the stable entry point for integration work:

- `bridge.py` exposes the JSONL bridge used by `rust_tui/`.
- `tools.py` exposes the typed repository tool registry.

The implementation remains under `harness_kernel/` during the compatibility
migration. New callers should import from `routing` rather than depending on
internal module paths. It is intentionally a thin compatibility surface, not a
second orchestration layer.
