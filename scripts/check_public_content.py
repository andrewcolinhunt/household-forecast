"""Check publishable files for private data without printing matched values."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORIES = {"_dev", "data", "backups", "outputs", "scratch"}
PRIVATE_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".dump",
    ".scenario.json",
    ".wealth.json",
    ".planning.json",
    ".forecast.json",
    ".preserved-draft.json",
)
SECRET_PATTERNS = {
    "personal machine path": re.compile(
        r"(?<![A-Za-z0-9])(?:/Users/|/home/)[A-Za-z0-9._-]+/"
    ),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY"),
    "access token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
        r"(?:AKIA|ASIA)[A-Z0-9]{16}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]{15,})\b"
    ),
    "credential URL": re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb|https?|redis)://"
        r"[^\s/@:'\"]+:[^\s/@'\"]+@[^\s/'\"]*"
    ),
}


def _synthetic_database_url(value: str) -> bool:
    """Recognize the exact documented placeholder and local CI password marker."""
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    if url.scheme not in {"postgres", "postgresql"}:
        return False
    placeholder = (
        url.username == "APP_USER"
        and url.password == "APP_PASSWORD"
        and url.netloc.rsplit("@", 1)[-1].partition(":")[0] == "HOST"
    )
    local_test = url.password == "ci-test-only" and url.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    return placeholder or local_test


def check_content(
    name: str, data: bytes, private_terms: tuple[str, ...] = ()
) -> list[str]:
    """Return file/line findings, omitting credential and private-term values.

    Args:
        name: Repository-relative file name or release archive member name.
        data: File contents, or the target text of a symbolic link.
        private_terms: Literal private phrases from a separate private file.

    Returns:
        Findings describing the location and category of each match.
    """
    path = PurePosixPath(name)
    findings = []
    if (
        PRIVATE_DIRECTORIES.intersection(path.parts)
        or name.endswith(PRIVATE_SUFFIXES)
        or (path.name.startswith("forecast-") and path.suffix == ".json")
        or any(
            extension + "-" in path.name for extension in (".sqlite", ".sqlite3", ".db")
        )
        or (path.name.startswith(".env") and path.name != ".env.example")
        or name.endswith(".streamlit/secrets.toml")
        or path.name == "CLAUDE.local.md"
    ):
        findings.append(f"{name}: private file")
    if data.startswith(b"SQLite format 3\x00"):
        findings.append(f"{name}: SQLite database")
    terms = [
        re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)
        for term in private_terms
    ]
    for number, line in enumerate(
        data.decode("utf-8", errors="replace").splitlines(), 1
    ):
        for category, pattern in SECRET_PATTERNS.items():
            matches = pattern.finditer(line)
            if any(
                category != "credential URL" or not _synthetic_database_url(match[0])
                for match in matches
            ):
                findings.append(f"{name}:{number}: {category}")
        if any(pattern.search(line) for pattern in terms):
            findings.append(f"{name}:{number}: private term")
    return findings


def tracked_contents(root: Path) -> Iterator[tuple[str, bytes]]:
    """Yield tracked working files without following links into private folders."""
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
        .decode()
        .split("\0")
    )
    for name in filter(None, names):
        path = root / name
        if path.is_symlink():
            yield name, str(path.readlink()).encode()
        elif path.is_file():
            yield name, path.read_bytes()


def history_contents(root: Path) -> Iterator[tuple[str, bytes]]:
    """Yield all reachable Git blobs and commit messages, including deleted files."""
    # One blob can have several names; rev-list reports only one of those names.
    names = subprocess.check_output(
        ["git", "log", "--all", "-m", "--format=", "--name-only", "-z", "--no-renames"],
        cwd=root,
    )
    for name in sorted(set(filter(None, names.split(b"\0")))):
        yield name.decode(), b""
    entries = subprocess.check_output(
        ["git", "rev-list", "--objects", "--all"], cwd=root
    )
    with subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as process:
        assert process.stdin is not None
        assert process.stdout is not None
        for entry in entries.splitlines():
            object_id, _, path = entry.partition(b" ")
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            _, kind, size = process.stdout.readline().split()
            data = process.stdout.read(int(size))
            process.stdout.read(1)
            if kind == b"blob":
                yield path.decode(), data
            elif kind == b"commit":
                yield f"commit {object_id.decode()[:12]}", data.split(b"\n\n", 1)[1]
        process.stdin.close()


def load_private_terms(path: Path, root: Path) -> tuple[str, ...]:
    """Read literal phrases from a JSON array outside the public repository.

    Args:
        path: Private JSON file; a symlink into a private notes repo is allowed.
        root: Public repository root.

    Raises:
        ValueError: If the file is in the repository or is not a string list.
    """
    if path.resolve().is_relative_to(root.resolve()):
        message = "Keep the private terms file outside the public repository."
        raise ValueError(message)
    terms = json.loads(path.read_text())
    if not isinstance(terms, list) or not all(
        isinstance(term, str) and len(term) >= 3 for term in terms
    ):
        message = "Private terms must be a JSON array of strings with at least three characters."
        raise ValueError(message)
    return tuple(terms)


def main() -> None:
    """Check tracked files, optionally including history or an unpacked release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--private-terms", type=Path)
    parser.add_argument("--history", action="store_true")
    parser.add_argument(
        "--directory", type=Path, help="Also scan an unpacked release directory."
    )
    args = parser.parse_args()
    if args.directory is not None and not args.directory.is_dir():
        parser.error("The release directory does not exist.")
    terms = (
        load_private_terms(args.private_terms, args.root) if args.private_terms else ()
    )
    sources = [tracked_contents(args.root)]
    if args.history:
        sources.append(history_contents(args.root))
    if args.directory:
        sources.append(
            (
                str(path.relative_to(args.directory)),
                str(path.readlink()).encode()
                if path.is_symlink()
                else path.read_bytes(),
            )
            for path in args.directory.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    findings = sorted(
        {
            finding
            for source in sources
            for name, data in source
            for finding in check_content(name, data, terms)
        }
    )
    if findings:
        sys.stdout.write("\n".join(findings) + "\n")
        raise SystemExit(1)
    sys.stdout.write("No private-content matches found.\n")


if __name__ == "__main__":
    main()
