from __future__ import annotations

import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from http.client import IncompleteRead
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from jsonschema import Draft202012Validator, FormatChecker
from validate_lock import validate_policy

from achievement_audit.cli import (
    AUDIT_QUERY,
    AuditError,
    GitHubClient,
    WebClient,
    build_report,
    main,
    parse_visible_achievements,
    render_text,
    valid_login,
    validate_report_semantics,
)

FIXED_TIME = datetime(2026, 8, 28, 10, 40, tzinfo=timezone.utc)


def connection(nodes, total, has_next=False, end_cursor=None):
    return {
        "totalCount": total,
        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        "nodes": nodes,
    }


class FakeGitHub:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.calls = []
        self.auth_calls = 0

    def authenticated_login(self):
        self.auth_calls += 1
        return "sirhegel"

    def graphql(self, query, variables):
        self.calls.append(deepcopy(dict(variables)))
        self.last_query = query
        call_number = len(self.calls)
        user = {
            "login": "SirHegel",
            "url": "https://github.com/SirHegel",
            "isBountyHunter": False,
            "isCampusExpert": False,
            "isDeveloperProgramMember": True,
            "isGitHubStar": False,
        }
        if self.mode == "bad_user_url":
            user["url"] = "https://example.com/SirHegel"
        if self.mode == "malformed_user_url":
            user["url"] = "https://[::1"
        if self.mode == "program_drift" and call_number > 1:
            user["isGitHubStar"] = True

        if variables["includePulls"]:
            if self.mode == "empty":
                user["pullRequests"] = connection([], 0)
            elif self.mode == "private_pagination":
                if variables["pullCursor"] is None:
                    private_pulls = [
                        {
                            "id": f"private-pull-{index}",
                            "author": {"login": "SirHegel"},
                            "repository": {
                                "isPrivate": True,
                                "owner": {"login": "private-owner"},
                            },
                        }
                        for index in range(100)
                    ]
                    user["pullRequests"] = connection(
                        private_pulls,
                        101,
                        has_next=True,
                        end_cursor="private-page-2",
                    )
                else:
                    user["pullRequests"] = connection(
                        [
                            {
                                "id": "private-pull-100",
                                "author": {"login": "SirHegel"},
                                "repository": {
                                    "isPrivate": True,
                                    "owner": {"login": "private-owner"},
                                },
                            }
                        ],
                        101,
                    )
            elif variables["pullCursor"] is None:
                pull_nodes = [
                    {
                        "id": "pull-1",
                        "author": {"login": "SirHegel"},
                        "repository": {
                            "isPrivate": False,
                            "owner": {"login": "SirHegel"},
                        },
                    },
                    {
                        "id": "pull-private",
                        "author": {"login": "SirHegel"},
                        "repository": {
                            "isPrivate": True,
                            "owner": {"login": "private-owner"},
                        },
                    },
                ]
                user["pullRequests"] = connection(
                    pull_nodes, 3, has_next=True, end_cursor="pull-2"
                )
            else:
                total = 4 if self.mode == "changing_total" else 3
                repeat = self.mode == "cursor_repeat"
                user["pullRequests"] = connection(
                    [
                        {
                            "id": (
                                "pull-1" if self.mode == "duplicate_id" else "pull-2"
                            ),
                            "author": {
                                "login": (
                                    "someone-else"
                                    if self.mode == "unexpected_author"
                                    else "SirHegel"
                                )
                            },
                            "repository": {
                                "isPrivate": False,
                                "owner": {"login": "encode"},
                            },
                        }
                    ],
                    total,
                    has_next=repeat,
                    end_cursor="pull-2" if repeat else "pull-3",
                )

        if variables["includeAnswers"]:
            answer_nodes = (
                []
                if self.mode == "empty"
                else [
                    {
                        "id": "answer-1",
                        "url": "https://github.com/SirHegel/game/discussions/1#discussioncomment-1",
                        "isAnswer": self.mode != "non_answer",
                        "upvoteCount": 1,
                        "createdAt": "2026-08-01T00:00:00Z",
                        "author": {"login": "SirHegel"},
                        "discussion": {
                            "author": {"login": "SirHegel"},
                            "repository": {
                                "nameWithOwner": "SirHegel/game",
                                "url": "https://github.com/SirHegel/game",
                                "isPrivate": False,
                                "owner": {"login": "SirHegel"},
                            },
                        },
                    },
                    {
                        "id": "answer-2",
                        "url": "https://github.com/python-poetry/poetry/discussions/2#discussioncomment-2",
                        "isAnswer": True,
                        "upvoteCount": 2,
                        "createdAt": "2026-08-02T00:00:00Z",
                        "author": {"login": "SirHegel"},
                        "discussion": {
                            "author": {"login": "poetry-user"},
                            "repository": {
                                "nameWithOwner": "python-poetry/poetry",
                                "url": "https://github.com/python-poetry/poetry",
                                "isPrivate": False,
                                "owner": {"login": "python-poetry"},
                            },
                        },
                    },
                    {
                        "id": "answer-private",
                        "url": "https://github.com/private-owner/private/discussions/3#discussioncomment-3",
                        "isAnswer": True,
                        "upvoteCount": 0,
                        "createdAt": "2026-08-03T00:00:00Z",
                        "author": {"login": "SirHegel"},
                        "discussion": {
                            "author": {"login": "private-owner"},
                            "repository": {
                                "nameWithOwner": "private-owner/private",
                                "url": "https://github.com/private-owner/private",
                                "isPrivate": True,
                                "owner": {"login": "private-owner"},
                            },
                        },
                    },
                ]
            )
            if self.mode == "bad_answer_url":
                answer_nodes[0]["url"] = "https://example.com/comment"
            if self.mode == "negative_upvotes":
                answer_nodes[0]["upvoteCount"] = -1
            if self.mode == "bad_answer_date":
                answer_nodes[0]["createdAt"] = "not-a-date"
            if self.mode == "iso_week_answer_date":
                answer_nodes[0]["createdAt"] = "2026-W35-5T10:00:00+00:00"
            if self.mode == "hour_24_answer_date":
                answer_nodes[0]["createdAt"] = "2026-08-28T24:00:00Z"
            if self.mode == "repository_mismatch":
                answer_nodes[0]["discussion"]["repository"]["url"] = (
                    "https://github.com/SirHegel/other"
                )
            if self.mode == "noncanonical_answer_url":
                answer_nodes[0]["url"] = (
                    "https://github.com/SirHegel/game/discussions/1/"
                    "#discussioncomment-1"
                )
            if self.mode == "duplicate_answer_url":
                answer_nodes[1]["url"] = answer_nodes[0]["url"]
                answer_nodes[1]["discussion"] = deepcopy(answer_nodes[0]["discussion"])
            if self.mode == "ghost_discussion_author":
                answer_nodes[0]["discussion"]["author"] = None
            if self.mode == "bad_discussion_author":
                answer_nodes[0]["discussion"]["author"] = {"login": "-not-valid-"}
            user["repositoryDiscussionComments"] = connection(answer_nodes, 3)
            if self.mode == "empty":
                user["repositoryDiscussionComments"] = connection([], 0)

        if variables["includeRepositories"]:
            repositories = (
                []
                if self.mode == "empty"
                else [
                    {
                        "id": "repo-1",
                        "nameWithOwner": "SirHegel/profile",
                        "url": "https://github.com/SirHegel/profile",
                        "isPrivate": False,
                        "isFork": False,
                        "stargazerCount": 1,
                        "forkCount": 0,
                        "owner": {"login": "SirHegel"},
                    },
                    {
                        "id": "repo-2",
                        "nameWithOwner": "SirHegel/audit",
                        "url": "https://github.com/SirHegel/audit",
                        "isPrivate": False,
                        "isFork": False,
                        "stargazerCount": 3,
                        "forkCount": 1,
                        "owner": {"login": "SirHegel"},
                    },
                ]
            )
            if self.mode == "negative_stars":
                repositories[0]["stargazerCount"] = -1
            if self.mode == "duplicate_repository":
                repositories[1]["nameWithOwner"] = repositories[0]["nameWithOwner"]
                repositories[1]["url"] = repositories[0]["url"]
            user["repositories"] = connection(repositories, 2)
            if self.mode == "empty":
                user["repositories"] = connection([], 0)

        if self.mode == "missing_user":
            user = None
        response = {
            "data": {
                "user": user,
                "advisoryCredits": {"issueCount": 1},
                "rateLimit": {
                    "cost": 1,
                    "remaining": 5000 - call_number,
                    "resetAt": "2026-08-28T11:00:00Z",
                },
            }
        }
        if self.mode == "negative_rate":
            response["data"]["rateLimit"]["remaining"] = -1
        if self.mode == "graphql_error":
            response["errors"] = [{"message": "simulated"}]
        return response


