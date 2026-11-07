# AGENTS.md

Project conventions for contributors (human and AI).

## Product purpose

This tool helps the household understand how decisions such as moving or
changing jobs affect current cash, future wealth, and retirement options.
Simplifi handles expense and account tracking. Develop this app around comparing
decisions and explaining their consequences; imports serve that planning work.
The app compares a single year and forecasts saved Plans through retirement,
with explicit account, benefit, economic, and future-tax assumptions. See
`docs/concepts/decision-planning.md`.

## Committed content

Committed code, tests, documentation, and generated artifacts must make sense
from a fresh checkout and references available to the repository's intended
audience. Do not cite private or uncommitted planning documents by path,
title, identifier, or section number. State the relevant behavior, constraint,
or rationale directly, or reference documentation available to that audience.

Use portable paths and synthetic example identities, and omit incidental
personal or organizational metadata. Preserve required attribution, copyright
and license notices, intentionally published project metadata, and the meaning
and validation of evidence. Do not rewrite immutable evidence or weaken
identity checks to satisfy this rule.

The stricter [personal-data requirements](CONTRIBUTING.md#keep-personal-data-private)
also apply: household databases, exports, and personal financial details stay
outside committed source. Examples and screenshots use fictional data.

---

## Environment

| Setting     | Value                       |
| ----------- | --------------------------- |
| Environment | Pixi 0.83.0   |
| Formatting  | Ruff                        |
| Testing     | Pytest                      |
| Docs        | Jupyter Book 2 (MyST)       |

Use the full path to the selected Pixi 0.83.0 executable. The examples assume
`~/.pixi/bin/pixi`; use a separate versioned executable while other repositories
still need older clients. The short `pixi` form is for user-facing docs only.

---

## Commands

```bash
~/.pixi/bin/pixi install --locked              # Install without changing pixi.lock
~/.pixi/bin/pixi run --locked setup            # Post-install fixups
~/.pixi/bin/pixi run --locked test             # Run all tests
~/.pixi/bin/pixi run --locked test-unit        # Run only unit tests
~/.pixi/bin/pixi run --locked test-integration # Run integration tests
~/.pixi/bin/pixi run --locked fmt              # Format and lint
~/.pixi/bin/pixi run --locked docs-build       # Build docs
~/.pixi/bin/pixi run --locked docs-serve       # Serve docs locally
~/.pixi/bin/pixi run --locked python script.py # Run scripts
```

### Pixi lock discipline

Use `--locked` in CI and agent work unrelated to dependency changes. Ordinary
local development may use `pixi install` and `pixi run`. Refresh the committed
lock intentionally when dependency metadata changes, starting from the existing
lock. Inspect unexpected changes before restoring them. Source, comment, task,
and version-only edits should preserve lock bytes. Regenerate and verify
`environment.yml` after a lock refresh; its exporter supports lock format 7.

Keep a literal `[project].version` and read runtime versions from installed
metadata. Feature commits do not bump the version; release preparation and the
next development series do. Existing model/tax revisions govern recalculation,
independently of this package version. See CONTRIBUTING for the release workflow.

Install each worktree's own `.pixi/envs/default`; do not share installed
environments or notebook interpreters. Shared package caches are fine. Preserve
the privacy pre-push hook; set `PIXI_EXE` to the selected executable's full path
when pushing with a separately installed Pixi. Do not replace a shared binary
while other repositories require an older client.

---

## Code Style

- **DRY, YAGNI, KISS** — no premature abstractions
- **No backwards-compat shims** — when removing/renaming, delete completely
- **Fail fast** — validate inputs early, raise clear exceptions
- **Type hints** on all function signatures
- **Comments for "why"**, not "what"
- **Functions < 30 lines** ideally
- **Specific exceptions** — no bare `except:`
- **Google-style docstrings:**

```python
def example(param1: str, param2: int = 0) -> bool:
    """Short description.

    Args:
        param1: Description.
        param2: Description. Defaults to 0.

    Returns:
        Description.

    Raises:
        ValueError: When param1 is empty.
    """
```

---

## Testing

Tests mirror source structure: `tests/financial_model/{module}/`

- Files: `test_<module>.py`
- Functions: `test_<function>_<scenario>`
- Cover: happy path, edge cases, error conditions
- `@pytest.mark.integration` for integration tests

---

## Git Conventions

### Branch Naming

```
feat/[name]      fix/[name]       refactor/[name]
docs/[name]      test/[name]      chore/[name]
```

### Commit Format

```
type: Brief description

```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `style`

### PR Validation Order

1. `~/.pixi/bin/pixi run --locked fmt`
2. `~/.pixi/bin/pixi run --locked test`
3. `~/.pixi/bin/pixi run --locked docs-build`

---

## Architecture

```
src/financial_model/   # Package source
tests/financial_model/ # Tests (mirrors src/)
docs/                  # Jupyter Book 2 / MyST
scripts/               # Post-install fixups
```

---

## Pre-PR Checklist

- [ ] Code: no debug prints, no commented-out code
- [ ] Tests pass (`~/.pixi/bin/pixi run --locked test`), new code has tests
- [ ] Formatted and linted (`~/.pixi/bin/pixi run --locked fmt`)
- [ ] Docs build (`~/.pixi/bin/pixi run --locked docs-build`)
- [ ] Commits are atomic with proper messages
- [ ] Self-reviewed all changes

---

## Personal Overrides

Personal agent settings are tool-specific and must remain gitignored. Claude
Code users may use `CLAUDE.local.md`, typically symlinked into the personal
notes repo under `_dev/`.

That setup is optional; a fresh checkout need not contain `_dev/`. Before
editing through a notes symlink, resolve its target repository and read that
repository's instructions. Keep its branch and commit rules separate from this
project's workflow.
