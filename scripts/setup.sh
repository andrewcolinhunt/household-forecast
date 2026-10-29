#!/usr/bin/env bash
#
# One-time post-install fixups. Invoked by `pixi run --locked setup` once after
# cloning. Idempotent: rerunning is cheap and safe.
#
# Add steps below as the project accretes post-install concerns.
# Keep each step guarded so the script is safe to rerun.

set -euo pipefail

repo=$(git rev-parse --show-toplevel)
hook=$(git rev-parse --git-path hooks/pre-push)
source="$repo/scripts/pre-push"
if [[ -e "$hook" || -L "$hook" ]]; then
    if [[ ! -L "$hook" || "$(readlink "$hook")" != "$source" ]]; then
        echo "setup: an existing pre-push hook needs manual integration with scripts/pre-push." >&2
        exit 1
    fi
else
    mkdir -p "$(dirname "$hook")"
    ln -s "$source" "$hook"
fi
chmod +x "$source"

# Pre-commit hooks: install if pre-commit is on PATH.
if command -v pre-commit >/dev/null 2>&1; then
    echo "setup: installing pre-commit hooks..."
    pre-commit install >/dev/null
fi

echo "setup: done."
