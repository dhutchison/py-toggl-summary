# Development Guide

All project changes are developed on feature branches and submitted as pull
requests. Do not make implementation commits directly on the default branch.

## Start a change

1. Read the relevant issue, documentation, and existing code. Resolve decisions
   that affect user-visible behavior before implementation.
2. Update the default branch and create a focused feature branch. Use the
   `codex/` prefix for agent-created branches and include an issue number or a
   short topic in the name, for example `codex/23-report-formats`.
3. Keep each branch focused on one change. Link related issues in the pull
   request description.

```sh
git switch main
git pull --ff-only
git switch -c codex/23-report-formats
```

Replace `main` with the repository's configured default branch if it differs.

## Implement and verify

Follow the project's documented commands and conventions. For this Python
project, the usual checks are:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Run focused checks while developing, then run the relevant full checks before
opening the pull request. Do not include credentials, raw account data, or
generated local configuration in commits.

Use Conventional Commit messages, as enforced by the repository's commit hook.

## Open a pull request

Push the feature branch and open a pull request against the default branch for
every change intended for the project. The pull request should include:

- A short summary of the user-visible change.
- The issue reference, using `Closes #N` when the pull request completes it.
- The checks run and their results.
- Any decisions, limitations, or follow-up work a reviewer needs to know.

Address review feedback on the feature branch and keep the pull request current
with the default branch using the repository's normal merge workflow. Do not
merge a pull request without the required review and checks.
