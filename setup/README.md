# Setup guide

This directory intentionally contains documentation only. Run setup from the
repository root through the Makefile; there is no separate shell installer.

## Fast path

```bash
make setup
make start REPO_ROOT=.
```

`make setup` creates a Python virtual environment, installs the project from
`pyproject.toml`, copies `.env.example` when necessary, and builds the Rust
TUI. Do not install from ad hoc requirement files; the Make targets below are
the maintained setup interface.

| Command | Installs |
| --- | --- |
| `make setup` | Core runtime and development dependencies |
| `make setup-formal` | Core plus Deal and CrossHair verification |
| `make setup-browser` | Core plus browser-backed documentation search |
| `make setup-all` | Core plus both optional capability groups |

## Optional services

- Install Ollama and pull `qwen2.5-coder:1.5b` for local chat and repository
  tools.
- Add `DEEPSEEK_API_KEY` to `.env` only when you want API-backed planning or
  architect escalation.
- Install Docker or Podman for default isolated generated-code execution.
- Use the matching `make setup-*` command when an optional capability is
  required. Existing environments can use `make install-formal` or
  `make install-kernel` without rebuilding the Rust client.

Use `make help` for the complete command list. Secrets, generated artifacts,
and local build output are intentionally excluded from Git.
