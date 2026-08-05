#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/KhushalP0126/agent_small_harness.git"
INSTALL_DIR="${1:-${HOME}/agent_small_harness}"

need_command() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Missing required command: %s\n' "$1" >&2
        exit 1
    }
}

need_command git
need_command python3
need_command cargo

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    printf 'Updating %s\n' "${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" pull --ff-only
elif [[ -e "${INSTALL_DIR}" ]]; then
    printf 'Install path exists but is not a git checkout: %s\n' "${INSTALL_DIR}" >&2
    exit 1
else
    printf 'Cloning into %s\n' "${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
make setup

if [[ -t 0 && -z "${DEEPSEEK_API_KEY:-}" ]] && ! grep -q '^DEEPSEEK_API_KEY=' .env 2>/dev/null; then
    read -r -p 'DeepSeek API key (optional; press Enter to skip): ' api_key
    if [[ -n "${api_key}" ]]; then
        printf 'DEEPSEEK_API_KEY=%s\n' "${api_key}" >> .env
        printf 'Saved DeepSeek key to .env\n'
    fi
fi

printf 'Building the Rust TUI\n'
cargo build --release --manifest-path rust/Cargo.toml

cat <<'EOF'

Install complete.

To launch:
  cd <install-directory>
  source .venv/bin/activate
  make rust-tui REPO_ROOT=.

The local worker defaults to qwen2.5-coder:1.5b.
EOF
