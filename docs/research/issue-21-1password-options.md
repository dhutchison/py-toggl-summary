# Issue 21: 1Password API-token source options

Research date: 2026-10-05. Research only: no implementation, dependency changes,
1Password commands, authentication, or credential reads were performed.

[Issue 21](https://github.com/dhutchison/py-toggl-summary/issues/21) asks for an
optional 1Password token source, preserving keyring as the default, explicit
source precedence, redacted failures, and offline tests. The issue mentions TOML;
the current code defaults to JSON and retains legacy TOML reading.

## Options and recommendation

For a native implementation, the best initial fit is a small read-only adapter
around `op read`, using the desktop app for local authorization and optionally
accepting an already signed-in manual CLI session. This is a design
recommendation, not an implemented choice.
The official Python SDK is a credible alternative when avoiding a separate `op`
installation matters more than packaging and process-specific authorization.

### Project decision and follow-up

On 2026-10-05, the project owner first chose to keep this single-user project
simple by documenting `op run` with `--api-token` only. After discussing the
shell wrapper, the owner selected environment-variable support as the cleaner
`op run` interface. The CLI now resolves credentials in this order:
`--api-token`, `TOGGL_API_TOKEN`, then keyring. Both `report` and `review` use the
same resolver; a higher-priority non-empty source skips lower-priority reads.
The [README](../../README.md) documents `op run`, precedence, and the exposure
tradeoff.

This keeps the 1Password token out of CLI arguments: `op run` resolves the
reference into `TOGGL_API_TOKEN` for the CLI process. The value is available in
the process environment and may be visible to other processes running as the
same user. Omitting `--save-config` leaves it out of keyring. No `op read`
adapter, 1Password dependency, account/reference configuration, or dedicated
1Password authentication behavior was added.
[Run command reference](https://www.1password.dev/cli/reference/commands/run).

The keyring workflow remains the default when neither `--api-token` nor the
environment variable is set. The environment is a runtime source only; it is
not persisted by configuration saving unless the user explicitly supplies
`--save-config`, in which case the current behavior saves the selected token to
keyring.

| Option | Local interactive fit | Principal cost | Recommendation |
| --- | --- | --- | --- |
| `op read` + desktop app | Strong; established terminal authorization | Separate CLI install; carefully capture subprocess output | Preferred if native integration is pursued |
| Official Python SDK + desktop app | Strong; direct supported app integration | Optional native dependency, async bridge, each process authorized | Genuine alternative |
| `op read` + existing manual CLI session | Useful without desktop integration | Session environment, sign-in lifecycle, broader local security risks | Document as secondary authentication route |
| Service account via CLI or SDK | Strong for unattended execution | Another secret to provision, restricted vaults and quotas | Future explicit automation mode |
| Connect via CLI/REST/Connect SDK | Fits existing shared infrastructure | Operate servers and provision credentials/access token | Excessive for a single local token |
| `op run` + `TOGGL_API_TOKEN` | Works with CLI environment support | Resolved token in process environment | Selected workflow |

## 1. CLI with desktop authorization

The supported operation reads one field to stdout:

```text
op read --no-newline --account <account-id> op://<vault-id>/<item-id>/<field-id>
```

`--no-newline` avoids an added trailing newline; `--out-file` exists but should
not be used here. `--force` suppresses confirmation; it does not establish a
headless authentication session or waive authorization.
[Read command reference](https://www.1password.dev/cli/reference/commands/read).

References have the form `op://vault/item/[section/]field`, using names or IDs.
References are case-insensitive, support spaces and selected punctuation, and
require IDs for names containing unsupported characters. IDs avoid rename and
duplicate-name surprises; prefer a reference copied from the app. Account
selection is separate from the reference.
[Secret reference syntax](https://www.1password.dev/cli/secret-reference-syntax).

Users install `op`, enable **Integrate with 1Password CLI** in the desktop app,
and sign in to the desired account there. Commands can trigger desktop
authorization without a preceding `op signin`. On macOS, Windows, and Linux the
supported unlock routes include Touch ID, Windows Hello, and system
authentication respectively. The app must remain running; official
troubleshooting covers lost connections and missing accounts.
[App integration](https://www.1password.dev/cli/app-integration).

The official installer guide supports Mac, Windows and Linux. Desktop Linux
authentication requires PolKit and an active authentication agent. Linux manual
installation requires the documented group/setgid setup. Windows desktop
authentication requires Windows Hello. CLI binaries are a separately installed
user prerequisite, not a mandatory Python dependency.
[Installation guide](https://www.1password.dev/cli/get-started).

CLI desktop authorization lasts ten minutes of inactivity, refreshing on use,
with a twelve-hour hard limit. Authorization is per account and terminal
session; on macOS/Linux it extends to subshells, whereas Windows subshells need
separate approval. Locking the account revokes prior authorization. Thus a
reference directs a field read, but does not constrain the authority of other
processes in the authorized terminal to that one field. This is a different
security boundary from granting Python access to a macOS Keychain item.
[CLI integration security](https://www.1password.dev/cli/app-integration-security).

The `--account` flag accepts an account shorthand, sign-in address, account ID,
or user ID. Pinning an account ID avoids accidental use of the last-selected
account. CLI caching is enabled by default on UNIX-like systems and unavailable
on Windows. Do not introduce a second persistent token cache in this app; if
CLI cache disabling is desired, its freshness/performance effects need separate
verification rather than assumed benefits.
[Global flags](https://www.1password.dev/cli/reference).

Ambient variables matter: `OP_ACCOUNT`, `OP_BIOMETRIC_UNLOCK_ENABLED`,
`OP_CONFIG_DIR`, `OP_DEBUG`, `OP_SERVICE_ACCOUNT_TOKEN`, and `OP_CONNECT_*` alter
CLI behavior. An explicit account flag overrides `OP_ACCOUNT`, but it does not
by itself prevent alternate authentication modes. For a personal-account mode,
reject unexpected service-account/Connect variables with a fixed message;
disable inherited debug output. Preserve the normal OS environment and intended
session variables rather than deleting every `OP_*` value indiscriminately.
[CLI environment variables](https://www.1password.dev/cli/environment-variables).

### Subprocess and failure contract

Proposed behavior, to verify later with offline fakes and an explicitly authorized
manual desktop check:

- Invoke the installed native executable with an argument list and `shell=False`.
  Put only the reference and account identifier in argv, never the resolved token.
- Capture stdout and stderr separately; do not inherit stderr or merge it into
  token output. Keep the token in memory and pass it directly to `TogglApi`.
- Disable stdin to avoid password/account-selection interaction inside report or
  review. Let `op` own desktop prompts; do not invoke `signin` automatically.
- Bound execution time generously enough for human authorization. On failure,
  discard partial output and stop before constructing a Toggl client.
- Emit distinct fixed messages for missing executable, launch failure, timeout,
  malformed/empty output, and unsuccessful CLI execution. Never echo raw
  stdout/stderr or the reference. Treat zero exit status plus a valid non-empty
  token as success; never accept partial output after nonzero exit.

Python documents that subprocess error/timeout objects can retain captured
output, and that `run(timeout=...)` terminates and waits for the child before
raising. Therefore do not stringify exceptions, dump process results, or chain
raw errors into displayed diagnostics. Choose an explicit token-output policy;
`--no-newline` avoids needing a broad `.strip()` that silently changes secrets.
[Python subprocess documentation](https://docs.python.org/3/library/subprocess.html).

The examined `op read` reference does not publish stable semantic exit codes
distinguishing denial, expired session, missing item, or missing field. Avoid
promising such classification or treating human stderr text as a stable API.
A generic redacted failure can direct users to check authorization, selected
account, and reference, and to sign in outside the app when using manual CLI
authentication. Missing item/field must still fail closed. Desktop prompts with
captured stdout/stderr and disabled stdin, cancellation, timeout, and Windows
child-process authorization remain unverified in this research.

## 2. Official Python SDK with desktop authorization

The official SDK supports local desktop authentication today; it is not limited
to service accounts. Desktop authorization shipped in SDK 0.4.0 on 2026-02-10.
[Combined SDK release notes](https://releases.1password.com/developers/sdks/).
The Python-specific release page currently lists 0.4.1, released 2026-07-29.
[Python SDK releases](https://releases.1password.com/developers/sdk-python/).

The documented API is `await Client.authenticate(auth=DesktopAuth(account_name=...),
integration_name=..., integration_version=...)`, followed by
`await client.secrets.resolve(reference)`. Users enable SDK integration in the
desktop app; a separate `op` executable is not required for ordinary resolution.
The SDK README requires Python 3.9+, and lists libssl 3/glibc 2.32+ for its native
Linux runtime. Its README describes app setup for Mac, Windows, and Linux.
[Python SDK README](https://github.com/1Password/onepassword-sdk-python/blob/main/README.md).

The SDK docs also permit the account UUID returned by `op account list` in place
of the account display name. Prefer this stable identity when practical. SDK
version 0 may introduce breaking changes between feature versions, with three
months of support/security patches; use an optional dependency, lazy import, and
an explicit upgrade policy. The public Python interface is asynchronous, so the
existing synchronous CLI needs a contained bridge.
[SDK overview](https://www.1password.dev/sdks).

Desktop SDK authorization is per account and process, expires after ten minutes
of inactivity, and is revoked when the account locks. Separate CLI invocations
are separate Python processes, so do not promise CLI-style terminal approval
reuse. This isolates approval to the process, but adds prompt friction across
repeated commands. Authorization covers the entire account, not just the chosen
reference.
[Local integration security](https://www.1password.dev/sdks/desktop-app-integrations),
[SDK authentication concepts](https://www.1password.dev/sdks/concepts).

The current package includes platform-native libraries and depends on
`pydantic>=2.5`; this repo already uses Pydantic 2, so Pydantic itself is not a new
dependency family. Source packaging selects architecture-specific libraries,
and published wheel compatibility still needs checking for the project's exact
supported OS/architecture matrix.
[SDK package definition](https://github.com/1Password/onepassword-sdk-python/blob/main/setup.py),
[Project dependencies](../../pyproject.toml).

Desktop initialization locates and loads a library from known desktop-app
installation paths. It can raise file-not-found/unsupported-OS errors and runtime
errors for dropped/closed IPC channels. Source inspection also shows synchronous
native calls inside async methods: an `asyncio` timeout alone must not be assumed
to interrupt a stalled native authorization call. Cancellation and timeout
behavior require verification for the chosen version.
[Desktop SDK implementation](https://github.com/1Password/onepassword-sdk-python/blob/main/src/onepassword/desktop_core.py).

The SDK has typed `DesktopSessionExpiredException` and
`RateLimitExceededException`, but other errors may remain generic. This offers
better classification for those two cases, not a complete guarantee of typed
missing-field or authorization-denied errors. SDK exceptions still need fixed,
redacted messages.
[SDK errors](https://github.com/1Password/onepassword-sdk-python/blob/main/src/onepassword/errors.py).

## 3. Existing manual CLI session

Manual CLI setup prompts for sign-in address, email, Secret Key, and account
password outside this app. A manual session uses an encrypted session key on
disk and a wrapper key in the shell environment; it expires after thirty minutes
of inactivity. 1Password explicitly prefers desktop integration for stronger
security guarantees. Manual signin is not suitable for launching fresh remote
non-interactive sessions.
[Manual sign-in documentation](https://www.1password.dev/cli/sign-in-manually).

A resolver could consume an existing authenticated session using the same
`op read` boundary. It should never manage the master password, call `eval`,
automatically capture a new session token, or silently switch from desktop to
manual authentication. An expired/missing session produces a fixed instruction
to sign in in the user's terminal and rerun. Whether the first release accepts
existing manual sessions or deliberately requires desktop integration is a
scope decision to document explicitly.

## 4. Service accounts

Service accounts authenticate unattended with a separately provisioned secret;
they are not tied to a person and can have scoped vault permissions. No server
deployment is needed.
[Service-account overview](https://www.1password.dev/service-accounts).
They require adequate creation permissions, cannot access built-in
Personal/Private/Employee or default Shared vaults, have request limits, and have
immutable access permissions requiring replacement to change scope. CLI support
requires version 2.18.0+. A dedicated non-built-in vault and read-only access are
appropriate for an automation variant.
[Service-account setup](https://www.1password.dev/service-accounts/get-started).

CLI authentication uses `OP_SERVICE_ACCOUNT_TOKEN`. Ambient Connect host/token
variables take precedence. `op read` is supported and may make three requests
with names, reduced to one by supplying vault/item IDs. This reinforces the need
to make authentication mode explicit and use IDs when designing headless
support.
[Service accounts with CLI](https://www.1password.dev/service-accounts/use-with-1password-cli).

Both CLI and official Python SDK can use service accounts. This is a good later
automation option, but for the current personal local workflow it introduces a
second secret whose provisioning must itself be solved. It also cannot read a
token kept only in the user's built-in personal vault.

## 5. Connect and other alternatives

Connect provides a self-hosted private REST API and caches vault data in the
deployment; clients can use `op`, HTTP, or the dedicated Connect Python SDK.
[Connect overview](https://www.1password.dev/connect).
Deployment requires API/sync services, a credentials file, and application access
tokens; Docker and Kubernetes setup are documented. Connect excludes the same
built-in/default vaults listed in its prerequisites.
[Connect setup](https://www.1password.dev/connect/get-started).
Existing organizational Connect deployments could support the feature, but
introducing that infrastructure for one local Toggl token has little advantage.

`op run` resolves references into a child process environment. 1Password warns
that other processes under the same user may access that environment. The CLI
reads `TOGGL_API_TOKEN` directly after `--api-token` and before keyring, so a
child shell and token argument are unnecessary. Output masking is not a
replacement for application redaction. Shell substitution into `--api-token`
would expose the resolved value in argv, even though the shell history contains
only the substitution expression.
[Environment injection documentation](https://www.1password.dev/cli/secrets-environment-variables).

The official desktop SDK is the supported direct desktop alternative. A bespoke
IPC implementation, credential-database scraping, or clipboard/UI automation
would add unnecessary security and compatibility work. The CLI's IPC security
design and SDK's platform-native IPC demonstrate why bypassing those supported
interfaces is a poor fit. This is an engineering inference from the cited CLI
and SDK security models, not a claim that every conceivable desktop interface
has been exhaustively ruled out.

## Proposed configuration and repository integration

These are design options, not committed implementation decisions.

```json
{
  "credentials": {
    "source": "1password",
    "reference": "op://<vault-id>/<item-id>/<field-id>",
    "account": "<account-id>"
  }
}
```

The same fields can be represented in a legacy TOML `[credentials]` table.
Absent credentials configuration means keyring. The reference and account are
metadata, not token values, but may still reveal names; IDs and redacted errors
reduce that exposure. Do not accept arbitrary shell commands as a source.

Suggested precedence:

1. Explicit one-use `--api-token` override, preserving compatibility.
2. The explicitly configured source, with no fallback after failure.
3. Keyring only when no other source is configured.

In [config.py](../../src/toggl_cli/config.py), `CredentialStore` combines `get`
and `set`, while `save_settings` always writes a token to keyring. A read-only
credential-source abstraction and separate writable keyring store avoid inventing
a 1Password setter or copying tokens out of 1Password. `Settings` should carry
validated source metadata, while JSON/TOML loading stays compatible.

The current `_without_secrets` sanitizer removes keys containing `token`,
`password`, `authorization`, or `secret`. Names such as `api_token_source` and
`secret_reference` would vanish during saves; neutral keys above avoid that.
Values must still be validated and secrets must still never enter saved config.

Both report and review select credentials separately in
[cli.py](../../src/toggl_cli/cli.py). They should share a single resolver so
source precedence and redaction remain identical. A one-use override should not
perform a keyring read or launch `op`.

The existing [API error boundary](../../src/toggl_cli/api.py) forwards up to 240
characters of a server-provided `error` or `message` into a displayed `ApiError`.
This research found no actual secret exposure, but a reflected token could pass
through that path. Redaction verification should include a fake HTTP error that
echoes the token, as well as credential-provider failures; redacting only `op`
output would not establish the issue's end-to-end diagnostic guarantee.

For `--save-config` in 1Password mode there are two reasonable designs: reject
it with an explanation, or save only non-secret source/workspace settings.
The latter better preserves useful configuration behavior. Never use the resolved
1Password token as the keyring save payload. An explicit override combined with
save needs defined semantics: either keep the configured source and save only
settings, or require a separate deliberate switch to keyring rather than
silently persisting the override or changing the source.

Keeping keyring as the default preserves its existing macOS Keychain approval
flow. Opting into 1Password changes which system authorizes token access; it does
not disable either system's security prompts. No Keychain operation should be
needed merely to resolve or save non-secret settings in 1Password mode.

## Verification to require if implementation is authorized later

All automated verification should use fakes/stubs. Cover source precedence and
no fallback; skipped keyring/subprocess operations with overrides; argument-list
invocation; redaction of stdout, stderr, exception objects and timeout partial
output; missing executable, failure, empty/malformed output and cancellation;
account pinning and unexpected auth variables; JSON/TOML metadata persistence;
and saving settings without copying a 1Password token into keyring. No test
should require a real account or network.

A separately authorized manual validation could check the actual desktop prompt
with captured streams and disabled stdin, denial/expiry behavior, chosen
timeout, token rotation, and platform-specific subprocess authentication.
No such validation was attempted during this research.
