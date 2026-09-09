#!/usr/bin/env python3
"""Validate one report from standard input against the fixed checked-in schema."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlencode, urlparse

from jsonschema import Draft202012Validator, FormatChecker

MAX_REPORT_BYTES = 10 * 1024 * 1024
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
POSITIVE_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
KNOWN_ACHIEVEMENT_NAMES = {
    "quickdraw": "Quickdraw",
    "yolo": "YOLO",
    "pull-shark": "Pull Shark",
    "pair-extraordinaire": "Pair Extraordinaire",
    "galaxy-brain": "Galaxy Brain",
    "starstruck": "Starstruck",
    "public-sponsor": "Public Sponsor",
    "heart-on-your-sleeve": "Heart On Your Sleeve",
    "open-sourcerer": "Open Sourcerer",
    "arctic-code-vault-contributor": "Arctic Code Vault Contributor",
    "mars-2020-contributor": "Mars 2020 Contributor",
}


def safe_text(value, *, maximum):
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and all(
            ord(character) >= 32
            and ord(character) != 127
            and not unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def github_url(value):
    """Return a parsed canonical-host GitHub URL, or None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or not parsed.path.startswith("/")
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return parsed


def profile_url_matches(login, value, *, achievements_tab=False):
    parsed = github_url(value)
    if parsed is None:
        return False
    expected_query = "tab=achievements" if achievements_tab else ""
    return (
        parsed.path.casefold() == f"/{login}".casefold()
        and parsed.query == expected_query
        and not parsed.params
        and not parsed.fragment
    )


def achievement_url_matches(login, slug, value):
    parsed = github_url(value)
    if parsed is None:
        return False
    expected_query = urlencode({"achievement": slug, "tab": "achievements"})
    return (
        parsed.path.casefold() == f"/{login}".casefold()
        and parsed.query == expected_query
        and not parsed.params
        and not parsed.fragment
    )


