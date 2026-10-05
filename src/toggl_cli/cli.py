"""Typer command-line adapter."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, tzinfo
from enum import StrEnum
from typing import Annotated

import typer
from rich.console import Console

from .api import ApiError, TogglApi
from .app import ReportService
from .config import (
    ConfigError,
    KeyringCredentialStore,
    default_config_path,
    load_settings,
    load_token,
    save_settings,
)
from .render import render_pretty_report, render_report, render_report_json
from .review import (
    CONFLICTING_ACTIVITY_TYPE,
    MISSING_ACTIVITY_TYPE,
    MISSING_JIRA_REFERENCE,
    MISSING_PROJECT,
    Project,
    ReviewCandidate,
    add_jira_reference,
    changed_entries,
    matching_prefix,
    propose,
    replace_activity_type,
    replace_project,
)


def _rich_terminal_output_enabled() -> bool:
    return (
        sys.stdout.isatty()
        and sys.stderr.isatty()
        and not os.environ.get("CI")
        and not os.environ.get("GITHUB_ACTIONS")
    )


def _api_token(
    explicit_token: str | None,
    credentials: KeyringCredentialStore,
) -> str | None:
    """Resolve credentials in one-use, environment, then keyring order."""
    return explicit_token or os.environ.get("TOGGL_API_TOKEN") or load_token(credentials)


_rich_terminal_output = _rich_terminal_output_enabled()
app = typer.Typer(
    help="Toggl reports and interactive entry review.",
    context_settings={"color": _rich_terminal_output and not os.environ.get("NO_COLOR")},
    rich_markup_mode="rich" if _rich_terminal_output else None,
)


class ReviewCancelled(Exception):
    pass


class ReportFormat(StrEnum):
    MARKDOWN = "markdown"
    PRETTY = "pretty"
    JSON = "json"


def _format_countdown(total_seconds: int) -> str:
    remaining = max(0, total_seconds)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes, seconds = divmod(remaining, 60)
    parts = [
        f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (seconds, "s")) if value
    ]
    return " ".join(parts) or "0s"


def _prompt(console: Console, prompt: str) -> str:
    try:
        value = input(prompt)
    except (EOFError, KeyboardInterrupt) as error:
        raise ReviewCancelled from error
    if value.casefold() == "c":
        raise ReviewCancelled
    return value.strip()


def _entry_prompt_label(candidate: ReviewCandidate) -> str:
    description = candidate.proposed.description or "(no description)"
    return f"Entry {candidate.original.id} — {description}"


def _project_label(project_id: int | None, projects: tuple[Project, ...]) -> str:
    if project_id is None:
        return "(none)"
    project = next((project for project in projects if project.id == project_id), None)
    return f"{project.name} ({project.id})" if project else f"(unknown project {project_id})"


def _choose_activity(
    console: Console, candidate: ReviewCandidate, activity_types: tuple[str, ...]
) -> str | None:
    while True:
        value = _prompt(console, f"{_entry_prompt_label(candidate)} activity type (s to skip): ")
        if value.casefold() == "s":
            return None
        matches = matching_prefix(value, activity_types)
        if len(matches) == 1:
            return matches[0]
        if matches:
            console.print("Ambiguous choice: " + ", ".join(matches))
        else:
            console.print("Choose an activity type by name or unique prefix.")


def _choose_project(
    console: Console, candidate: ReviewCandidate, projects: tuple[Project, ...]
) -> Project | None:
    if not projects:
        console.print(f"{_entry_prompt_label(candidate)}: no active projects are available.")
        return None
    choices = tuple(project.name for project in projects)
    while True:
        value = _prompt(console, f"{_entry_prompt_label(candidate)} project (s to skip): ")
        if value.casefold() == "s":
            return None
        matches = matching_prefix(value, choices)
        if len(matches) == 1:
            return next(project for project in projects if project.name == matches[0])
        if matches:
            console.print("Ambiguous choice: " + ", ".join(matches))
        else:
            console.print("Choose an active project by name or unique prefix.")


def _review_candidate(
    console: Console,
    candidate: ReviewCandidate,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...],
    projects: tuple[Project, ...],
    display_timezone: tzinfo | None = None,
) -> ReviewCandidate:
    current = candidate
    start = (
        current.original.start.astimezone(display_timezone)
        if display_timezone
        else current.original.start
    )
    stop = (
        current.original.stop.astimezone(display_timezone)
        if display_timezone and current.original.stop
        else current.original.stop
    )
    state = "running" if stop is None else stop.isoformat()
    console.print(f"{_entry_prompt_label(current)}: {start.isoformat()} - {state}")
    for issue in (
        MISSING_ACTIVITY_TYPE,
        CONFLICTING_ACTIVITY_TYPE,
        MISSING_PROJECT,
        MISSING_JIRA_REFERENCE,
    ):
        if issue not in current.issues:
            continue
        if issue in {MISSING_ACTIVITY_TYPE, CONFLICTING_ACTIVITY_TYPE}:
            activity = _choose_activity(console, current, activity_types)
            if activity is not None:
                current = propose(
                    current,
                    replace_activity_type(current.proposed, activity, activity_types),
                    activity_types,
                    jira_activity_types,
                )
        elif issue == MISSING_PROJECT:
            project = _choose_project(console, current, projects)
            if project is not None:
                current = propose(
                    current,
                    replace_project(current.proposed, project),
                    activity_types,
                    jira_activity_types,
                )
        else:
            while True:
                value = _prompt(
                    console,
                    f"{_entry_prompt_label(current)} Jira key (s to skip): ",
                )
                if value.casefold() == "s":
                    break
                try:
                    proposed = add_jira_reference(current.proposed, value)
                except ValueError as error:
                    console.print(str(error))
                    continue
                current = propose(
                    current,
                    proposed,
                    activity_types,
                    jira_activity_types,
                )
                break
    return current


def _render_review_summary(
    console: Console,
    candidates: tuple[ReviewCandidate, ...],
    total_owned: int | None = None,
    projects: tuple[Project, ...] = (),
    writes_qualified: bool = False,
) -> None:
    changed = sum(candidate.changed for candidate in candidates)
    unresolved = sum(not candidate.valid for candidate in candidates)
    valid = (
        (total_owned - unresolved)
        if total_owned is not None
        else sum(candidate.valid for candidate in candidates)
    )
    console.print(f"Review summary: valid={valid}, changed={changed}, unresolved={unresolved}")
    writes = changed_entries(candidates)
    operation_count = sum(len(changes) for _, changes in writes)
    console.print(
        f"Write plan: {len(writes)} entries, "
        f"{operation_count} field update(s), {len(writes)} per-entry PUT request(s), "
        "at least one second between requests; writes are partial and have no rollback."
    )
    if any("tags" in changes for _, changes in writes):
        console.print(
            "Warning: tag updates replace the complete tag array; tags added after this "
            "review snapshot may be overwritten."
        )
    if writes_qualified:
        console.print("Write readiness: qualified; writes remain gated until confirmation.")
    else:
        console.print(
            "Write readiness: blocked — live writes require the separately authorized "
            "disposable-entry probe described in plan issue 09."
        )
    for candidate in candidates:
        if not candidate.changed and candidate.valid:
            continue
        console.print(f"{_entry_prompt_label(candidate)}:")
        if candidate.original.tags != candidate.proposed.tags:
            console.print(f"  tags: {candidate.original.tags!r} -> {candidate.proposed.tags!r}")
        if candidate.original.project_id != candidate.proposed.project_id:
            console.print(
                f"  project: {_project_label(candidate.original.project_id, projects)} -> "
                f"{_project_label(candidate.proposed.project_id, projects)}"
            )
        if candidate.original.description != candidate.proposed.description:
            console.print(
                f"  description: {candidate.original.description!r} -> "
                f"{candidate.proposed.description!r}"
            )
        if candidate.issues:
            console.print("  unresolved: " + ", ".join(candidate.issues))


@app.callback()
def root() -> None:
    """Toggl reports and interactive entry review."""


@app.command()
def report(
    day: str | None = typer.Option(None, "--day", "-d", help="Reporting day in YYYY-MM-DD format."),
    week: bool = typer.Option(
        False, "--week", "-w", help="Report the Toggl-defined week containing the day."
    ),
    include_summary: bool = typer.Option(
        False, "--include-summary", help="Include client/project grouping."
    ),
    output_format: Annotated[
        ReportFormat,
        typer.Option(
            "--format",
            help="Report format: markdown, pretty (interactive terminal only), or json.",
            case_sensitive=False,
        ),
    ] = ReportFormat.MARKDOWN,
    debug: bool = typer.Option(
        False, "--debug", "-D", help="Write redacted diagnostics to stderr."
    ),
    api_token: str | None = typer.Option(
        None, "--api-token", help="One-use API token; never written to TOML."
    ),
    workspace_id: int | None = typer.Option(
        None, "--workspace-id", help="Override the configured workspace."
    ),
    save_config: bool = typer.Option(
        False, "--save-config", help="Save the token and workspace to secure settings."
    ),
) -> None:
    stderr = Console(stderr=True, no_color=True, markup=False)
    if output_format is ReportFormat.PRETTY and not _rich_terminal_output_enabled():
        stderr.print("Error: --format pretty requires an interactive terminal.")
        raise typer.Exit(code=2)
    try:
        selected_day = date.fromisoformat(day) if day else None
        config_path = default_config_path()
        settings = load_settings()
        selected_workspace = workspace_id if workspace_id is not None else settings.workspace_id
        if selected_workspace is not None and selected_workspace <= 0:
            raise ConfigError("Workspace ID must be a positive integer.")
        effective_settings = replace(settings, workspace_id=selected_workspace)
        credentials = KeyringCredentialStore()
        token = _api_token(api_token, credentials)
        if not token:
            raise ConfigError(
                "No API token is available. Provide --api-token, set TOGGL_API_TOKEN, "
                "or save one with --save-config."
            )
        if save_config:
            if config_path.exists() and not typer.confirm(
                f"Overwrite {config_path}?", default=False
            ):
                raise typer.Abort()
            save_settings(effective_settings, token, config_path, credentials)
            stderr.print("Saved workspace settings and API token to secure storage.")

        diagnostics = stderr.print if debug else None
        api = TogglApi(token, diagnostics=diagnostics)
        try:
            period, total, summary, activity_summary = ReportService(
                api, effective_settings
            ).run_with_entries(
                selected_day,
                week,
                include_summary,
                effective_settings.review.activity_types,
            )
        finally:
            api.close()
        if output_format is ReportFormat.MARKDOWN:
            typer.echo(
                render_report(period, total, include_summary, summary, activity_summary), nl=False
            )
        elif output_format is ReportFormat.JSON:
            typer.echo(
                render_report_json(period, total, include_summary, summary, activity_summary),
                nl=False,
            )
        else:
            console = Console(
                file=sys.stdout,
                markup=False,
                highlight=False,
            )
            render_pretty_report(console, period, total, include_summary, summary, activity_summary)
        for warning in total.warnings:
            stderr.print(f"Warning: {warning}")
    except (ApiError, ConfigError, ValueError) as error:
        stderr.print(f"Error: {error}")
        raise typer.Exit(code=2) from error


@app.command()
def review(  # pragma: no cover - interactive TTY boundary is covered by subprocess smoke tests
    day: str | None = typer.Option(None, "--day", "-d", help="Reporting day in YYYY-MM-DD format."),
    week: bool = typer.Option(
        False, "--week", "-w", help="Review the Toggl-defined week containing the day."
    ),
    debug: bool = typer.Option(
        False, "--debug", "-D", help="Write redacted diagnostics to stderr."
    ),
    api_token: str | None = typer.Option(
        None, "--api-token", help="One-use API token; never written to TOML."
    ),
    workspace_id: int | None = typer.Option(
        None, "--workspace-id", help="Override the configured workspace."
    ),
) -> None:
    """Interactively review and optionally repair entry-quality issues."""

    stderr = Console(stderr=True, no_color=True)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        stderr.print("Error: review requires an interactive terminal.")
        raise typer.Exit(code=2)
    api: TogglApi | None = None
    try:
        selected_day = date.fromisoformat(day) if day else None
        settings = load_settings()
        selected_workspace = workspace_id if workspace_id is not None else settings.workspace_id
        if selected_workspace is not None and selected_workspace <= 0:
            raise ConfigError("Workspace ID must be a positive integer.")
        effective_settings = replace(settings, workspace_id=selected_workspace)
        credentials = KeyringCredentialStore()
        token = _api_token(api_token, credentials)
        if not token:
            raise ConfigError(
                "No API token is available. Provide --api-token, set TOGGL_API_TOKEN, "
                "or save one with --save-config."
            )
        diagnostics = stderr.print if debug else None
        api = TogglApi(token, diagnostics=diagnostics)
        from .app import ReviewService

        service = ReviewService(api, effective_settings)
        period, profile, _entries, candidates, projects = service.snapshot(selected_day, week)
        rich_terminal_output = _rich_terminal_output_enabled()
        console = Console(
            color_system="auto" if rich_terminal_output else None,
            force_terminal=rich_terminal_output,
            highlight=rich_terminal_output,
            markup=False,
        )
        if not candidates:
            console.print(f"No entries need review for {period.start} to {period.end}.")
            return
        reviewed = tuple(
            _review_candidate(
                console,
                candidate,
                effective_settings.review.activity_types,
                effective_settings.review.jira_activity_types,
                projects,
                profile.timezone,
            )
            for candidate in candidates
        )
        _render_review_summary(
            console,
            reviewed,
            sum(not entry.is_marker and entry.user_id == profile.user_id for entry in _entries),
            projects=projects,
            writes_qualified=True,
        )
        if not any(candidate.changed for candidate in reviewed):
            console.print("No changes to write.")
            return
        quota = api.get_quota()
        writes = changed_entries(reviewed)
        if quota.remaining < len(writes):
            console.print(
                f"Only {quota.remaining} API request(s) remain; the plan needs "
                f"{len(writes)} per-entry PUT request(s). No changes were written."
            )
            return
        if not typer.confirm("Submit these changes?", default=False):
            console.print("No changes written.")
            return
        api.writes_qualified = True
        workspace = effective_settings.workspace_id or profile.default_workspace_id
        if workspace is None:
            raise ConfigError("No workspace is configured and Toggl has no default workspace.")
        result = service.submit(
            workspace,
            reviewed,
            quota_remaining=quota.remaining,
            quota_resets_in_seconds=quota.resets_in_seconds,
        )
        retry_label = "retry" if result.retries == 1 else "retries"
        quota_label = (
            f"; last known quota {result.quota_remaining}"
            f" (resets in {_format_countdown(result.quota_resets_in_seconds)})"
            if result.quota_remaining is not None and result.quota_resets_in_seconds is not None
            else ""
        )
        console.print(
            f"Submitted {len(result.success)} succeeded, {len(result.failures)} failed, "
            f"{len(result.uncertain)} uncertain, and {len(result.not_attempted)} not attempted "
            f"after {result.attempts} request(s) ({result.retries} {retry_label}){quota_label}."
        )
        for entry_id, message in result.failures:
            stderr.print(f"Warning: entry {entry_id}: {message}")
    except ReviewCancelled:
        stderr.print("Review cancelled; no changes written.")
    except (ApiError, ConfigError, ValueError) as error:
        stderr.print(f"Error: {error}")
        raise typer.Exit(code=2) from error
    finally:
        if api is not None:
            api.close()


def main() -> None:
    app()