class StaggeredGitHub:
    """Exercise three independent connections that finish on different calls."""

    def __init__(self):
        self.calls = []

    def graphql(self, query, variables):
        self.calls.append(deepcopy(dict(variables)))
        user = {
            "login": "SirHegel",
            "url": "https://github.com/SirHegel",
            "isBountyHunter": False,
            "isCampusExpert": False,
            "isDeveloperProgramMember": False,
            "isGitHubStar": False,
        }

        if variables["includePulls"]:
            offset = 0 if variables["pullCursor"] is None else 100
            size = 100 if offset == 0 else 1
            pulls = [
                {
                    "id": f"staggered-pull-{index}",
                    "author": {"login": "SirHegel"},
                    "repository": {
                        "isPrivate": False,
                        "owner": {"login": "SirHegel"},
                    },
                }
                for index in range(offset, offset + size)
            ]
            user["pullRequests"] = connection(
                pulls,
                101,
                has_next=offset == 0,
                end_cursor="pull-page-2" if offset == 0 else "pull-complete",
            )

        if variables["includeAnswers"]:
            cursor = variables["answerCursor"]
            offset = {None: 0, "answer-page-2": 100, "answer-page-3": 200}[cursor]
            size = 100 if offset < 200 else 1
            answers = [
                {
                    "id": f"staggered-answer-{index}",
                    "url": (
                        "https://github.com/SirHegel/game/discussions/"
                        f"{index + 1}#discussioncomment-{index + 1}"
                    ),
                    "isAnswer": True,
                    "upvoteCount": 0,
                    "createdAt": "2026-08-01T00:00:00Z",
                    "author": {"login": "SirHegel"},
                    "discussion": {
                        "author": {"login": "SirHegel"},
                        "repository": {
                            "nameWithOwner": "SirHegel/game",
                            "url": "https://github.com/SirHegel/game",
                            "isPrivate": False,
                            "owner": {"login": "SirHegel"},
                        },
                    },
                }
                for index in range(offset, offset + size)
            ]
            next_cursor = {
                0: "answer-page-2",
                100: "answer-page-3",
                200: "answer-complete",
            }[offset]
            user["repositoryDiscussionComments"] = connection(
                answers,
                201,
                has_next=offset < 200,
                end_cursor=next_cursor,
            )

        if variables["includeRepositories"]:
            user["repositories"] = connection(
                [
                    {
                        "id": "staggered-repository-1",
                        "nameWithOwner": "SirHegel/game",
                        "url": "https://github.com/SirHegel/game",
                        "isPrivate": False,
                        "isFork": False,
                        "stargazerCount": 0,
                        "forkCount": 0,
                        "owner": {"login": "SirHegel"},
                    }
                ],
                1,
            )

        return {
            "data": {
                "user": user,
                "advisoryCredits": {"issueCount": 0},
                "rateLimit": {
                    "cost": 1,
                    "remaining": 4999 - len(self.calls),
                    "resetAt": "2026-08-28T11:00:00Z",
                },
            }
        }


