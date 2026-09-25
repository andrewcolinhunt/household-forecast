#!/usr/bin/env bash
#
# One-time post-install fixups. Invoked by `pixi run --locked setup` once after
# cloning. Idempotent: rerunning is cheap and safe.
#
# Add steps below as the project accretes post-install concerns.
# Keep each step guarded so the script is safe to rerun.

set -euo pipefail

# Pre-commit hooks: install if pre-commit is on PATH.
if command -v pre-commit >/dev/null 2>&1; then
    echo "setup: installing pre-commit hooks..."
    pre-commit install >/dev/null
fi

echo "setup: done."
