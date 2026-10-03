# AGENTS.md

Project conventions for contributors (human and AI).

## Product purpose

This tool helps the household understand how decisions such as moving or
changing jobs affect current cash, future wealth, and retirement options.
Simplifi handles expense and account tracking. Develop this app around comparing
decisions and explaining their consequences; imports serve that planning work.
The app compares a single year and forecasts saved Plans through retirement,
with explicit account, benefit, economic, and future-tax assumptions. See
`docs/concepts/decision-planning.md` and the current direction in `_dev/README.md`.

---

## Environment

| Setting     | Value                       |
| ----------- | --------------------------- |
| Environment | Pixi (`~/.pixi/bin/pixi`)   |
| Formatting  | Ruff                        |
| Testing     | Pytest                      |
| Docs        | Jupyter Book 2 (MyST)       |

IMPORTANT: Always use the full pixi path (`~/.pixi/bin/pixi`) when running
commands. The short `pixi` form is for user-facing docs only.

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

Use `--locked` for routine installs and tasks so Pixi stops instead of silently
rewriting `pixi.lock`. Run an unlocked Pixi command only when intentionally
changing dependencies, and commit `pyproject.toml` and `pixi.lock` together.

If `pixi.lock` becomes dirty unexpectedly, inspect the diff. When the only
changes are the editable project's Git-derived `version` and `sha256`, and no
dependency change was intended, restore `pixi.lock`. Do not discard it when
`pyproject.toml` or dependencies were intentionally changed.

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
skills/                # Canonical in-repo agent skills
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