class FakeWeb:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.profile_calls = []
        self.status_calls = []

    def profile_html(self, url):
        self.profile_calls.append(url)
        if self.mode == "invalid_html":
            return "not html"
        if self.mode == "none":
            return (
                '<html><head><meta name="route-controller" '
                'content="profiles_achievements"></head><body></body></html>'
            )
        if self.mode == "tier_legacy":
            return (
                '<html><head><meta name="route-controller" '
                'content="profiles_achievements"></head><body>'
                '<details data-achievement-slug="pull-shark">'
                '<img alt="Achievement: Pull Shark"></details>'
                '<details data-achievement-slug="arctic-code-vault-contributor">'
                '<img alt="Achievement: Arctic Code Vault Contributor"></details>'
                "</body></html>"
            )
        unknown = (
            '<details data-achievement-slug="future-badge">'
            '<img alt="Achievement: Future Badge"></details>'
            if self.mode == "unknown"
            else ""
        )
        return (
            '<html><head><meta name="route-controller" '
            'content="profiles_achievements"></head><body>'
            '<details data-achievement-slug="yolo">'
            '<img alt="Achievement: YOLO"></details>'
            '<details data-achievement-slug="quickdraw">'
            '<img alt="Achievement: Quickdraw"></details>'
            '<details data-achievement-slug="yolo">'
            '<img alt="Achievement: YOLO"></details>'
            f"{unknown}</body></html>"
        )

    def status(self, url):
        self.status_calls.append(url)
        slug = parse_qs(urlparse(url).query)["achievement"][0]
        if self.mode == "mismatch" and slug == "pull-shark":
            return 200
        if self.mode == "unknown" and slug == "future-badge":
            return 200
        if self.mode == "tier_legacy":
            return (
                200 if slug in {"pull-shark", "arctic-code-vault-contributor"} else 404
            )
        if self.mode == "none":
            return 404
        return 200 if slug in {"quickdraw", "yolo"} else 404


class StubHTTPResponse:
    def __init__(self, url, body=b"", status=200):
        self.url = url
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, amount):
        return self.body[:amount]