def repository_identity(login, name_with_owner, value):
    if not isinstance(name_with_owner, str):
        return False
    parts = name_with_owner.split("/")
    parsed = github_url(value)
    return (
        len(parts) == 2
        and LOGIN_PATTERN.fullmatch(parts[0]) is not None
        and parts[0].casefold() == login.casefold()
        and parsed is not None
        and parsed.path.rstrip("/").casefold() == f"/{name_with_owner}".casefold()
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def discussion_answer_url_key(name_with_owner, value):
    parsed = github_url(value)
    if parsed is None or not isinstance(name_with_owner, str):
        return None
    prefix = f"/{name_with_owner}/discussions/".casefold()
    folded_path = parsed.path.casefold()
    if not folded_path.startswith(prefix):
        return None
    discussion_number = parsed.path[len(prefix) :]
    fragment_prefix = "discussioncomment-"
    folded_fragment = parsed.fragment.casefold()
    if not folded_fragment.startswith(fragment_prefix):
        return None
    comment_number = parsed.fragment[len(fragment_prefix) :]
    if (
        POSITIVE_ID_PATTERN.fullmatch(discussion_number) is None
        or POSITIVE_ID_PATTERN.fullmatch(comment_number) is None
        or parsed.params
        or parsed.query
    ):
        return None
    return folded_path, folded_fragment


def semantic_errors(instance):
    """Return cross-field errors that JSON Schema cannot express portably."""
    errors = []
    subject = instance["subject"]
    login = subject["login"]
    if not profile_url_matches(login, subject["url"]):
        errors.append("invalid subject identity")

    evidence = instance["public_evidence"]
    answers = evidence["accepted_discussion_answers"]
    answer_items = answers["evidence"]
    if answers["public_total"] != len(answer_items):
        errors.append("accepted-answer total differs from evidence length")
    outside_answers = sum(not item["owned_by_subject"] for item in answer_items)
    if answers["outside_personal_namespace_total"] != outside_answers:
        errors.append("accepted-answer namespace total disagrees with evidence")
    self_accepted = sum(item["self_accepted"] is True for item in answer_items)
    if answers["self_accepted_total"] != self_accepted:
        errors.append("accepted-answer self-accepted total disagrees with evidence")
    answer_keys = []
    for item in answer_items:
        repository = item["repository"]
        parts = repository.split("/")
        key = discussion_answer_url_key(repository, item["url"])
        if (
            len(parts) != 2
            or LOGIN_PATTERN.fullmatch(parts[0]) is None
            or key is None
            or item["owned_by_subject"] != (parts[0].casefold() == login.casefold())
        ):
            errors.append("invalid accepted-answer identity")
        answer_keys.append(key)
    if len(set(answer_keys)) != len(answer_items):
        errors.append("duplicate accepted-answer URL")

    pulls = evidence["merged_pull_requests"]
    if pulls["outside_personal_namespace_total"] > pulls["public_total"]:
        errors.append("pull-request namespace count exceeds public total")

    repositories = evidence["owned_public_nonfork_repositories"]
    top_repository = repositories["top_by_stars"]
    if (repositories["total"] == 0) != (top_repository is None):
        errors.append("repository total and top repository disagree")
    if top_repository is None and repositories["total_stars"] != 0:
        errors.append("zero repositories must have zero stars")
    if (
        top_repository is not None
        and top_repository["stars"] > repositories["total_stars"]
    ):
        errors.append("top repository stars exceed aggregate stars")
    if top_repository is not None and not repository_identity(
        login,
        top_repository["name_with_owner"],
        top_repository["url"],
    ):
        errors.append("invalid top repository identity")

    visible = {item["slug"]: item for item in instance["visible_achievements"]}
    endpoints = {item["slug"]: item for item in instance["achievement_endpoints"]}
    if len(visible) != len(instance["visible_achievements"]):
        errors.append("duplicate visible slug")
    if len(endpoints) != len(instance["achievement_endpoints"]):
        errors.append("duplicate endpoint slug")
    if set(endpoints) != set(KNOWN_ACHIEVEMENT_NAMES) | set(visible):
        errors.append("incomplete achievement endpoint inventory")
    if len({item["url"] for item in instance["achievement_endpoints"]}) != len(
        instance["achievement_endpoints"]
    ):
        errors.append("duplicate achievement endpoint URL")
    if any(
        not safe_text(item["name"], maximum=100)
        or (
            slug in KNOWN_ACHIEVEMENT_NAMES
            and item["name"] != KNOWN_ACHIEVEMENT_NAMES[slug]
        )
        or not achievement_url_matches(login, slug, item["url"])
        for slug, item in endpoints.items()
    ):
        errors.append("invalid achievement endpoint identity")
    rendered = {slug for slug, item in endpoints.items() if item["status"] == 200}
    if rendered != set(visible):
        errors.append("visible achievements and endpoint statuses disagree")
    for slug, item in visible.items():
        endpoint = endpoints.get(slug)
        if endpoint is None or (item["name"], item["url"]) != (
            endpoint["name"],
            endpoint["url"],
        ):
            errors.append("visible achievement and endpoint identity disagree")
            break

    profile = instance["source_health"]["profile"]
    if not profile_url_matches(login, profile["url"], achievements_tab=True):
        errors.append("invalid profile source identity")
    return errors


def main() -> int:
    if len(sys.argv) != 1:
        print("usage: validate_stdin.py", file=sys.stderr)
        return 2

    try:
        document_bytes = sys.stdin.buffer.read(MAX_REPORT_BYTES + 1)
    except OSError as exc:
        print(f"could not read report: {exc}", file=sys.stderr)
        return 1
    if len(document_bytes) > MAX_REPORT_BYTES:
        print("report exceeds 10 MiB", file=sys.stderr)
        return 1
    try:
        document = document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        print("report is not valid UTF-8", file=sys.stderr)
        return 1

    schema_path = (
        Path(__file__).resolve().parents[1] / "schema" / "report-v1.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        instance = json.loads(document)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid report or schema: {exc}", file=sys.stderr)
        return 1

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            print(f"{location}: {error.message}", file=sys.stderr)
        return 1

    invariants = semantic_errors(instance)
    for error in invariants:
        print(f"<semantic>: {error}", file=sys.stderr)
    return 1 if invariants else 0


if __name__ == "__main__":
    raise SystemExit(main())
