"""Public files exclude private data; repository integration checks require Git."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_public_content import (
    ROOT,
    check_content,
    history_contents,
    load_private_terms,
    tracked_contents,
)


@pytest.mark.parametrize(
    "name",
    [
        "data/household.json",
        "_dev/notes.md",
        "outputs/snapshot.png",
        "backup.sqlite",
        "backup.sqlite-wal",
        "backup.db",
        "backup.dump",
        ".env",
        ".env.local",
        ".streamlit/secrets.toml",
        "family.wealth.json",
        "family.planning.json",
        "family.forecast.json",
        "forecast-example.json",
        "work.preserved-draft.json",
        "CLAUDE.local.md",
    ],
)
def test_check_content_rejects_private_paths(name: str) -> None:
    assert check_content(name, b"example") == [f"{name}: private file"]


def test_check_content_detects_disguised_database() -> None:
    assert check_content("example.txt", b"SQLite format 3\x00data") == [
        "example.txt: SQLite database"
    ]


@pytest.mark.parametrize(
    "data",
    [
        b"ghp_" + b"x" * 36,
        b"-----BEGIN " + b"PRIVATE KEY-----",
        b"postgresql:" + b"//user:password@host/db",
        b"/Users/" + b"example/private/notes.md",
    ],
)
def test_check_content_redacts_credentials(data: bytes) -> None:
    result = check_content("settings.txt", data)
    assert len(result) == 1
    assert result[0].startswith("settings.txt:1:")
    assert data.decode() not in result[0]


@pytest.mark.parametrize(
    "authority",
    [
        "APP_USER:APP_PASSWORD@HOST",
        "APP_USER:APP_PASSWORD@HOST:5432",
        "financial_test:ci-test-only@127.0.0.1:5432",
        "financial_test:ci-test-only@localhost",
        "financial_test:ci-test-only@[::1]",
    ],
)
def test_check_content_allows_explicit_database_fixtures(authority: str) -> None:
    value = ("postgresql:" + "//" + authority + "/DATABASE").encode()
    assert not check_content("settings.example", value)


@pytest.mark.parametrize(
    "authority",
    [
        "APP_USER:APP_PASSWORD@db.example.org",
        "APP_USER:real-password@HOST",
        "real-user:APP_PASSWORD@HOST",
        "APP_USER:APP_PASSWORD@host",
        "financial_test:ci-test-only@db.example.org",
        "financial_test:real-password@127.0.0.1",
        "financial_test:real-password@localhost",
        "financial_test:real-password@[::1]",
        "financial_test:ci-test-only@127.0.0.1.example.org",
    ],
)
def test_check_content_rejects_changes_to_database_fixture(authority: str) -> None:
    value = ("postgresql:" + "//" + authority + "/database").encode()
    assert check_content("settings.txt", value) == ["settings.txt:1: credential URL"]


def test_check_content_checks_every_url_and_private_term_even_in_fixtures() -> None:
    prefix = "postgresql:" + "//"
    value = (
        prefix
        + "APP_USER:APP_PASSWORD@HOST/database "
        + prefix
        + "user:real-password@db.example.org/database"
    ).encode()
    findings = check_content("settings.txt", value, ("APP_USER",))
    assert findings == [
        "settings.txt:1: credential URL",
        "settings.txt:1: private term",
    ]


def test_check_content_redacts_private_terms_and_matches_words() -> None:
    result = check_content(
        "example.py", b"salary = 50000\nMorgan Example", ("Morgan Example",)
    )
    assert result == ["example.py:2: private term"]
    assert not check_content("example.py", b"Morgan Exampleton", ("Morgan Example",))


def test_check_content_redacts_private_filename() -> None:
    findings = check_content(
        "Morgan Example.txt", b"Morgan Example", ("Morgan Example",)
    )
    assert findings == [
        "[redacted filename]: private term in filename",
        "[redacted filename]:1: private term",
    ]


def test_check_content_allows_synthetic_examples_and_author_contact() -> None:
    assert not check_content(".env.example", b"PASSWORD=")
    assert not check_content("src/examples.py", b"salary=55000\nname='Alex'")
    assert not check_content("pyproject.toml", b"email='author@example.com'")


def test_load_private_terms_requires_external_file(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    private = tmp_path / "private.json"
    private.write_text(json.dumps(["Morgan Example"]))
    assert load_private_terms(private, root) == ("Morgan Example",)
    (root / "_dev").symlink_to(tmp_path)
    assert load_private_terms(root / "_dev/private.json", root) == ("Morgan Example",)
    included = root / "private.json"
    included.write_text(private.read_text())
    with pytest.raises(ValueError, match="outside"):
        load_private_terms(included, root)
    private.write_text(json.dumps([None]))
    with pytest.raises(ValueError, match="JSON array"):
        load_private_terms(private, root)


@pytest.mark.integration
def test_history_contents_includes_deleted_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.name", "Example"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "example@example.com"], cwd=tmp_path, check=True
    )
    path = tmp_path / "old.txt"
    path.write_text("Morgan Example")
    subprocess.run(["git", "add", "old.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "Initial example"], cwd=tmp_path, check=True
    )
    path.unlink()
    subprocess.run(
        ["git", "commit", "-qam", "Remove example"], cwd=tmp_path, check=True
    )
    findings = [
        finding
        for name, data in history_contents(tmp_path)
        for finding in check_content(name, data, ("Morgan Example",))
    ]
    assert findings == ["old.txt:1: private term"]


@pytest.mark.integration
def test_history_contents_checks_every_name_for_reused_blob(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in ("public.txt", ".env.production"):
        (tmp_path / name).write_text("Identical contents")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Example",
            "-c",
            "user.email=example@example.com",
            "commit",
            "-qm",
            "Same data under two names",
        ],
        cwd=tmp_path,
        check=True,
    )
    contents = list(history_contents(tmp_path))
    assert {name for name, _ in contents} >= {"public.txt", ".env.production"}
    findings = {
        finding for name, data in contents for finding in check_content(name, data)
    }
    assert ".env.production: private file" in findings


@pytest.mark.integration
def test_tracked_contents_does_not_read_private_symlink(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    private = tmp_path.parent / "private-notes.txt"
    private.write_text("private contents")
    (tmp_path / "link").symlink_to(private)
    subprocess.run(["git", "add", "link"], cwd=tmp_path, check=True)
    assert list(tracked_contents(tmp_path)) == [("link", str(private).encode())]


@pytest.mark.integration
def test_repository_tracked_files_exclude_private_content() -> None:
    findings = [
        finding
        for name, data in tracked_contents(ROOT)
        for finding in check_content(name, data)
    ]
    assert not findings, "\n".join(findings)


@pytest.mark.integration
def test_main_checks_unpacked_release_and_fails_if_missing(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    release = tmp_path / "release"
    release.mkdir()
    (release / "household.txt").write_bytes(b"SQLite format 3\x00data")
    (release / "private-link").symlink_to(Path("/Users") / "example/private")
    command = [
        sys.executable,
        str(ROOT / "scripts/check_public_content.py"),
        "--root",
        str(repository),
        "--directory",
    ]
    result = subprocess.run(
        [*command, str(release)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "household.txt: SQLite database" in result.stdout
    assert "private-link:1: personal machine path" in result.stdout
    missing = subprocess.run(
        [*command, str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "release directory does not exist" in missing.stderr


@pytest.mark.integration
@pytest.mark.parametrize(
    "name",
    [
        ".env.local",
        ".env.production",
        "data/household.sqlite",
        "outputs/backup.json",
        "sample.wealth.json",
        "sample.planning.json",
        "sample.forecast.json",
        "forecast-example.json",
        "work.preserved-draft.json",
        "_dev",
    ],
)
def test_private_outputs_are_ignored(name: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", name], cwd=ROOT, check=False
    )
    assert result.returncode == 0


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()


@pytest.fixture
def push_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Example")
    _git(repository, "config", "user.email", "example@example.com")
    _git(repository, "commit", "--allow-empty", "-qm", "Initial example")
    private = tmp_path / "private.json"
    private.write_text(json.dumps(["Morgan Example"]))
    _git(
        repository,
        "config",
        "--local",
        "household-forecast.privateTermsFile",
        str(private),
    )
    return repository


def _check_push(root: Path, *refs: str) -> subprocess.CompletedProcess[str]:
    updates = "".join(
        f"{ref} {_git(root, 'rev-parse', ref)} refs/heads/example-{number} {'0' * 40}\n"
        for number, ref in enumerate(refs)
    )
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_public_content.py"),
            "--root",
            str(root),
            "--pre-push",
        ],
        input=updates,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.integration
def test_pre_push_checks_deleted_contents_and_all_pushed_refs(
    push_repository: Path,
) -> None:
    root = push_repository
    clean = _git(root, "rev-parse", "HEAD")
    (root / "old.txt").write_text("Morgan Example")
    _git(root, "add", "old.txt")
    _git(root, "commit", "-qm", "Add example")
    _git(root, "rm", "old.txt")
    _git(root, "commit", "-qm", "Remove example")
    result = _check_push(root, clean, "HEAD")
    assert result.returncode == 1
    assert "old.txt:1: private term" in result.stdout
    assert "Morgan Example" not in result.stdout + result.stderr


@pytest.mark.integration
def test_pre_push_checks_reused_private_filename_after_deletion(
    push_repository: Path,
) -> None:
    root = push_repository
    for name in ("example.txt", ".env.production"):
        (root / name).write_text("same contents")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Add example")
    _git(root, "rm", ".env.production")
    _git(root, "commit", "-qm", "Remove private path")
    result = _check_push(root, "HEAD")
    assert result.returncode == 1
    assert ".env.production: private file" in result.stdout


@pytest.mark.integration
def test_pre_push_ignores_unrelated_history_and_uncommitted_files(
    push_repository: Path,
) -> None:
    root = push_repository
    _git(root, "checkout", "-qb", "private")
    (root / "private.txt").write_text("Morgan Example")
    _git(root, "add", "private.txt")
    _git(root, "commit", "-qm", "Private branch")
    _git(root, "checkout", "-q", "main")
    (root / "uncommitted.txt").write_text("Morgan Example")
    _git(root, "add", "uncommitted.txt")
    result = _check_push(root, "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
@pytest.mark.parametrize("location", ["author", "message", "tag"])
def test_pre_push_checks_commit_metadata_and_annotated_tags(
    push_repository: Path, location: str
) -> None:
    root = push_repository
    if location == "author":
        _git(root, "config", "user.name", "Morgan Example")
    message = "Morgan Example" if location == "message" else "Example change"
    _git(root, "commit", "--allow-empty", "-qm", message)
    ref = "HEAD"
    if location == "tag":
        _git(root, "tag", "-a", "v1", "-m", "Morgan Example")
        ref = "refs/tags/v1"
    result = _check_push(root, ref)
    assert result.returncode == 1
    assert "private term" in result.stdout
    assert "Morgan Example" not in result.stdout + result.stderr


@pytest.mark.integration
def test_pre_push_fails_when_configured_terms_are_missing(
    push_repository: Path,
) -> None:
    _git(
        push_repository,
        "config",
        "--local",
        "household-forecast.privateTermsFile",
        "../missing.json",
    )
    result = _check_push(push_repository, "HEAD")
    assert result.returncode == 2
    assert "Cannot load private terms" in result.stderr


@pytest.mark.integration
def test_pre_push_allows_ref_deletion(push_repository: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_public_content.py"),
            "--root",
            str(push_repository),
            "--pre-push",
        ],
        input=f"(delete) {'0' * 40} refs/heads/old {_git(push_repository, 'rev-parse', 'HEAD')}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_setup_installs_guard_repeatably_and_preserves_existing_hook(
    push_repository: Path,
) -> None:
    root = push_repository
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("setup.sh", "pre-push"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    hook = root / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    command = ["bash", "scripts/setup.sh"]
    blocked = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False
    )
    assert blocked.returncode == 1
    assert "manual integration" in blocked.stderr
    assert hook.read_text() == "#!/bin/sh\nexit 1\n"
    hook.unlink()
    for _ in range(2):
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert hook.is_symlink()
        assert hook.resolve() == scripts / "pre-push"
        assert hook.stat().st_mode & 0o111


@pytest.mark.integration
def test_installed_hook_blocks_actual_push_of_deleted_private_data(
    push_repository: Path, tmp_path: Path
) -> None:
    root = push_repository
    clean = _git(root, "rev-parse", "HEAD")
    (root / "old.txt").write_text("Morgan Example")
    _git(root, "add", "old.txt")
    _git(root, "commit", "-qm", "Add example")
    _git(root, "rm", "old.txt")
    _git(root, "commit", "-qm", "Remove example")
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("pre-push", "check_public_content.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    (scripts / "pre-push").chmod(0o755)
    (root / ".git/hooks/pre-push").symlink_to(scripts / "pre-push")
    # Substitute only Pixi's environment launcher; exercise the real Git hook.
    home = tmp_path / "home"
    launcher = home / ".pixi/bin/pixi"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        f'#!/bin/sh\nshift 4\nexec {shlex.quote(sys.executable)} "$@"\n'
    )
    launcher.chmod(0o755)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    environment = {**os.environ, "HOME": str(home)}
    for ref, expected in ((clean, 0), ("HEAD", 1)):
        result = subprocess.run(
            ["git", "push", str(remote), f"{ref}:refs/heads/main"],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == expected, result.stdout + result.stderr
    assert "old.txt:1: private term" in result.stdout
    assert _git(remote, "rev-parse", "refs/heads/main") == clean