def report(mode="normal", web_mode="normal"):
    return build_report(
        "sirhegel",
        github=FakeGitHub(mode),
        web=FakeWeb(web_mode),
        now=lambda: FIXED_TIME,
    )


def semantic_mutations():
    """Return schema-valid reports with deliberately contradictory identities."""
    mutations = {}

    answer_total = deepcopy(report())
    answer_total["public_evidence"]["accepted_discussion_answers"]["public_total"] = 0
    mutations["answer total"] = answer_total

    repository_top = deepcopy(report())
    repository_top["public_evidence"]["owned_public_nonfork_repositories"]["total"] = 0
    mutations["repository total"] = repository_top

    endpoint_status = deepcopy(report())
    endpoint_status["achievement_endpoints"][0]["status"] = 404
    mutations["endpoint status"] = endpoint_status

    subject_identity = deepcopy(report())
    subject_identity["subject"]["url"] = "https://github.com/another-user"
    mutations["subject identity"] = subject_identity

    top_identity = deepcopy(report())
    top = top_identity["public_evidence"]["owned_public_nonfork_repositories"][
        "top_by_stars"
    ]
    top["name_with_owner"] = "another-user/audit"
    top["url"] = "https://github.com/another-user/audit"
    mutations["top repository identity"] = top_identity

    profile_identity = deepcopy(report())
    profile_identity["source_health"]["profile"]["url"] = (
        "https://github.com/another-user?tab=achievements"
    )
    mutations["profile source identity"] = profile_identity

    answer_identity = deepcopy(report())
    answer_identity["public_evidence"]["accepted_discussion_answers"]["evidence"][0][
        "owned_by_subject"
    ] = False
    answer_identity["public_evidence"]["accepted_discussion_answers"][
        "outside_personal_namespace_total"
    ] = 2
    mutations["answer identity"] = answer_identity

    answer_self_accepted = deepcopy(report())
    answer_self_accepted["public_evidence"]["accepted_discussion_answers"][
        "self_accepted_total"
    ] = 2
    mutations["answer self-accepted total"] = answer_self_accepted

    achievement_identity = deepcopy(report())
    foreign_url = (
        "https://github.com/another-user?achievement=quickdraw&tab=achievements"
    )
    achievement_identity["visible_achievements"][0]["url"] = foreign_url
    achievement_identity["achievement_endpoints"][0]["url"] = foreign_url
    mutations["achievement endpoint identity"] = achievement_identity

    endpoint_inventory = deepcopy(report())
    replaced_endpoint = next(
        item
        for item in endpoint_inventory["achievement_endpoints"]
        if item["slug"] == "pull-shark"
    )
    replaced_endpoint.update(
        {
            "name": "Invented Hidden Achievement",
            "slug": "invented-hidden-achievement",
            "url": (
                "https://github.com/SirHegel?achievement="
                "invented-hidden-achievement&tab=achievements"
            ),
        }
    )
    mutations["achievement endpoint inventory"] = endpoint_inventory

    for label, invalid_name in (
        ("known achievement name", "Definitely Not YOLO"),
        ("achievement control character", "YOLO\x1b[31m"),
    ):
        achievement_name = deepcopy(report())
        for collection in ("visible_achievements", "achievement_endpoints"):
            item = next(
                entry
                for entry in achievement_name[collection]
                if entry["slug"] == "yolo"
            )
            item["name"] = invalid_name
        mutations[label] = achievement_name

    return mutations


class LoginTests(unittest.TestCase):
    def test_valid_logins(self):
        for login in ("a", "SirHegel", "a-b", "a" * 39):
            with self.subTest(login=login):
                self.assertTrue(valid_login(login))

    def test_invalid_logins(self):
        for login in ("", "-start", "end-", "has space", "a" * 40, "owner/repo"):
            with self.subTest(login=login):
                self.assertFalse(valid_login(login))

    def test_invalid_login_stops_before_graphql(self):
        github = FakeGitHub()
        with self.assertRaisesRegex(AuditError, "invalid GitHub login"):
            build_report("bad/login", github=github, web=FakeWeb())
        self.assertEqual(github.calls, [])


