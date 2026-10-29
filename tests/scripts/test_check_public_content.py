"""Public files exclude private data; repository integration checks require Git."""

from __future__ import annotations

import json
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
