# Contributing

Thank you for improving `gh-achievement-audit`.

1. Open or reference an issue that describes a user-facing evidence gap or failure mode.
2. Keep all GitHub access read-only. Never add mutations, automated comments, stars,
   reviews, follows, co-authorship, or achievement-farming behavior.
3. Request only the metadata needed for the public report. If a connection cannot exclude
   private nodes server-side, use the minimum metadata needed to discard them before
   aggregation. Never request discussion bodies or persist private repository names,
   tokens, or personal data.
4. Fail closed when pagination, profile parsing, endpoint agreement, or JSON validation is
   incomplete.
5. Add a deterministic regression for every new source, parser rule, and failure path.

Run the same checks as CI before opening a pull request:

```bash
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r requirements-dev.txt
.venv/bin/python tests/validate_lock.py
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -O -m unittest discover -s tests -v
```

## AI-assisted contributions

AI-assisted work is allowed, but the pull request author must disclose material use in the
pull request body, understand every changed line, verify every cited source, and run the
tests. Fabricated results, automated maintainer replies, and activity intended only to earn
profile credit are not acceptable.
