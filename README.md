# financial-model

A financial model for personal and family financial planning.

## Setup

Prerequisites: [Pixi](https://pixi.sh).

```bash
pixi install --locked
pixi run --locked setup    # installs pre-commit hooks
```

## Common tasks

```bash
pixi run --locked test             # all tests
pixi run --locked test-unit        # unit tests only
pixi run --locked test-integration # integration tests (parallel)
pixi run --locked fmt              # ruff format + lint with auto-fix
pixi run --locked docs-build       # build HTML docs
pixi run --locked docs-serve       # serve docs at http://localhost:8000
pixi run --locked docs-clean       # remove built docs
```

## Layout

```
src/financial_model/   # package source (src layout, py.typed)
tests/financial_model/ # tests mirror the src tree
docs/                  # Jupyter Book 2 / MyST
scripts/setup.sh       # idempotent post-install fixups
AGENTS.md              # shared instructions for coding agents
CLAUDE.md              # Claude Code adapter importing AGENTS.md
GEMINI.md              # Gemini CLI adapter importing AGENTS.md
skills/                # canonical Agent Skills packages
.agents/skills/        # cross-agent discovery symlinks
.claude/skills/        # Claude Code discovery symlinks
.claude-plugin/        # Claude Code distribution adapter
_dev/                  # gitignored symlink → ~/git/notes/<project>/
```

## Notes / design docs

Design notes, scratch, and tool-specific personal overrides live in
`~/git/notes/financial-model/`, symlinked into the repo as `_dev/` and
`CLAUDE.local.md`. Both are gitignored.

## CI

Lint, pre-commit, and tests run on every push and PR via
`.github/workflows/ci.yaml`.
