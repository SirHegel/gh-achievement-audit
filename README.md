# gh-achievement-audit

An evidence-first, read-only GitHub CLI extension that reports what a public profile
actually renders, then keeps supporting event counts separate from achievement claims.

GitHub documents Achievements as a public-preview profile feature and does not publish a
complete official criteria or tier table. This tool therefore never turns an unofficial
threshold into a claim that a badge was earned.

## What it audits

- Server-rendered visible achievements, cross-checked against known detail endpoints.
- Every visible merged pull request authored by the account, paginated by cursor and
  reduced to public totals.
- Every accepted GitHub Discussions answer visible to the caller, with public evidence URLs
  but no comment bodies.
- Owned public non-fork repositories, total stars, and the highest-starred repository.
- Public GraphQL program signals for Developer Program, Security Bug Bounty Hunter, Campus
  Expert, and GitHub Star.
- Merged contributions to `github/advisory-database` as event evidence, without claiming
  that profile indexing has completed.

It never creates comments, stars, reviews, follows, pull requests, co-authorship, or any
other activity.

“Outside personal namespace” has one deliberately narrow meaning: the repository owner's
login differs from the audited account's login. It does not claim that an organization is
independent from that account.

## Install

Requirements: Python 3.10 or newer and an authenticated
[GitHub CLI](https://cli.github.com/).

```bash
gh extension install SirHegel/gh-achievement-audit
```

## Use

Audit the authenticated account:

```bash
gh achievement-audit
```

Audit any public account:

```bash
gh achievement-audit octocat
```

Produce the versioned JSON report:

```bash
gh achievement-audit SirHegel --json
```

The JSON contract is published at
[`schema/report-v1.schema.json`](schema/report-v1.schema.json). A successful command emits a
complete report and exits `0`. Usage, authentication, API, pagination, HTML, endpoint, or
contract failures emit no partial report and exit `2`.

## Trust boundary

The extension makes authenticated, read-only GraphQL queries through `gh api` and performs
low-volume public GET requests to `github.com`. Independent cursors are fully paginated;
changing totals, repeated cursors, malformed nodes, GraphQL errors, DOM drift, and
profile/detail disagreement fail closed.

Some user connections can include private events visible to the authenticated caller and
do not offer a public-only filter. Minimal metadata is used only long enough to identify
and exclude those nodes. Private events and repository names are never placed in the
report. Discussion bodies are not requested at all.

A missing item in `visible_achievements` means only “not rendered publicly at audit time.”
GitHub allows users to hide Achievements, so absence is not evidence that an event never
happened.

## Sources

- [GitHub profile reference](https://docs.github.com/en/account-and-profile/reference/profile-reference)
- [GitHub Discussions GraphQL reference](https://docs.github.com/en/graphql/reference/discussions)
- [`gh api` pagination reference](https://cli.github.com/manual/gh_api)

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r requirements-dev.txt
.venv/bin/python tests/validate_lock.py
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -O -m unittest discover -s tests -v
```

The deterministic suite covers independent pagination, changing totals, repeated cursors,
private-event exclusion, untrusted metadata, redirected and oversized HTTP responses,
HTML drift, unknown future badges, endpoint disagreement, zero-result accounts, optimized
Python, and schema rejection of unexpected bodies and prediction fields.

`requirements-dev.txt` is a wheel-only, hash-locked resolution generated from
`requirements-dev.in`. `.pip-tools.toml` makes the wheel-only and hashing policy durable
when Dependabot or a maintainer recompiles the lock. CI independently checks that policy
and installs with both `--only-binary=:all:` and `--require-hashes`; update the input and
lock together when intentionally changing a dependency.

## License

MIT