class ProfileTests(unittest.TestCase):
    def test_parser_deduplicates_and_preserves_first_order(self):
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head>"
            "<details data-achievement-slug='yolo'>"
            "<img alt='Achievement: YOLO'></details>"
            "<details data-achievement-slug='quickdraw'>"
            "<img alt='Achievement: Quickdraw'></details>"
            "<details data-achievement-slug='yolo'>"
            "<img alt='Achievement: YOLO'></details></html>"
        )
        self.assertEqual(
            parse_visible_achievements(html),
            [("yolo", "YOLO"), ("quickdraw", "Quickdraw")],
        )

    def test_parser_requires_html_document(self):
        with self.assertRaisesRegex(AuditError, "HTML document"):
            parse_visible_achievements("no document")

    def test_parser_requires_the_achievements_route_marker(self):
        with self.assertRaisesRegex(AuditError, "achievements route"):
            parse_visible_achievements("<html><body></body></html>")

    def test_parser_rejects_an_incomplete_card(self):
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head>"
            "<details data-achievement-slug='yolo'></details></html>"
        )
        with self.assertRaisesRegex(AuditError, "card structure was incomplete"):
            parse_visible_achievements(html)

    def test_parser_rejects_truncated_html(self):
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head>"
            "<details data-achievement-slug='yolo'>"
            "<img alt='Achievement: YOLO'></details>"
        )
        with self.assertRaisesRegex(AuditError, "structurally incomplete"):
            parse_visible_achievements(html)

    def test_parser_rejects_control_characters_in_a_name(self):
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head>"
            "<details data-achievement-slug='future'>"
            "<img alt='Achievement: unsafe\x1bname'></details></html>"
        )
        with self.assertRaisesRegex(AuditError, "invalid achievement name"):
            parse_visible_achievements(html)

    def test_parser_rejects_cards_after_the_html_document(self):
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head><body></body></html>"
            "<details data-achievement-slug='yolo'>"
            "<img alt='Achievement: YOLO'></details>"
        )
        with self.assertRaisesRegex(AuditError, "structurally incomplete"):
            parse_visible_achievements(html)

    def test_parser_bounds_achievement_cards_before_endpoint_probes(self):
        cards = "".join(
            f"<details data-achievement-slug='future-{index}'>"
            f"<img alt='Achievement: Future {index}'></details>"
            for index in range(33)
        )
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head><body>"
            f"{cards}</body></html>"
        )
        with self.assertRaisesRegex(AuditError, "exceeded 32 achievement cards"):
            parse_visible_achievements(html)

    def test_parser_bounds_achievement_slug_length(self):
        slug = "a" * 101
        html = (
            "<html><head><meta name='route-controller' "
            "content='profiles_achievements'></head><body>"
            f"<details data-achievement-slug='{slug}'>"
            "<img alt='Achievement: Future'></details></body></html>"
        )
        with self.assertRaisesRegex(AuditError, "invalid achievement slug"):
            parse_visible_achievements(html)

    def test_profile_and_endpoint_mismatch_fails_closed(self):
        with self.assertRaisesRegex(AuditError, "endpoints disagreed"):
            report(web_mode="mismatch")

    def test_unknown_visible_achievement_is_retained_without_inventing_slug(self):
        result = report(web_mode="unknown")
        unknown = result["visible_achievements"][-1]
        self.assertEqual(unknown["name"], "Future Badge")
        self.assertEqual(unknown["slug"], "future-badge")
        self.assertIn("achievement=future-badge", unknown["url"])
        endpoint = result["achievement_endpoints"][-1]
        self.assertEqual(endpoint["slug"], "future-badge")
        self.assertEqual(endpoint["status"], 200)

    def test_profile_with_no_visible_achievements_is_not_called_unearned(self):
        result = report(web_mode="none")
        self.assertEqual(result["visible_achievements"], [])
        self.assertTrue(
            all(item["status"] == 404 for item in result["achievement_endpoints"])
        )
        self.assertIn("not rendered", " ".join(result["limitations"]))

    def test_tier_and_legacy_cards_use_the_same_structured_contract(self):
        result = report(web_mode="tier_legacy")
        self.assertEqual(
            [item["slug"] for item in result["visible_achievements"]],
            ["pull-shark", "arctic-code-vault-contributor"],
        )


