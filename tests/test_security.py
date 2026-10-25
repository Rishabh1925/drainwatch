"""Security regression tests.

These catch accidentally committed secrets without exposing their values in test output.
They check that no tracked file contains patterns that look like real credentials.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[Path]:
    """Return all Git-tracked files in the repository."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=True,
        )
        return [PROJECT_ROOT / f for f in result.stdout.split("\0") if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available or not a git repository")


# Patterns that indicate a real secret rather than a placeholder.
# These are crafted to match JWT tokens and AWS keys without matching placeholder text.
SECRET_PATTERNS = [
    # Supabase / JWT service-role key (starts with eyJ, which is base64 for {"...)
    (re.compile(r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}"),
     "JWT token (possible Supabase service-role key)"),
    # AWS secret access key — requires a preceding context like "secret", "key", "=", or ":" 
    # to avoid matching table formatting, base64 chunks, and other benign 40-char strings.
    (re.compile(r"(?:secret|key|aws)[_\s:=\"']*[A-Za-z0-9/+=]{40}\b", re.IGNORECASE),
     "possible AWS secret access key"),
]

# Files that are allowed to contain test JWT patterns (e.g. this test file itself).
ALLOWLIST = {
    "tests/test_security.py",
}

# Binary file extensions to skip
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".safetensors", ".bin", ".pt", ".pth",
    ".zip", ".gz", ".tar", ".bz2",
    ".pdf", ".mp4", ".mp3",
    ".DS_Store",
}

# Files that contain hashes or integrity values that look like secrets.
SKIP_FILENAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}


def test_no_secrets_in_tracked_files():
    """No tracked file should contain what looks like a real credential.

    This test deliberately does NOT print the matched value — printing a secret
    in test output defeats the purpose. It reports only the file and line number.
    """
    violations = []
    for filepath in _tracked_files():
        rel = str(filepath.relative_to(PROJECT_ROOT))
        if rel in ALLOWLIST:
            continue
        if filepath.suffix.lower() in BINARY_EXTENSIONS:
            continue
        if filepath.name in SKIP_FILENAMES:
            continue
        if not filepath.is_file():
            continue
        try:
            content = filepath.read_text(errors="ignore")
        except Exception:
            continue
        for line_num, line in enumerate(content.splitlines(), start=1):
            for pattern, description in SECRET_PATTERNS:
                if pattern.search(line):
                    violations.append(f"  {rel}:{line_num} — {description}")

    assert not violations, (
        "Possible secrets found in tracked files (values not shown):\n"
        + "\n".join(violations)
        + "\n\nRemove real secrets and use environment variables or a secrets manager."
    )


def test_env_file_is_not_tracked():
    """The .env file must be in .gitignore and not tracked."""
    tracked = _tracked_files()
    env_files = [f for f in tracked if f.name == ".env" and ".example" not in f.name]
    assert not env_files, (
        f".env file is tracked in Git: {env_files}. "
        "Add it to .gitignore and remove it from tracking."
    )


def test_env_example_contains_no_real_values():
    """The .env.example must contain only placeholders, not real credentials."""
    env_example = PROJECT_ROOT / ".env.example"
    if not env_example.exists():
        pytest.skip(".env.example does not exist")

    content = env_example.read_text()
    for pattern, description in SECRET_PATTERNS:
        assert not pattern.search(content), (
            f".env.example contains what looks like a real {description}. "
            "Use placeholder values only."
        )


def test_readme_does_not_publish_default_passwords():
    """The README must not contain cleartext default passwords for production accounts."""
    readme = PROJECT_ROOT / "README.md"
    if not readme.exists():
        pytest.skip("README.md not found")

    content = readme.read_text()
    # Check for common default password patterns in table rows
    password_patterns = [
        re.compile(r"\|\s*`?admin\d{2,}`?\s*\|", re.IGNORECASE),
        re.compile(r"\|\s*`?operator\d{2,}`?\s*\|", re.IGNORECASE),
        re.compile(r"\|\s*`?password\d*`?\s*\|", re.IGNORECASE),
    ]
    for pattern in password_patterns:
        assert not pattern.search(content), (
            "README.md appears to contain default credentials in a table. "
            "Remove production passwords from documentation."
        )
