# Working in this repo (Claude)

Dummy money only. Merge only when the owner says "merge". Plan first, then ask before building.

## Code: smallest complete change (ponytail rules, owner's choice 25 Nov 2026)

- Read the task and the code it touches. List every place the change must reach: callers, tests, fixtures, config,
  infra, docs. That is the scope; extra features are not.
- Take the first option that fully works: not needed at all > already in this codebase > standard library or
  platform feature > an installed dependency > one clear line > the minimum code that works. Never add a
  dependency for a few lines.
- No abstraction, option, config or "for later" code nobody asked for. Deletion beats addition. Keep the existing
  structure and conventions.
- Finish everything the change breaks (callers, tests, fixtures, Terraform, docs).
- Bug fix: find every caller first, fix the root cause once in the shared code.
- New non-trivial logic (a branch, a loop, a parser, money, risk or security) gets a small test.
- A shortcut with a known limit gets a comment: `shortcut: <the limit>, <when to upgrade>`.
- Never cut: validation at trust boundaries, error handling that prevents data loss, security, the risk gate.
- End the PR description with one line on what was skipped or not checked.

## Writing

Commits, PRs, docs and code comments: plain, complete English for a non-specialist owner (no caveman style).
