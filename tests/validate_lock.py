#!/usr/bin/env python3
"""Validate the checked-in dependency lock's no-source-build policy."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONFIG = """[tool.pip-tools.compile]
allow-unsafe = true
emit-index-url = false
emit-options = true
emit-trusted-host = false
generate-hashes = true
pip-args = "--only-binary=:all:"
strip-extras = true
"""
SHA256_PATTERN = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")
PIN_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==[^\s;\\]+$")


def validate_policy(config: str, lock: str) -> tuple[int, int]:
    """Return pin/hash counts or raise ValueError for a policy violation."""
    if config != EXPECTED_CONFIG:
        raise ValueError(".pip-tools.toml differs from the hardened compiler policy")

    lines = lock.splitlines()
    requirement_starts = [
        index
        for index, line in enumerate(lines)
        if line and not line[0].isspace() and not line.startswith(("#", "--"))
    ]
    if not requirement_starts:
        raise ValueError("lock contains no requirements")
    preamble_entries = [
        line.strip()
        for line in lines[: requirement_starts[0]]
        if line.strip() and not line.strip().startswith("#")
    ]
    if preamble_entries != ["--only-binary :all:"]:
        raise ValueError(
            "the only permitted lock preamble entry is '--only-binary :all:'"
        )

    all_hashes = []
    normalized_names = []
    for position, start in enumerate(requirement_starts):
        stop = (
            requirement_starts[position + 1]
            if position + 1 < len(requirement_starts)
            else len(lines)
        )
        block = "\n".join(lines[start:stop])
        requirement = lines[start].split("\\", 1)[0].strip()
        pin_match = PIN_PATTERN.fullmatch(requirement)
        if pin_match is None:
            raise ValueError(f"requirement is not exactly pinned: {requirement}")
        normalized_names.append(
            re.sub(r"[-_.]+", "-", pin_match.group("name")).casefold()
        )
        content_lines = [
            line.rstrip()
            for line in lines[start:stop]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if (
            len(content_lines) < 2
            or any(not line.endswith("\\") for line in content_lines[:-1])
            or content_lines[-1].endswith("\\")
            or any(
                SHA256_PATTERN.fullmatch(line.strip().removesuffix("\\").rstrip())
                is None
                for line in content_lines[1:]
            )
        ):
            raise ValueError(f"requirement has malformed continuations: {requirement}")
        hashes = SHA256_PATTERN.findall(block)
        if not hashes:
            raise ValueError(f"requirement has no SHA-256 hash: {requirement}")
        all_hashes.extend(hashes)

    if len(all_hashes) != len(set(all_hashes)):
        raise ValueError("lock contains duplicate SHA-256 artifact hashes")
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError("lock contains duplicate normalized requirement names")

    return len(requirement_starts), len(all_hashes)


def fail(message: str) -> int:
    print(f"dependency lock policy failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    try:
        config = (ROOT / ".pip-tools.toml").read_text(encoding="utf-8")
        lock = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        pin_count, hash_count = validate_policy(config, lock)
    except OSError as exc:
        return fail(str(exc))
    except ValueError as exc:
        return fail(str(exc))

    print(
        "dependency lock policy: ok "
        f"({pin_count} pins, {hash_count} SHA-256 hashes "
        "under wheel-only policy)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
