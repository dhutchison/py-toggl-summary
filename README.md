# toggl-cli

`toggl-cli` produces a pipeable Markdown report of booked, unbooked, break, and
total time from Toggl Track. It uses the authenticated user's Toggl timezone and
week settings and is read-only with respect to Toggl.

## Development

This project uses `uv`:

```text
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

Install the local commit hook once with `uv run pre-commit install --hook-type commit-msg`.
Commit messages must use Conventional Commits; Commitizen can create one with
`uv run cz commit`.

## Configuration

The API token is stored in the operating system credential store under service
`toggl-cli`, account `api-token`. Workspace settings are stored in the platform
configuration directory in `config.toml`, for example:

```toml
[toggl]
workspace_id = 123456
```

For a first run, pass `--api-token TOKEN --workspace-id ID --save-config`. The
token is never written to TOML. A token can also be supplied for one invocation
with `--api-token`; environment variables are intentionally not used for secrets.

## Usage

```text
toggl-cli report --day 2026-08-08
toggl-cli report --day 2026-08-08 --week --include-summary
```

`--debug` sends redacted diagnostics to stderr. Report Markdown is written to
stdout, so it can be passed directly to another command.

## Live smoke verification

Tests are offline by default. To make one narrow, read-only Reports API v2
request against the configured account, supply an explicit date and opt in.

On macOS/Linux:

```text
env TOGGL_CLI_LIVE_SMOKE=1 TOGGL_CLI_LIVE_DAY=2026-08-08 uv run pytest -m live
```

On Windows PowerShell:

```text
$env:TOGGL_CLI_LIVE_SMOKE = "1"; $env:TOGGL_CLI_LIVE_DAY = "2026-08-08"; uv run pytest -m live
```

The smoke test reads the token from the OS credential store and workspace from
the local TOML settings. It does not write Toggl data, print the token, or
refresh committed fixtures. If a contract fixture needs refreshing, run this
smoke first and then follow the separate, manual in-memory sanitisation and
provenance steps documented in `tests/fixtures/README.md`; never redirect a
raw authenticated response to a file.
