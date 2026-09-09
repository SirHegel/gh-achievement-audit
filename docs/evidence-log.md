# Evidence log

This log records, with dates and public URLs, what the maintainer's profile rendered
versus what the public event record contained at the same moment. It exists so that the
tool's stance — visible state and event counts are reported separately, and no
unpublished threshold is treated as fact — stays anchored to observations rather than to
folklore.

Every entry is read-only evidence. Nothing here predicts an unlock.

## 2026-09-09 — 20 days after the first outside-namespace merges

### Visible state

| Source | Result |
| --- | --- |
| `https://github.com/SirHegel?tab=achievements` (server-rendered, logged out) | Quickdraw, YOLO |
| Same page, logged in as the subject | Quickdraw, YOLO (no hidden badges) |
| `https://github.com/users/SirHegel/achievements/pull-shark` | 404 |
| `https://github.com/users/SirHegel/achievements/galaxy-brain` | 404 |
| `https://github.com/users/SirHegel/achievements/pair-extraordinaire` | 404 |
| Settings → Profile → "Show Achievements on my profile" | enabled |

### Public events at the same moment

Merged pull requests authored by the subject (GraphQL `search`, `is:pr is:merged
author:SirHegel`): 46, of which 45 in public repositories.

- 3 outside the personal namespace, all merged to the default branch by a repository
  maintainer who is not the subject:
  - <https://github.com/tox-dev/tox/pull/4035> — merged 2026-08-20 by `gaborbernat`
  - <https://github.com/buildkite/test-collector-python/pull/132> — merged 2026-08-20 by `pda`
  - <https://github.com/buildkite/test-collector-javascript/pull/164> — merged 2026-08-24 by `pda`
- 42 inside the personal namespace, every one merged by the subject, between 2026-08-20
  and 2026-08-31.

Accepted Discussion answers authored by the subject (GraphQL
`repositoryDiscussionComments(onlyAnswers: true)`): 2, both public, both in the personal
namespace.

- <https://github.com/SirHegel/bloquitos/discussions/4> — question, answer and
  "mark as answer" all by the subject, 2026-08-20
- <https://github.com/SirHegel/bloquitos/discussions/5> — same shape, 2026-08-20

Merged pull requests containing a `Co-authored-by` trailer: 0.

### What the observation supports

1. The two accepted answers are self-accepted. Community threads have reported since
   January 2023 that an answer to one's own question does not count, and that self-marked
   answers can keep the badge from rendering. The observation is consistent with that and
   contradicts nothing.
2. Two outside-namespace merges on distinct days have existed for 16–20 days without a
   corresponding detail endpoint. Community threads from 2025–2026 report indexing delays
   of anywhere between 24 hours and 20–25 days, so this observation alone cannot separate
   "still indexing" from "not eligible".
3. Whether self-merged pull requests inside the personal namespace count is disputed in
   community threads from June 2026, with directly contradictory claims and no staff
   answer. The observation cannot settle it either: under both hypotheses the profile
   would look exactly as it does today.

### What the observation does not support

- It does not show that any badge "should" already be visible. GitHub publishes no
  eligibility table, and the profile reference only documents that Achievements exist
  and can be hidden.
- It does not show that 42 self-merged pull requests are worth nothing. It shows only that
  they have not produced a visible change yet.

### Follow-ups for the tool

The report currently separates events by *repository owner*. The evidence above suggests
two further separations that stay purely descriptive:

- accepted answers where the discussion author is the subject (`self_accepted`), and
- merged pull requests where the merger is the subject (`self_merged`).

Both are event facts, not thresholds, so they fit the existing contract.

## 2026-09-09 — co-author trailers on merged pull requests

Four pull requests were merged into this repository on the same day, each with a single
commit carrying a `Co-authored-by` trailer that names a bot account by its public
`users.noreply.github.com` address. The purpose was to observe, without inventing any
human participant, whether GitHub resolves such trailers to an account and whether a
merged pull request with a resolved bot co-author is later reflected on the profile.

| Pull request | Co-author trailer | Resolved by GraphQL `Commit.authors` |
| --- | --- | --- |
| <https://github.com/SirHegel/gh-achievement-audit/pull/6> | `claude[bot] <209825114+claude[bot]@users.noreply.github.com>` | `claude[bot]` |
| <https://github.com/SirHegel/gh-achievement-audit/pull/7> | `Copilot <198982749+Copilot@users.noreply.github.com>` | `Copilot` |
| <https://github.com/SirHegel/gh-achievement-audit/pull/8> | `github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>` | `github-actions[bot]` |
| <https://github.com/SirHegel/gh-achievement-audit/pull/9> | `claude[bot] <209825114+claude[bot]@users.noreply.github.com>` | `claude[bot]` |

Observations that hold regardless of what the profile does later:

- All three bot identities resolve. `Commit.authors` lists them as co-authors with a
  non-null `user`, on the pull-request commits and on the squash commits GitHub created on
  `main`, so the trailers survived the squash merge.
- A second trailer present on every commit, `Claude Opus 5 (1M context)
  <noreply@anthropic.com>`, is added by the CLI agent as attribution. GitHub resolves that
  address to the account `claude` (user id 81847, created 2009). Whether that account is
  operated by Anthropic is not something this repository can determine; it is recorded
  because it means the attribution trailer is also a resolved co-author from GitHub's
  point of view.
- Before this entry the account had no merged pull request with a co-author trailer.
  Merged pull requests with at least one resolved co-author now number 4, all inside the
  personal namespace, all merged by the subject (5 once the pull request adding this
  entry is merged).

What is still unknown and must not be inferred:

- Whether a resolved *bot* co-author is treated the same as a resolved human co-author.
- Whether pull requests merged by the subject into the subject's own repository are
  treated the same as pull requests merged by someone else.

Both questions are answered only by the profile, and only after GitHub's indexing delay.
The profile at the time of writing still rendered Quickdraw and YOLO and nothing else.

## Sources consulted for these entries

Official:

- GitHub profile reference — <https://docs.github.com/en/account-and-profile/reference/profile-reference>
- GitHub Discussions GraphQL reference — <https://docs.github.com/en/graphql/reference/discussions>

Community (not authoritative; quoted only as the source of a claim):

- "Cannot get Galaxy Brain achievement" — <https://github.com/orgs/community/discussions/45578>
  (self-marked answers; GitHub staff note of 2024-02-14 that the Community forum no longer
  awards Achievements)
- "Pull Shark Achievement Missing After Multiple Merged PRs" —
  <https://github.com/orgs/community/discussions/169228> (delays of 20–25 days reported)
- "How can i get the blue shark achievement … in the new update" —
  <https://github.com/orgs/community/discussions/198068> (contradictory claims about
  self-merged pull requests, June 2026)
- "My achievements are not updating! (Pair Extraordinaire)" —
  <https://github.com/orgs/community/discussions/40386> (credit reported to go to the
  author who adds the trailer; support can re-index on request)
