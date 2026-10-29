# Contributing

Install the locked environment and Git hooks before making changes:

```bash
pixi install --locked
pixi run --locked setup
```

Setup installs formatting checks and a pre-push privacy check. If you already
have a pre-push hook, setup stops so you can integrate the checks without losing
your existing hook.

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
pixi run --locked fmt
pixi run --locked test
pixi run --locked docs-build
```

Run the dependency audit after changing the lockfile:

```bash
pixi run --locked check-dependencies
```

The audit scans the installed Pixi environment on Linux x86_64 or macOS arm64.
It downloads a checksum-verified Grype release and an advisory database, then
scans locally. It reports known vulnerabilities and fails for high or critical
findings. Conda packages use CPE matches, which can be incomplete or require
manual review; a passing scan is not proof that every dependency is safe.
CI audits both platforms on pull requests, pushes to `main`, and weekly.

Open a pull request with a short explanation of the change and its validation.
Report vulnerabilities using [the security policy](SECURITY.md).
