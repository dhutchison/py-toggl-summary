# toggl-cli

`toggl-cli` produces a pipeable Markdown report of booked, unbooked, break, and
total time from Toggl Track. It also provides an interactive, confirmation-gated
entry-quality review. Reports use the authenticated user's Toggl timezone and
week settings; normal reporting remains read-only with respect to Toggl.

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

All changes are developed on feature branches and submitted as pull requests;
see the [development guide](DEVELOPMENT.md) for the workflow.

## Configuration

The API token defaults to the operating system credential store under service
`toggl-cli`, account `api-token`. For one invocation, credentials can instead
come from `--api-token` or `TOGGL_API_TOKEN`, in that order before keyring.
Workspace settings are stored in the platform configuration directory in
`config.json`, for example:

```json
{
  "toggl": {
    "workspace_id": 123456
  }
}
```

For a first run, pass `--api-token TOKEN --workspace-id ID --save-config`. The
token is never written to the configuration file. `--api-token` is intended for
one invocation; environment variables are useful for integrations such as
1Password CLI's `op run`.
Existing `config.toml` files are still read; saving settings writes the new
`config.json` format and leaves the old file untouched.

### Using 1Password with `op run`

You can use 1Password CLI's `op run` without adding a token to the OS credential
store. [Install and configure 1Password CLI](https://www.1password.dev/cli/get-started),
then copy the secret reference for your Toggl API-token field from 1Password.

On macOS/Linux with Bash, Zsh, or sh:

```sh
TOGGL_API_TOKEN='op://Private/Toggl/api token' \
  op run --account ACCOUNT_ID -- toggl-cli report --workspace-id 123456
```

Replace the reference, `ACCOUNT_ID`, and workspace ID with your values. If running
from a checkout, use `uv run toggl-cli` in place of `toggl-cli`. `op run` resolves
the reference into `TOGGL_API_TOKEN` for the CLI. An explicit `--api-token`
overrides the environment variable, which overrides keyring. Omit `--save-config`
to avoid copying the resolved token into keyring.
See the [1Password `op run` documentation](https://www.1password.dev/cli/reference/commands/run).

In PowerShell:

```powershell
$Env:TOGGL_API_TOKEN = 'op://Private/Toggl/api token'
op run --account ACCOUNT_ID -- toggl-cli report --workspace-id 123456
Remove-Item Env:TOGGL_API_TOKEN
```

The token value stays out of the command arguments and shell history: only the
1Password reference appears there. `op run` passes the resolved token through
the child environment for the duration of the command. Other processes running
as your user may be able to inspect process environments, so avoid using this
for untrusted local processes. 1Password authorization prompts are separate
from macOS Keychain access prompts.

The [issue 21 research](docs/research/issue-21-1password-options.md) records the
integration alternatives and the decision to use an environment variable with
`op run` for this single-user project.

## Usage

```text
toggl-cli report --day 2026-08-08
toggl-cli report --day 2026-08-08 --week --include-summary
toggl-cli report --day 2026-08-08 --format pretty
toggl-cli report --day 2026-08-08 --format json --include-summary
```

`--debug` sends redacted diagnostics to stderr. Report Markdown is written to
stdout by default, so it can be passed directly to another command. Select
`--format markdown`, `--format pretty`, or `--format json` to choose another
output format. Pretty output uses terminal tables and requires an interactive
terminal; it exits with an error if stdout or stderr is redirected or the
command is running in CI. JSON writes a structured report to stdout. Each
duration includes both `milliseconds` and `human_readable` fields; when
`--include-summary` is enabled, JSON and pretty output include both the
client/project and activity-type summaries.

When `--include-summary` is enabled, the report also includes an activity-type
breakdown. Configure the taxonomy and optional Jira checks in the same JSON file:

```json
{
  "review": {
    "activity_types": ["reviewing", "supporting", "doing", "meeting"],
    "jira_activity_types": ["reviewing", "supporting", "doing"]
  }
}
```

The `review` command is always interactive and requires a TTY. It previews
proposed activity-type, project, and Jira-reference corrections and writes
only after explicit confirmation:

```text
toggl-cli review --day 2026-08-08
```

Review writes use one Toggl PUT request per changed entry and can partially
succeed; the command reports per-entry failures and never claims rollback.
Tag changes send the complete desired tag array and do not use Toggl's
unreliable tag-delete action. The separately authorized disposable-entry
qualification probe described in the [live-write qualification runbook](docs/live-write-qualification.md)
has passed, so the interactive review command enables the reviewed write path.
Writes still require explicit confirmation and can partially succeed. The
detailed [entry review contract](docs/entry-review-contract.md) describes the
rules the command follows, and [ADR 0001](docs/adr/0001-per-entry-put-for-review-writes.md)
records why review uses per-entry PUT requests.

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