class EvidenceTests(unittest.TestCase):
    def test_complete_report_separates_visible_state_and_events(self):
        result = report()
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["generated_at"], "2026-08-28T10:40:00Z")
        self.assertEqual(
            [item["name"] for item in result["visible_achievements"]],
            ["Quickdraw", "YOLO"],
        )
        pulls = result["public_evidence"]["merged_pull_requests"]
        self.assertEqual(
            pulls,
            {"public_total": 2, "outside_personal_namespace_total": 1},
        )
        self.assertNotIn("estimated_tier", json.dumps(result))

    def test_private_events_are_not_disclosed(self):
        result = report()
        serialized = json.dumps(result)
        self.assertNotIn("private-owner", serialized)
        answers = result["public_evidence"]["accepted_discussion_answers"]
        self.assertEqual(answers["public_total"], 2)
        self.assertEqual(answers["outside_personal_namespace_total"], 1)
        self.assertEqual(answers["self_accepted_total"], 1)
        self.assertEqual(len(answers["evidence"]), 2)

    def test_self_accepted_answers_are_separated_from_namespace(self):
        answers = report()["public_evidence"]["accepted_discussion_answers"]
        self.assertEqual(answers["self_accepted_total"], 1)
        by_repository = {item["repository"]: item for item in answers["evidence"]}
        own = by_repository["SirHegel/game"]
        foreign = by_repository["python-poetry/poetry"]
        self.assertTrue(own["self_accepted"])
        self.assertTrue(own["owned_by_subject"])
        self.assertFalse(foreign["self_accepted"])
        self.assertFalse(foreign["owned_by_subject"])

    def test_deleted_discussion_author_is_not_self_accepted(self):
        answers = report(mode="ghost_discussion_author")["public_evidence"][
            "accepted_discussion_answers"
        ]
        self.assertEqual(answers["self_accepted_total"], 0)
        self.assertFalse(any(item["self_accepted"] for item in answers["evidence"]))

    def test_owned_repositories_are_aggregated_and_sorted(self):
        repositories = report()["public_evidence"]["owned_public_nonfork_repositories"]
        self.assertEqual(repositories["total"], 2)
        self.assertEqual(repositories["total_stars"], 4)
        self.assertEqual(
            repositories["top_by_stars"]["name_with_owner"], "SirHegel/audit"
        )

    def test_program_and_advisory_signals_remain_separate(self):
        result = report()
        self.assertTrue(result["program_signals"]["developer_program_member"])
        self.assertEqual(
            result["public_evidence"]["advisory_database"]["merged_contributions"],
            1,
        )
        self.assertNotIn("security_advisory_credit", result["program_signals"])

    def test_no_response_bodies_enter_report(self):
        result = report()

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotEqual(key.casefold(), "body")
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)

    def test_second_request_disables_complete_connections(self):
        github = FakeGitHub()
        build_report("SirHegel", github=github, web=FakeWeb(), now=lambda: FIXED_TIME)
        self.assertEqual(len(github.calls), 2)
        self.assertTrue(github.calls[1]["includePulls"])
        self.assertFalse(github.calls[1]["includeAnswers"])
        self.assertFalse(github.calls[1]["includeRepositories"])
        self.assertEqual(github.calls[1]["pullCursor"], "pull-2")

    def test_three_connections_finish_on_independent_rounds(self):
        github = StaggeredGitHub()
        result = build_report(
            "SirHegel", github=github, web=FakeWeb(), now=lambda: FIXED_TIME
        )
        self.assertEqual(len(github.calls), 3)
        self.assertEqual(
            [
                (
                    call["includePulls"],
                    call["includeAnswers"],
                    call["includeRepositories"],
                )
                for call in github.calls
            ],
            [(True, True, True), (True, True, False), (False, True, False)],
        )
        evidence = result["public_evidence"]
        self.assertEqual(evidence["merged_pull_requests"]["public_total"], 101)
        self.assertEqual(evidence["accepted_discussion_answers"]["public_total"], 201)
        self.assertEqual(evidence["owned_public_nonfork_repositories"]["total"], 1)

    def test_query_is_read_only_and_requests_only_metadata(self):
        folded = AUDIT_QUERY.casefold()
        self.assertNotIn("mutation", folded)
        self.assertNotIn(" body", folded)
        self.assertIn("onlyanswers: true", folded)


class FailClosedTests(unittest.TestCase):
    def test_repeated_cursor_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "cursor repeated"):
            report(mode="cursor_repeat")

    def test_changing_total_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "total changed"):
            report(mode="changing_total")

    def test_non_answer_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "non-answer"):
            report(mode="non_answer")

    def test_duplicate_node_id_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "duplicated node IDs"):
            report(mode="duplicate_id")

    def test_unexpected_author_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "pull-request evidence was malformed"):
            report(mode="unexpected_author")

    def test_missing_user_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "user not found"):
            report(mode="missing_user")

    def test_graphql_errors_are_rejected(self):
        with self.assertRaisesRegex(AuditError, "reported an error"):
            report(mode="graphql_error")

    def test_untrusted_api_values_are_rejected(self):
        modes = {
            "bad_user_url": "user metadata",
            "malformed_user_url": "user metadata",
            "bad_answer_url": "accepted-answer evidence",
            "negative_upvotes": "accepted-answer evidence",
            "bad_answer_date": "accepted-answer evidence",
            "iso_week_answer_date": "accepted-answer evidence",
            "hour_24_answer_date": "accepted-answer evidence",
            "repository_mismatch": "accepted-answer evidence",
            "noncanonical_answer_url": "accepted-answer evidence",
            "duplicate_answer_url": "duplicated a public URL",
            "bad_discussion_author": "accepted-answer evidence",
            "negative_stars": "repository evidence",
            "duplicate_repository": "repository evidence was duplicated",
            "negative_rate": "rate-limit metadata",
            "program_drift": "metadata changed",
        }
        for mode, message in modes.items():
            with self.subTest(mode=mode), self.assertRaisesRegex(AuditError, message):
                report(mode=mode)


