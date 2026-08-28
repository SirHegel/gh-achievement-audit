"""Command-line implementation for gh-achievement-audit."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from . import __version__

OFFICIAL_PROFILE_REFERENCE = (
    "https://docs.github.com/en/account-and-profile/reference/profile-reference"
)
OFFICIAL_DISCUSSIONS_REFERENCE = (
    "https://docs.github.com/en/graphql/reference/discussions"
)
OFFICIAL_GH_API_REFERENCE = "https://cli.github.com/manual/gh_api"

LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
RFC3339_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})T"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.[0-9]+)?(?:Z|(?P<offset_sign>[+-])"
    r"(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))$"
)
MAX_ACHIEVEMENT_CARDS = 32
MAX_ACHIEVEMENT_SLUG_LENGTH = 100

KNOWN_ACHIEVEMENTS: Tuple[Tuple[str, str], ...] = (
    ("Quickdraw", "quickdraw"),
    ("YOLO", "yolo"),
    ("Pull Shark", "pull-shark"),
    ("Pair Extraordinaire", "pair-extraordinaire"),
    ("Galaxy Brain", "galaxy-brain"),
    ("Starstruck", "starstruck"),
    ("Public Sponsor", "public-sponsor"),
    ("Heart On Your Sleeve", "heart-on-your-sleeve"),
    ("Open Sourcerer", "open-sourcerer"),
    ("Arctic Code Vault Contributor", "arctic-code-vault-contributor"),
    ("Mars 2020 Contributor", "mars-2020-contributor"),
)

AUDIT_QUERY = """
query(
  $login: String!
  $advisoryQuery: String!
  $pullCursor: String
  $answerCursor: String
  $repositoryCursor: String
  $includePulls: Boolean!
  $includeAnswers: Boolean!
  $includeRepositories: Boolean!
) {
  user(login: $login) {
    login
    url
    isBountyHunter
    isCampusExpert
    isDeveloperProgramMember
    isGitHubStar
    pullRequests(first: 100, after: $pullCursor, states: MERGED)
      @include(if: $includePulls) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        author { login }
        repository { isPrivate owner { login } }
      }
    }
    repositoryDiscussionComments(
      first: 100
      after: $answerCursor
      onlyAnswers: true
    ) @include(if: $includeAnswers) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        url
        isAnswer
        upvoteCount
        createdAt
        author { login }
        discussion {
          repository { nameWithOwner url isPrivate owner { login } }
        }
      }
    }
    repositories(
      first: 100
      after: $repositoryCursor
      privacy: PUBLIC
      ownerAffiliations: OWNER
      isFork: false
    ) @include(if: $includeRepositories) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        nameWithOwner
        url
        isPrivate
        isFork
        stargazerCount
        forkCount
        owner { login }
      }
    }
  }
  advisoryCredits: search(
    query: $advisoryQuery
    type: ISSUE
    first: 1
  ) { issueCount }
  rateLimit { cost remaining resetAt }
}
"""


class AuditError(RuntimeError):
    """A safe, user-facing audit failure."""


class AchievementHTMLParser(HTMLParser):
    """Extract structured achievement cards from the dedicated profile route."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_html = False
        self.saw_html_end = False
        self.in_html = False
        self.saw_content_outside_html = False
        self.saw_achievements_route = False
        self.saw_unmatched_details_end = False
        self.exceeded_achievement_card_limit = False
        self.achievement_card_count = 0
        self.achievement_item_count = 0
        self.card_slugs: List[str] = []
        self.items: List[Tuple[str, str]] = []
        self._details_stack: List[Optional[str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        folded_tag = tag.casefold()
        attributes = dict(attrs)
        if folded_tag == "html":
            if self.saw_html or self.saw_html_end or self.in_html:
                self.saw_content_outside_html = True
            else:
                self.saw_html = True
                self.in_html = True
            return
        if not self.in_html:
            self.saw_content_outside_html = True
            return
        if (
            folded_tag == "meta"
            and attributes.get("name") == "route-controller"
            and attributes.get("content") == "profiles_achievements"
        ):
            self.saw_achievements_route = True
        if folded_tag == "details":
            slug = attributes.get("data-achievement-slug")
            self._details_stack.append(slug)
            if slug is not None:
                self.achievement_card_count += 1
                if self.achievement_card_count > MAX_ACHIEVEMENT_CARDS:
                    self.exceeded_achievement_card_limit = True
                else:
                    self.card_slugs.append(slug)

        alt = attributes.get("alt")
        prefix = "Achievement: "
        active_slug = next(
            (slug for slug in reversed(self._details_stack) if slug is not None), None
        )
        if alt and alt.startswith(prefix) and active_slug is not None:
            name = alt[len(prefix) :].strip()
            if name:
                self.achievement_item_count += 1
                if self.achievement_item_count > MAX_ACHIEVEMENT_CARDS:
                    self.exceeded_achievement_card_limit = True
                else:
                    self.items.append((active_slug, name))

    def handle_endtag(self, tag: str) -> None:
        folded_tag = tag.casefold()
        if folded_tag == "html":
            if not self.in_html or self.saw_html_end:
                self.saw_content_outside_html = True
            else:
                self.in_html = False
                self.saw_html_end = True
            return
        if not self.in_html:
            self.saw_content_outside_html = True
            return
        if folded_tag == "details":
            if self._details_stack:
                self._details_stack.pop()
            else:
                self.saw_unmatched_details_end = True

    def handle_data(self, data: str) -> None:
        if not self.in_html and data.strip():
            self.saw_content_outside_html = True


class GitHubClient:
    """Authenticated read-only access through the installed GitHub CLI."""

    def _run(
        self,
        args: Sequence[str],
        *,
        input_text: Optional[str] = None,
        timeout: int = 60,
    ) -> str:
        try:
            completed = subprocess.run(
                list(args),
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise AuditError("required command not found: gh") from exc
        except subprocess.TimeoutExpired as exc:
            raise AuditError("GitHub API request timed out") from exc

        if completed.returncode != 0:
            # gh can include request details in stderr. Keep the public error stable
            # instead of reflecting potentially sensitive environment or API data.
            raise AuditError("GitHub API request failed")
        return completed.stdout

    def authenticated_login(self) -> str:
        output = self._run(
            ("gh", "api", "user", "--hostname", "github.com", "--jq", ".login")
        )
        login = output.strip()
        if not login:
            raise AuditError("could not resolve the authenticated GitHub.com user")
        return login

    def graphql(self, query: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = json.dumps({"query": query, "variables": dict(variables)})
        output = self._run(
            ("gh", "api", "graphql", "--hostname", "github.com", "--input", "-"),
            input_text=payload,
        )
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError as exc:
            raise AuditError("GitHub GraphQL returned malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise AuditError("GitHub GraphQL returned an unexpected document")
        errors = decoded.get("errors")
        if errors:
            raise AuditError("GitHub GraphQL reported an error")
        return decoded


class WebClient:
    """Low-volume public profile reads with bounded responses."""

    MAX_PROFILE_BYTES = 2 * 1024 * 1024

    def _request(self, url: str, *, read_body: bool) -> Tuple[int, bytes]:
        if not _valid_github_url(url):
            raise AuditError("public GitHub request URL was invalid")
        requested_url = urlparse(url)
        try:
            request = Request(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en",
                    "User-Agent": f"gh-achievement-audit/{__version__}",
                },
                method="GET",
            )
        except (TypeError, ValueError) as exc:
            raise AuditError("public GitHub request URL was invalid") from exc
        try:
            with urlopen(request, timeout=20) as response:
                status = response.getcode()
                final_url = urlparse(response.geturl())
                if final_url.scheme != "https" or final_url.netloc != "github.com":
                    raise AuditError("public GitHub request redirected off github.com")
                if (
                    final_url.path != requested_url.path
                    or final_url.query != requested_url.query
                ):
                    raise AuditError("public GitHub request redirected unexpectedly")
                body = response.read(self.MAX_PROFILE_BYTES + 1) if read_body else b""
        except HTTPError as exc:
            if not read_body and exc.code == 404:
                try:
                    final_url = urlparse(exc.geturl())
                except (TypeError, ValueError) as parse_exc:
                    raise AuditError(
                        "public GitHub request returned an invalid final URL"
                    ) from parse_exc
                if final_url.scheme != "https" or final_url.netloc != "github.com":
                    raise AuditError(
                        "public GitHub request redirected off github.com"
                    ) from exc
                if (
                    final_url.path != requested_url.path
                    or final_url.query != requested_url.query
                ):
                    raise AuditError(
                        "public GitHub request redirected unexpectedly"
                    ) from exc
                return 404, b""
            raise AuditError(
                f"public GitHub profile request returned HTTP {exc.code}"
            ) from exc
        except (HTTPException, OSError, TypeError, ValueError) as exc:
            raise AuditError("public GitHub profile request failed") from exc

        if read_body and len(body) > self.MAX_PROFILE_BYTES:
            raise AuditError("public GitHub profile response exceeded 2 MiB")
        return status, body

    def profile_html(self, url: str) -> str:
        status, body = self._request(url, read_body=True)
        if status != 200:
            raise AuditError(f"public GitHub profile returned HTTP {status}")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuditError("public GitHub profile was not valid UTF-8") from exc

    def status(self, url: str) -> int:
        status, _ = self._request(url, read_body=False)
        return status


def valid_login(login: str) -> bool:
    return bool(LOGIN_PATTERN.fullmatch(login))


def _safe_text(value: Any, *, maximum: int) -> bool:
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


def _valid_github_url(value: Any) -> bool:
    if not _safe_text(value, maximum=2048):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and parsed.path.startswith("/")
        and parsed.username is None
        and parsed.password is None
    )


def _valid_datetime(value: Any) -> bool:
    if not _safe_text(value, maximum=50):
        return False
    match = RFC3339_PATTERN.fullmatch(value)
    if match is None:
        return False
    if (
        int(match.group("hour")) > 23
        or int(match.group("minute")) > 59
        or int(match.group("second")) > 59
    ):
        return False
    offset_hour = match.group("offset_hour")
    offset_minute = match.group("offset_minute")
    if offset_hour is not None and (int(offset_hour) > 23 or int(offset_minute) > 59):
        return False
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _profile_url_matches(login: str, value: Any) -> bool:
    if not _valid_github_url(value):
        return False
    parsed = urlparse(value)
    return (
        parsed.path.casefold() == f"/{login}".casefold()
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _profile_achievements_url_matches(login: str, value: Any) -> bool:
    if not _valid_github_url(value):
        return False
    parsed = urlparse(value)
    return (
        parsed.path.casefold() == f"/{login}".casefold()
        and parsed.query == "tab=achievements"
        and not parsed.params
        and not parsed.fragment
    )


def _achievement_url_matches(login: str, slug: str, value: Any) -> bool:
    if not _valid_github_url(value):
        return False
    parsed = urlparse(value)
    expected_query = urlencode({"achievement": slug, "tab": "achievements"})
    return (
        parsed.path.casefold() == f"/{login}".casefold()
        and parsed.query == expected_query
        and not parsed.params
        and not parsed.fragment
    )


def _repository_identity(name_with_owner: Any, owner_login: Any, url: Any) -> bool:
    if (
        not _safe_text(name_with_owner, maximum=201)
        or not isinstance(owner_login, str)
        or not valid_login(owner_login)
        or not _valid_github_url(url)
    ):
        return False
    parts = name_with_owner.split("/")
    if len(parts) != 2:
        return False
    owner, repository = parts
    if (
        owner.casefold() != owner_login.casefold()
        or not valid_login(owner)
        or not _safe_text(repository, maximum=100)
        or any(character.isspace() or character == "/" for character in repository)
    ):
        return False
    parsed = urlparse(url)
    return (
        parsed.path.rstrip("/").casefold() == f"/{name_with_owner}".casefold()
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _discussion_answer_url_key(
    name_with_owner: str, value: Any
) -> Optional[Tuple[str, str]]:
    if not _valid_github_url(value):
        return None
    parsed = urlparse(value)
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
    positive_integer = re.compile(r"^[1-9][0-9]*$")
    if (
        positive_integer.fullmatch(discussion_number) is None
        or positive_integer.fullmatch(comment_number) is None
        or parsed.params
        or parsed.query
    ):
        return None
    return folded_path, folded_fragment


def _discussion_answer_url_matches(name_with_owner: str, value: Any) -> bool:
    return _discussion_answer_url_key(name_with_owner, value) is not None


def parse_visible_achievements(html: str) -> List[Tuple[str, str]]:
    parser = AchievementHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser can surface malformed entity data.
        raise AuditError("public GitHub profile HTML could not be parsed") from exc
    if not parser.saw_html:
        raise AuditError("public GitHub profile did not contain an HTML document")
    if parser.exceeded_achievement_card_limit:
        raise AuditError(
            f"public GitHub profile exceeded {MAX_ACHIEVEMENT_CARDS} achievement cards"
        )
    if (
        not parser.saw_html_end
        or parser.in_html
        or parser._details_stack
        or parser.saw_unmatched_details_end
        or parser.saw_content_outside_html
    ):
        raise AuditError("public GitHub profile HTML was structurally incomplete")
    if not parser.saw_achievements_route:
        raise AuditError("public GitHub profile did not match the achievements route")

    slug_pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    names_by_slug: Dict[str, str] = {}
    for slug, name in parser.items:
        if not _safe_text(
            slug, maximum=MAX_ACHIEVEMENT_SLUG_LENGTH
        ) or not slug_pattern.fullmatch(slug):
            raise AuditError(
                "public GitHub profile contained an invalid achievement slug"
            )
        if not _safe_text(name, maximum=100):
            raise AuditError(
                "public GitHub profile contained an invalid achievement name"
            )
        previous = names_by_slug.get(slug)
        if previous is not None and previous != name:
            raise AuditError(
                "public GitHub profile assigned two names to one achievement"
            )
        names_by_slug[slug] = name
    card_slugs = set(parser.card_slugs)
    if any(not slug_pattern.fullmatch(slug) for slug in card_slugs):
        raise AuditError("public GitHub profile contained an invalid achievement card")
    if card_slugs != set(names_by_slug):
        raise AuditError("public GitHub achievement card structure was incomplete")
    return list(names_by_slug.items())


def _connection(
    user: Mapping[str, Any], key: str, expected_total: Optional[int]
) -> Tuple[List[Mapping[str, Any]], int, bool, Optional[str]]:
    value = user.get(key)
    if not isinstance(value, dict):
        raise AuditError(f"GraphQL connection missing: {key}")
    nodes = value.get("nodes")
    page_info = value.get("pageInfo")
    total = value.get("totalCount")
    if (
        not isinstance(nodes, list)
        or not isinstance(page_info, dict)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
    ):
        raise AuditError(f"GraphQL connection malformed: {key}")
    if expected_total is not None and total != expected_total:
        raise AuditError(f"GraphQL total changed during pagination: {key}")
    has_next = page_info.get("hasNextPage")
    end_cursor = page_info.get("endCursor")
    if not isinstance(has_next, bool):
        raise AuditError(f"GraphQL pageInfo malformed: {key}")
    if has_next and (not isinstance(end_cursor, str) or not end_cursor):
        raise AuditError(f"GraphQL cursor missing: {key}")
    if not has_next and end_cursor is not None and not isinstance(end_cursor, str):
        raise AuditError(f"GraphQL cursor malformed: {key}")
    typed_nodes: List[Mapping[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise AuditError(f"GraphQL node malformed: {key}")
        typed_nodes.append(node)
    if len(typed_nodes) > 100:
        raise AuditError(f"GraphQL page exceeded the requested size: {key}")
    return typed_nodes, total, has_next, end_cursor


def _collect_graphql(
    client: GitHubClient, requested_login: str
) -> Tuple[Mapping[str, Any], Dict[str, List[Mapping[str, Any]]], Dict[str, Any]]:
    keys = {
        "pulls": "pullRequests",
        "answers": "repositoryDiscussionComments",
        "repositories": "repositories",
    }
    cursors: Dict[str, Optional[str]] = {name: None for name in keys}
    active = {name: True for name in keys}
    totals: Dict[str, Optional[int]] = {name: None for name in keys}
    nodes: Dict[str, List[Mapping[str, Any]]] = {name: [] for name in keys}
    seen_cursors: Dict[str, set[str]] = {name: set() for name in keys}
    canonical_user: Optional[Mapping[str, Any]] = None
    advisory_count: Optional[int] = None
    request_count = 0

    while any(active.values()):
        request_count += 1
        if request_count > 10_000:
            raise AuditError("GraphQL pagination exceeded the safety limit")
        variables = {
            "login": requested_login,
            "advisoryQuery": (
                "repo:github/advisory-database is:pr is:merged "
                f"author:{requested_login}"
            ),
            "pullCursor": cursors["pulls"],
            "answerCursor": cursors["answers"],
            "repositoryCursor": cursors["repositories"],
            "includePulls": active["pulls"],
            "includeAnswers": active["answers"],
            "includeRepositories": active["repositories"],
        }
        document = client.graphql(AUDIT_QUERY, variables)
        if document.get("errors"):
            raise AuditError("GitHub GraphQL reported an error")
        data = document.get("data")
        if not isinstance(data, dict):
            raise AuditError("GitHub GraphQL response omitted data")
        user = data.get("user")
        if not isinstance(user, dict):
            raise AuditError(f"GitHub user not found: {requested_login}")
        login = user.get("login")
        url = user.get("url")
        if (
            not isinstance(login, str)
            or not valid_login(login)
            or login.casefold() != requested_login.casefold()
            or not _profile_url_matches(login, url)
        ):
            raise AuditError("GitHub user metadata was incomplete")

        current_user = {
            "login": login,
            "url": url,
            "isBountyHunter": user.get("isBountyHunter"),
            "isCampusExpert": user.get("isCampusExpert"),
            "isDeveloperProgramMember": user.get("isDeveloperProgramMember"),
            "isGitHubStar": user.get("isGitHubStar"),
        }
        if any(
            not isinstance(current_user[key], bool)
            for key in (
                "isBountyHunter",
                "isCampusExpert",
                "isDeveloperProgramMember",
                "isGitHubStar",
            )
        ):
            raise AuditError("GitHub program signals were incomplete")
        if canonical_user is None:
            canonical_user = current_user
        elif canonical_user != current_user:
            raise AuditError("GitHub user metadata changed during pagination")

        advisory = data.get("advisoryCredits")
        current_advisory = (
            advisory.get("issueCount") if isinstance(advisory, dict) else None
        )
        if (
            not isinstance(current_advisory, int)
            or isinstance(current_advisory, bool)
            or current_advisory < 0
        ):
            raise AuditError("security advisory evidence was incomplete")
        if advisory_count is None:
            advisory_count = current_advisory
        elif advisory_count != current_advisory:
            raise AuditError("security advisory evidence changed during pagination")

        rate_limit = data.get("rateLimit")
        cost = rate_limit.get("cost") if isinstance(rate_limit, dict) else None
        remaining = (
            rate_limit.get("remaining") if isinstance(rate_limit, dict) else None
        )
        reset_at = rate_limit.get("resetAt") if isinstance(rate_limit, dict) else None
        if (
            not isinstance(cost, int)
            or isinstance(cost, bool)
            or cost < 0
            or not isinstance(remaining, int)
            or isinstance(remaining, bool)
            or remaining < 0
            or not _valid_datetime(reset_at)
        ):
            raise AuditError("GraphQL rate-limit metadata was incomplete")
        for name, key in keys.items():
            if not active[name]:
                continue
            page_nodes, total, has_next, end_cursor = _connection(
                user, key, totals[name]
            )
            totals[name] = total
            nodes[name].extend(page_nodes)
            if len(nodes[name]) > total:
                raise AuditError(f"GraphQL pagination duplicated nodes: {key}")
            if has_next:
                if not isinstance(end_cursor, str) or not end_cursor:
                    raise AuditError(f"GraphQL cursor missing: {key}")
                if end_cursor in seen_cursors[name]:
                    raise AuditError(f"GraphQL cursor repeated: {key}")
                seen_cursors[name].add(end_cursor)
                cursors[name] = end_cursor
            else:
                active[name] = False

    for name, key in keys.items():
        if totals[name] is None or len(nodes[name]) != totals[name]:
            raise AuditError(f"GraphQL pagination was incomplete: {key}")
        identifiers = [node.get("id") for node in nodes[name]]
        if any(not _safe_text(identifier, maximum=200) for identifier in identifiers):
            raise AuditError(f"GraphQL node ID was incomplete: {key}")
        if len(set(identifiers)) != len(identifiers):
            raise AuditError(f"GraphQL pagination duplicated node IDs: {key}")
    if canonical_user is None or advisory_count is None:
        raise AuditError("GitHub GraphQL pagination produced no complete snapshot")
    health = {
        "advisory_database_merged_contributions": advisory_count,
    }
    return canonical_user, nodes, health


def _require_bool(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise AuditError(f"GitHub program signal was incomplete: {key}")
    return value


def _public_evidence(
    login: str,
    nodes: Mapping[str, List[Mapping[str, Any]]],
    advisory_count: int,
) -> Mapping[str, Any]:
    login_folded = login.casefold()
    public_pulls: List[Mapping[str, Any]] = []
    outside_namespace_pulls = 0
    for node in nodes["pulls"]:
        author = node.get("author")
        author_login = author.get("login") if isinstance(author, dict) else None
        repository = node.get("repository")
        owner = repository.get("owner") if isinstance(repository, dict) else None
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        private = repository.get("isPrivate") if isinstance(repository, dict) else None
        if (
            not isinstance(author_login, str)
            or not valid_login(author_login)
            or author_login.casefold() != login_folded
            or not isinstance(owner_login, str)
            or not valid_login(owner_login)
            or not isinstance(private, bool)
        ):
            raise AuditError("merged pull-request evidence was malformed")
        if private:
            continue
        public_pulls.append(node)
        if owner_login.casefold() != login_folded:
            outside_namespace_pulls += 1

    answer_evidence: List[Mapping[str, Any]] = []
    outside_namespace_answers = 0
    seen_answer_urls: set[Tuple[str, str]] = set()
    for node in nodes["answers"]:
        if node.get("isAnswer") is not True:
            raise AuditError("accepted-answer query returned a non-answer")
        author = node.get("author")
        author_login = author.get("login") if isinstance(author, dict) else None
        discussion = node.get("discussion")
        repository = (
            discussion.get("repository") if isinstance(discussion, dict) else None
        )
        owner = repository.get("owner") if isinstance(repository, dict) else None
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        name_with_owner = (
            repository.get("nameWithOwner") if isinstance(repository, dict) else None
        )
        repository_url = repository.get("url") if isinstance(repository, dict) else None
        private = repository.get("isPrivate") if isinstance(repository, dict) else None
        url = node.get("url")
        created_at = node.get("createdAt")
        upvotes = node.get("upvoteCount")
        if (
            not isinstance(author_login, str)
            or not valid_login(author_login)
            or author_login.casefold() != login_folded
            or not isinstance(owner_login, str)
            or not valid_login(owner_login)
            or not _repository_identity(name_with_owner, owner_login, repository_url)
            or not isinstance(private, bool)
            or not isinstance(name_with_owner, str)
            or not _discussion_answer_url_matches(name_with_owner, url)
            or not _valid_datetime(created_at)
            or not isinstance(upvotes, int)
            or isinstance(upvotes, bool)
            or upvotes < 0
        ):
            raise AuditError("accepted-answer evidence was malformed")
        if private:
            continue
        answer_url_key = _discussion_answer_url_key(name_with_owner, url)
        if answer_url_key is None:  # Already checked above; retain fail-closed defense.
            raise AuditError("accepted-answer evidence was malformed")
        if answer_url_key in seen_answer_urls:
            raise AuditError("accepted-answer evidence duplicated a public URL")
        seen_answer_urls.add(answer_url_key)
        owned_by_subject = owner_login.casefold() == login_folded
        if not owned_by_subject:
            outside_namespace_answers += 1
        answer_evidence.append(
            {
                "url": url,
                "repository": name_with_owner,
                "owned_by_subject": owned_by_subject,
                "created_at": created_at,
                "upvotes": upvotes,
            }
        )
    answer_evidence.sort(key=lambda item: (item["created_at"], item["url"]))

    repositories: List[Mapping[str, Any]] = []
    seen_repository_names: set[str] = set()
    seen_repository_urls: set[str] = set()
    for node in nodes["repositories"]:
        owner = node.get("owner")
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        name = node.get("nameWithOwner")
        url = node.get("url")
        private = node.get("isPrivate")
        fork = node.get("isFork")
        stars = node.get("stargazerCount")
        forks = node.get("forkCount")
        if (
            not isinstance(owner_login, str)
            or not valid_login(owner_login)
            or owner_login.casefold() != login_folded
            or not _repository_identity(name, owner_login, url)
            or private is not False
            or fork is not False
            or not isinstance(stars, int)
            or isinstance(stars, bool)
            or stars < 0
            or not isinstance(forks, int)
            or isinstance(forks, bool)
            or forks < 0
        ):
            raise AuditError("owned public repository evidence was malformed")
        name_key = name.casefold()
        url_key = urlparse(url).path.rstrip("/").casefold()
        if name_key in seen_repository_names or url_key in seen_repository_urls:
            raise AuditError("owned public repository evidence was duplicated")
        seen_repository_names.add(name_key)
        seen_repository_urls.add(url_key)
        repositories.append(
            {
                "name_with_owner": name,
                "url": url,
                "stars": stars,
                "forks": forks,
            }
        )
    repositories.sort(key=lambda item: (-item["stars"], item["name_with_owner"]))

    return {
        "merged_pull_requests": {
            "public_total": len(public_pulls),
            "outside_personal_namespace_total": outside_namespace_pulls,
        },
        "accepted_discussion_answers": {
            "public_total": len(answer_evidence),
            "outside_personal_namespace_total": outside_namespace_answers,
            "evidence": answer_evidence,
        },
        "owned_public_nonfork_repositories": {
            "total": len(repositories),
            "total_stars": sum(item["stars"] for item in repositories),
            "top_by_stars": repositories[0] if repositories else None,
        },
        "advisory_database": {
            "merged_contributions": advisory_count,
        },
    }


def _visible_inventory(
    login: str, web: WebClient
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]], str]:
    profile_url = f"https://github.com/{login}?{urlencode({'tab': 'achievements'})}"
    observed_items = parse_visible_achievements(web.profile_html(profile_url))
    known_name_by_slug = {slug: name for name, slug in KNOWN_ACHIEVEMENTS}
    display_order = {slug: index for index, (_, slug) in enumerate(KNOWN_ACHIEVEMENTS)}
    observed_items.sort(
        key=lambda item: (display_order.get(item[0], len(display_order)), item[0])
    )
    achievements: List[Mapping[str, Any]] = []
    for slug, name in observed_items:
        known_name = known_name_by_slug.get(slug)
        if known_name is not None and name != known_name:
            raise AuditError("known achievement slug and name disagreed")
        url = f"https://github.com/{login}?" + urlencode(
            {"achievement": slug, "tab": "achievements"}
        )
        achievements.append({"name": name, "slug": slug, "url": url})

    endpoints: List[Mapping[str, Any]] = []
    for name, slug in KNOWN_ACHIEVEMENTS:
        url = f"https://github.com/{login}?" + urlencode(
            {"achievement": slug, "tab": "achievements"}
        )
        status = web.status(url)
        if status not in (200, 404):
            raise AuditError(
                "achievement endpoint returned unexpected HTTP status for "
                f"{slug}: {status}"
            )
        endpoints.append({"name": name, "slug": slug, "status": status, "url": url})

    for achievement in achievements:
        if achievement["slug"] in known_name_by_slug:
            continue
        status = web.status(achievement["url"])
        if status != 200:
            raise AuditError(
                "visible unknown achievement endpoint did not return HTTP 200"
            )
        endpoints.append({**achievement, "status": status})

    parsed_visible = sorted(achievement["slug"] for achievement in achievements)
    endpoint_visible = sorted(
        endpoint["slug"] for endpoint in endpoints if endpoint["status"] == 200
    )
    if parsed_visible != endpoint_visible:
        raise AuditError("profile badges and achievement detail endpoints disagreed")
    return achievements, endpoints, profile_url


def validate_report_semantics(report: Mapping[str, Any]) -> None:
    """Validate cross-field invariants that JSON Schema cannot express."""

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AuditError(f"report semantic invariant failed: {message}")

    try:
        require(report["complete"] is True, "report must be complete")
        require(_valid_datetime(report["generated_at"]), "invalid generated_at")

        subject = report["subject"]
        login = subject["login"]
        require(
            isinstance(login, str)
            and valid_login(login)
            and _profile_url_matches(login, subject["url"]),
            "invalid subject identity",
        )

        visible = report["visible_achievements"]
        endpoints = report["achievement_endpoints"]
        visible_by_slug = {item["slug"]: item for item in visible}
        endpoint_by_slug = {item["slug"]: item for item in endpoints}
        known_name_by_slug = {slug: name for name, slug in KNOWN_ACHIEVEMENTS}
        require(len(visible_by_slug) == len(visible), "duplicate visible slug")
        require(len(endpoint_by_slug) == len(endpoints), "duplicate endpoint slug")
        require(
            set(endpoint_by_slug) == set(known_name_by_slug) | set(visible_by_slug),
            "achievement endpoint inventory is incomplete",
        )
        require(
            len({item["url"] for item in endpoints}) == len(endpoints),
            "duplicate achievement endpoint URL",
        )
        rendered_slugs = {item["slug"] for item in endpoints if item["status"] == 200}
        require(
            rendered_slugs == set(visible_by_slug),
            "visible achievements and endpoint statuses disagree",
        )
        for slug, item in visible_by_slug.items():
            endpoint = endpoint_by_slug[slug]
            require(
                item["name"] == endpoint["name"] and item["url"] == endpoint["url"],
                "visible achievement and endpoint identity disagree",
            )
        for slug, item in endpoint_by_slug.items():
            known_name = known_name_by_slug.get(slug)
            require(
                _safe_text(item["name"], maximum=100)
                and (known_name is None or item["name"] == known_name)
                and _achievement_url_matches(login, slug, item["url"]),
                "achievement endpoint identity is invalid",
            )

        evidence = report["public_evidence"]
        pulls = evidence["merged_pull_requests"]
        require(
            pulls["outside_personal_namespace_total"] <= pulls["public_total"],
            "pull-request namespace count exceeds public total",
        )

        answers = evidence["accepted_discussion_answers"]
        answer_items = answers["evidence"]
        require(
            answers["public_total"] == len(answer_items),
            "accepted-answer total differs from evidence length",
        )
        require(
            answers["outside_personal_namespace_total"]
            == sum(not item["owned_by_subject"] for item in answer_items),
            "accepted-answer namespace total disagrees with evidence",
        )
        answer_url_keys = set()
        for item in answer_items:
            repository = item["repository"]
            repository_parts = (
                repository.split("/") if isinstance(repository, str) else []
            )
            require(
                len(repository_parts) == 2
                and valid_login(repository_parts[0])
                and _discussion_answer_url_matches(repository, item["url"]),
                "accepted-answer identity is invalid",
            )
            require(
                item["owned_by_subject"]
                == (repository_parts[0].casefold() == login.casefold()),
                "accepted-answer ownership is invalid",
            )
            require(
                _valid_datetime(item["created_at"]),
                "accepted-answer timestamp is invalid",
            )
            answer_url_key = _discussion_answer_url_key(repository, item["url"])
            require(
                answer_url_key is not None and answer_url_key not in answer_url_keys,
                "duplicate accepted-answer URL",
            )
            answer_url_keys.add(answer_url_key)

        repositories = evidence["owned_public_nonfork_repositories"]
        total_repositories = repositories["total"]
        top_repository = repositories["top_by_stars"]
        require(
            (total_repositories == 0) == (top_repository is None),
            "repository total and top repository disagree",
        )
        if top_repository is None:
            require(
                repositories["total_stars"] == 0,
                "zero repositories must have zero stars",
            )
        else:
            require(
                _repository_identity(
                    top_repository["name_with_owner"],
                    login,
                    top_repository["url"],
                ),
                "top repository identity is invalid",
            )
            require(
                top_repository["stars"] <= repositories["total_stars"],
                "top repository stars exceed aggregate stars",
            )

        profile_health = report["source_health"]["profile"]
        require(
            profile_health["complete"] is True
            and _profile_achievements_url_matches(login, profile_health["url"]),
            "profile source identity is invalid",
        )
        require(
            report["source_health"]["graphql"]["complete"] is True,
            "GraphQL source must be complete",
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise AuditError("report semantic invariant failed: malformed report") from exc


def build_report(
    requested_login: Optional[str] = None,
    *,
    github: Optional[GitHubClient] = None,
    web: Optional[WebClient] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Mapping[str, Any]:
    github = github or GitHubClient()
    web = web or WebClient()
    login = requested_login or github.authenticated_login()
    if not valid_login(login):
        raise AuditError("invalid GitHub login")

    user, nodes, graphql_health = _collect_graphql(github, login)
    canonical_login = user["login"]
    if not isinstance(canonical_login, str) or not valid_login(canonical_login):
        raise AuditError("GitHub returned an invalid canonical login")
    advisory_count = graphql_health["advisory_database_merged_contributions"]
    public_evidence = _public_evidence(canonical_login, nodes, advisory_count)
    visible, endpoints, profile_url = _visible_inventory(canonical_login, web)

    clock = now or (lambda: datetime.now(timezone.utc))
    generated = clock()
    if generated.tzinfo is None:
        raise AuditError("report clock must be timezone-aware")
    generated_at = generated.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    generated_at = generated_at.replace("+00:00", "Z")

    report = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "subject": {"login": canonical_login, "url": user["url"]},
        "complete": True,
        "scope": {
            "visibility": "PUBLIC_ONLY",
            "anti_farming": True,
            "predicts_unlocks": False,
            "outside_personal_namespace_definition": (
                "REPOSITORY_OWNER_DIFFERS_FROM_SUBJECT_LOGIN"
            ),
        },
        "visible_achievements": visible,
        "achievement_endpoints": endpoints,
        "public_evidence": public_evidence,
        "program_signals": {
            "developer_program_member": _require_bool(user, "isDeveloperProgramMember"),
            "security_bug_bounty_hunter": _require_bool(user, "isBountyHunter"),
            "campus_expert": _require_bool(user, "isCampusExpert"),
            "github_star": _require_bool(user, "isGitHubStar"),
        },
        "source_health": {
            "graphql": {"complete": True},
            "profile": {
                "complete": True,
                "url": profile_url,
            },
        },
        "sources": {
            "official_profile_reference": OFFICIAL_PROFILE_REFERENCE,
            "official_discussions_graphql_reference": OFFICIAL_DISCUSSIONS_REFERENCE,
            "official_gh_api_pagination_reference": OFFICIAL_GH_API_REFERENCE,
        },
        "limitations": [
            (
                "GitHub documents achievements as a public preview and does not "
                "publish a complete official criteria or tier table."
            ),
            (
                "A missing visible achievement means only that it is not rendered "
                "on the public profile at audit time; the user may have hidden "
                "achievements."
            ),
            (
                "Public event counts are supporting evidence, not proof that GitHub "
                "considers an achievement eligible or indexed."
            ),
            (
                "Private events hidden from the viewer are neither enumerated nor "
                "disclosed."
            ),
            (
                "Outside the personal namespace means only that the repository owner "
                "login differs; it does not prove organizational independence."
            ),
            "Live GitHub data can change between paginated requests.",
            "Program signals are profile-program memberships, not GitHub Achievements.",
            (
                "This command is read-only and never creates activity, comments, "
                "stars, reviews, or co-authorship."
            ),
        ],
    }
    validate_report_semantics(report)
    return report


def render_text(report: Mapping[str, Any]) -> str:
    visible = report["visible_achievements"]
    visible_text = ", ".join(item["name"] for item in visible) or "none"
    evidence = report["public_evidence"]
    pulls = evidence["merged_pull_requests"]
    answers = evidence["accepted_discussion_answers"]
    answer_outside = answers["outside_personal_namespace_total"]
    repositories = evidence["owned_public_nonfork_repositories"]
    top = repositories["top_by_stars"]
    top_text = "none"
    if top is not None:
        star_suffix = "" if top["stars"] == 1 else "s"
        top_text = f"{top['name_with_owner']} — {top['stars']} star{star_suffix}"
    programs = report["program_signals"]

    def yes_no(value: bool) -> str:
        return "yes" if value else "no"

    lines = [
        f"GH ACHIEVEMENT AUDIT — {report['subject']['login']}",
        f"Snapshot: {report['generated_at']}",
        f"Visible now: {visible_text}",
        "",
        "Public evidence (read-only):",
        (
            f"  Merged pull requests: {pulls['public_total']} "
            f"({pulls['outside_personal_namespace_total']} outside personal namespace)"
        ),
        (
            f"  Accepted Discussion answers: {answers['public_total']} "
            f"({answer_outside} outside personal namespace)"
        ),
        f"  Owned public non-fork repositories: {repositories['total']}",
        f"  Stars across those repositories: {repositories['total_stars']}",
        f"  Top repository: {top_text}",
        (
            "  Merged GitHub Advisory Database contributions: "
            f"{evidence['advisory_database']['merged_contributions']}"
        ),
        "",
        "Public program signals (not Achievements):",
        f"  Developer Program: {yes_no(programs['developer_program_member'])}",
        (
            "  Security Bug Bounty Hunter: "
            f"{yes_no(programs['security_bug_bounty_hunter'])}"
        ),
        f"  Campus Expert: {yes_no(programs['campus_expert'])}",
        f"  GitHub Star: {yes_no(programs['github_star'])}",
        "",
        (
            "Visible profile state and public event evidence are reported separately; "
            "no unpublished threshold is treated as fact."
        ),
        "STATUS: COMPLETE",
    ]
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh achievement-audit",
        description=(
            "Build a read-only, evidence-first snapshot of visible GitHub "
            "achievements and public supporting events."
        ),
    )
    parser.add_argument(
        "login",
        nargs="?",
        help="GitHub.com login; defaults to the authenticated user",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the versioned JSON report"
    )
    parser.add_argument(
        "--version", action="version", version=f"gh-achievement-audit {__version__}"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(args.login)
        if args.json:
            output = (
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            )
        else:
            output = render_text(report)
        sys.stdout.write(output)
        return 0
    except AuditError as exc:
        print(f"gh achievement-audit: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
