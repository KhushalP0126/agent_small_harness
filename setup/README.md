# Setup guide

This directory intentionally contains documentation only. Run setup from the
repository root through the Makefile; there is no separate shell installer.

## Fast path

```bash
make setup
make start REPO_ROOT=.
```

`make setup` creates a Python virtual environment, installs base
dependencies, copies `.env.example` when necessary, and builds the Rust TUI.

## Optional services

- Install Ollama and pull `qwen2.5-coder:1.5b` for local chat and repository
  tools.
- Add `DEEPSEEK_API_KEY` to `.env` only when you want API-backed planning or
  architect escalation.
- Install Docker or Podman for default isolated generated-code execution.
- Run `make install-formal` only for Deal/CrossHair checks.

Use `make help` for the complete command list. Secrets, generated artifacts,
and local build output are intentionally excluded from Git.