class TransportTests(unittest.TestCase):
    def test_gh_stderr_is_not_reflected(self):
        completed = subprocess.CompletedProcess(
            args=["gh"],
            returncode=1,
            stdout="",
            stderr="request failed with secret-token-value",
        )
        with (
            patch("achievement_audit.cli.subprocess.run", return_value=completed),
            self.assertRaises(AuditError) as raised,
        ):
            GitHubClient().authenticated_login()
        self.assertEqual(str(raised.exception), "GitHub API request failed")
        self.assertNotIn("secret-token-value", str(raised.exception))

    def test_profile_response_size_is_bounded(self):
        response = StubHTTPResponse(
            "https://github.com/SirHegel?tab=achievements",
            b"x" * (WebClient.MAX_PROFILE_BYTES + 1),
        )
        with (
            patch("achievement_audit.cli.urlopen", return_value=response),
            self.assertRaisesRegex(AuditError, "exceeded 2 MiB"),
        ):
            WebClient().profile_html("https://github.com/SirHegel?tab=achievements")

    def test_incomplete_profile_response_has_a_stable_failure(self):
        response = StubHTTPResponse(
            "https://github.com/SirHegel?tab=achievements", b"partial"
        )
        with (
            patch(
                "achievement_audit.cli.urlopen",
                return_value=response,
            ),
            patch.object(
                response,
                "read",
                side_effect=IncompleteRead(b"partial", 100),
            ),
            self.assertRaisesRegex(AuditError, "profile request failed"),
        ):
            WebClient().profile_html("https://github.com/SirHegel?tab=achievements")

    def test_malformed_final_url_has_a_stable_failure(self):
        response = StubHTTPResponse("https://[::1", b"<html></html>")
        with (
            patch("achievement_audit.cli.urlopen", return_value=response),
            self.assertRaisesRegex(AuditError, "profile request failed"),
        ):
            WebClient().profile_html("https://github.com/SirHegel?tab=achievements")

    def test_redirects_are_rejected(self):
        cases = (
            ("https://example.com/SirHegel", "off github.com"),
            ("https://github.com:8443/SirHegel", "off github.com"),
            ("https://github.com/login", "redirected unexpectedly"),
        )
        for final_url, message in cases:
            with self.subTest(final_url=final_url):
                response = StubHTTPResponse(final_url, b"<html></html>")
                with (
                    patch("achievement_audit.cli.urlopen", return_value=response),
                    self.assertRaisesRegex(AuditError, message),
                ):
                    WebClient().profile_html(
                        "https://github.com/SirHegel?tab=achievements"
                    )

    def test_off_host_404_is_rejected(self):
        error = HTTPError("https://example.com/missing", 404, "Not Found", {}, None)

        # A directly constructed HTTPError has no response file on Python 3.9.
        def off_host_url():
            return "https://example.com/missing"

        error.geturl = off_host_url
        with (
            patch("achievement_audit.cli.urlopen", side_effect=error),
            self.assertRaisesRegex(AuditError, "off github.com"),
        ):
            WebClient().status(
                "https://github.com/SirHegel?achievement=yolo&tab=achievements"
            )


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_path = (
            Path(__file__).resolve().parents[1] / "schema" / "report-v1.schema.json"
        )
        cls.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())

    def test_fixture_report_validates_against_schema(self):
        errors = list(self.validator.iter_errors(report()))
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_empty_evidence_report_validates_with_a_null_top_repository(self):
        empty_report = report(mode="empty")
        repositories = empty_report["public_evidence"][
            "owned_public_nonfork_repositories"
        ]
        self.assertEqual(repositories["total"], 0)
        self.assertIsNone(repositories["top_by_stars"])
        self.assertEqual(list(self.validator.iter_errors(empty_report)), [])

    def test_private_pagination_does_not_enter_source_health(self):
        private_report = report(mode="private_pagination")
        pulls = private_report["public_evidence"]["merged_pull_requests"]
        self.assertEqual(pulls["public_total"], 0)
        self.assertEqual(private_report["source_health"]["graphql"], {"complete": True})
        health = json.dumps(private_report["source_health"])
        self.assertNotIn("pages", health)
        self.assertNotIn("requests", health)

    def test_report_contract_has_no_unlock_predictions(self):
        forbidden = {
            "estimated_tier",
            "next_action",
            "remaining",
            "target",
            "unlock_probability",
        }

        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)

        self.assertTrue(forbidden.isdisjoint(set(keys(report()))))

    def test_schema_rejects_an_unexpected_body(self):
        invalid = deepcopy(report())
        invalid["public_evidence"]["accepted_discussion_answers"]["evidence"][0][
            "body"
        ] = "must never be stored"
        self.assertTrue(list(self.validator.iter_errors(invalid)))

    def test_rfc3339_hour_24_is_rejected_by_schema_and_semantics(self):
        invalid = deepcopy(report())
        invalid["generated_at"] = "2026-08-28T24:00:00Z"
        self.assertTrue(list(self.validator.iter_errors(invalid)))
        with self.assertRaisesRegex(AuditError, "invalid generated_at"):
            validate_report_semantics(invalid)

    def test_schema_pins_the_official_source_references(self):
        invalid = deepcopy(report())
        invalid["sources"]["official_profile_reference"] = "https://example.com"
        self.assertTrue(list(self.validator.iter_errors(invalid)))

    def test_semantic_validator_rejects_cross_field_contradictions(self):
        for name, invalid in semantic_mutations().items():
            with self.subTest(name=name):
                self.assertEqual(list(self.validator.iter_errors(invalid)), [])
                with self.assertRaisesRegex(AuditError, "semantic invariant failed"):
                    validate_report_semantics(invalid)

    def test_stdin_validator_rejects_semantic_contradictions(self):
        for name, invalid in semantic_mutations().items():
            with self.subTest(name=name):
                completed = subprocess.run(
                    [sys.executable, "tests/validate_stdin.py"],
                    input=json.dumps(invalid),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, "")
                self.assertIn("<semantic>", completed.stderr)

    def test_stdin_validator_rejects_invalid_utf8(self):
        completed = subprocess.run(
            [sys.executable, "tests/validate_stdin.py"],
            input=b"\xff",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"report is not valid UTF-8\n")

    def test_dependency_lock_policy_validator_passes(self):
        completed = subprocess.run(
            [sys.executable, "tests/validate_lock.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("dependency lock policy: ok", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_dependency_lock_policy_rejects_indented_preamble_bypasses(self):
        config = Path(".pip-tools.toml").read_text(encoding="utf-8")
        lock = Path("requirements-dev.txt").read_text(encoding="utf-8")
        insertions = (
            "  --no-binary :all:\n",
            "  unsafe @ https://example.com/unsafe.whl\n",
        )
        for insertion in insertions:
            mutated_lock = lock.replace(
                "--only-binary :all:\n",
                f"--only-binary :all:\n{insertion}",
                1,
            )
            with self.subTest(insertion=insertion):
                self.assertNotEqual(mutated_lock, lock)
                with self.assertRaisesRegex(
                    ValueError, "only permitted lock preamble entry"
                ):
                    validate_policy(config, mutated_lock)

    def test_dependency_lock_policy_rejects_an_option_hidden_in_a_marker(self):
        config = Path(".pip-tools.toml").read_text(encoding="utf-8")
        lock = Path("requirements-dev.txt").read_text(encoding="utf-8")
        mutated_lock = lock.replace(
            "arrow==1.4.0 \\\n",
            'arrow==1.4.0 ; python_version >= "3.10" --no-binary :all: \\\n',
            1,
        )
        self.assertNotEqual(mutated_lock, lock)
        with self.assertRaisesRegex(ValueError, "not exactly pinned"):
            validate_policy(config, mutated_lock)

    def test_text_renderer_labels_evidence_and_visibility(self):
        text = render_text(report())
        self.assertIn("Visible now: Quickdraw, YOLO", text)
        self.assertIn("Merged pull requests: 2 (1 outside personal namespace)", text)
        self.assertIn(
            "Accepted Discussion answers: 2 "
            "(1 outside personal namespace, 1 self-accepted)",
            text,
        )
        self.assertIn("no unpublished threshold is treated as fact", text)

    def test_main_json_writes_one_document(self):
        fixed_report = report()
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("achievement_audit.cli.build_report", return_value=fixed_report),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(["SirHegel", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), fixed_report)
        self.assertEqual(stderr.getvalue(), "")

    def test_main_failure_writes_only_stderr(self):
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch(
                "achievement_audit.cli.build_report",
                side_effect=AuditError("simulated"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(["SirHegel", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "gh achievement-audit: simulated\n")


if __name__ == "__main__":
    unittest.main()
