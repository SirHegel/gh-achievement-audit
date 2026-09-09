# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the CLI and a separate
`schema_version` for the JSON report contract.

## [0.2.0] - 2026-09-09

### Added

- `self_accepted` on every accepted-answer evidence item and `self_accepted_total` on the
  aggregate, so an answer to the audited account's own discussion is reported as a
  different event from an answer accepted by someone else (`schema_version` 1.1).
- `self_merged_total` on the merged pull-request counts, so a pull request the audited
  account merged itself is reported as a different event from one merged by another
  maintainer (`schema_version` 1.2).
- `docs/evidence-log.md`, a dated record of what the maintainer's profile rendered versus
  what the public event record contained at the same moment.
- `.editorconfig` so editors agree on indentation, line endings and trailing whitespace.

### Changed

- The JSON schema definition for merged pull-request counts is now named
  `pullRequestCounts` (previously `countPair`).
- The text renderer prints the self-accepted and self-merged counts next to the
  namespace counts.

### Unchanged on purpose

- The report still predicts nothing: no threshold, tier or eligibility statement was
  added, and the forbidden-key test for predictions still passes.
- The audit remains read-only and metadata-only.

## [0.1.0] - 2026-08-28

### Added

- Initial release: evidence-first, read-only audit of visible achievements, merged pull
  requests, accepted Discussion answers, owned repositories, program signals and
  Advisory Database contributions, with a hash-locked development environment and a
  fail-closed report validator.

[0.2.0]: https://github.com/SirHegel/gh-achievement-audit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SirHegel/gh-achievement-audit/releases/tag/v0.1.0
