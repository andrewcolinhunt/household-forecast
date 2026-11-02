# Contributing

Install Pixi **0.83.0**, then the project environment and Git hooks:

```bash
pixi install
pixi run setup
```

Setup installs formatting checks and a pre-push privacy check. If you already
have a pre-push hook, setup stops so you can integrate the checks without losing
your existing hook.

Use ordinary `pixi install` and `pixi run` locally. CI and agents working on
changes unrelated to dependencies use `--locked`. Commit `pixi.lock`; refresh it
intentionally when dependency metadata changes, starting from the existing lock.
Source, comment, task, and version-only edits should preserve the lock bytes.
Inspect unexpected changes before restoring anything. The cloud exporter reads
lock format 7; run `pixi run export-cloud` and `pixi run check-cloud` after a
lock refresh to keep `environment.yml` in sync.

Each worktree needs its own installation under `.pixi/envs/default`. Do not copy
or symlink installed environments or reuse another worktree's notebook
interpreter. Shared download caches are fine. During a staged upgrade, use a
separate Pixi executable if other projects still require an older version. The
privacy hook accepts its full path through `PIXI_EXE`:

```bash
PIXI_EXE=/path/to/pixi-0.83.0 git push origin your-branch
```

The hook defaults to `~/.pixi/bin/pixi` when `PIXI_EXE` is unset. It still checks
the lock and scans the pushed history before any upload.

## Package versions

`pyproject.toml` holds the literal package version. Runtime `__version__` reads
the installed `household-forecast` metadata, even though the import is
`financial_model`. New development starts at `0.1.0.dev0`; builds need no Git
history or tags. The unused generated `__version_tuple__` export was removed.

Feature commits leave this version unchanged. It identifies a release or
development series, while the existing model revision and tax-engine versions
continue to determine whether saved results need recalculation.

When preparing a release, set its intended version, update release notes,
synchronize the editable installation, run the required checks, and build fresh
wheel and source distributions. Verify their actual metadata against the
intended tag before publishing. Commit the version and notes with any genuinely
required lock update. After release, advance to the next development version,
synchronize the environment, and commit that change. This application does not
have an automated publication workflow.

## Keep personal data private

Use fictional data in code, tests, issues, pull requests, and screenshots. Keep
household databases and exports outside the source repository.

The pre-push check scans each pushed branch or tag and its full history. Deleting
a private file in a later commit does not remove it from that history. The check
also examines commit metadata and tag annotations. It ignores unrelated local
branches.

To check personal names or other private phrases, create a JSON array of strings
in a file outside this repository, then configure its location in your local Git
settings:

```bash
git config --local household-forecast.privateTermsFile "$HOME/private/forecast-private-terms.json"
```

The file must contain phrases of at least three characters. Keep it private;
never add it to the repository or CI. Pushes stop if a configured file is missing
or invalid. Without this setting, the hook checks only the built-in private-file
and credential patterns.

Review findings before pushing. Do not bypass the hook to upload private data.
CI provides another check after upload; it cannot prevent disclosure on a public
branch.

## Validate changes

```bash
pixi run fmt
pixi run test
pixi run docs-build
```

Run the dependency audit after changing the lockfile:

```bash
pixi run check-dependencies
```

The audit scans the installed Pixi environment on Linux x86_64 or macOS arm64.
It downloads a checksum-verified Grype release and an advisory database, then
scans locally. It reports known vulnerabilities and fails for high or critical
findings. Conda packages use CPE matches, which can be incomplete or require
manual review; a passing scan is not proof that every dependency is safe.
CI audits both platforms on pull requests, pushes to `main`, and weekly.

Open a pull request with a short explanation of the change and its validation.
Report vulnerabilities using [the security policy](SECURITY.md).
