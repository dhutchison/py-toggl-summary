"""Typer command-line adapter."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, tzinfo

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
from .render import render_report
from .review import (
    CONFLICTING_ACTIVITY_TYPE,
    MISSING_ACTIVITY_TYPE,
    MISSING_JIRA_REFERENCE,
    MISSING_PROJECT,
    Project,
    ReviewCandidate,
    add_jira_reference,
    grouped_patches,
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


_rich_terminal_output = _rich_terminal_output_enabled()
app = typer.Typer(
    help="Toggl reports and interactive entry review.",
    context_settings={"color": _rich_terminal_output and not os.environ.get("NO_COLOR")},
    rich_markup_mode="rich" if _rich_terminal_output else None,
)


class ReviewCancelled(Exception):
    pass


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
    patches = grouped_patches(candidates)
    operation_count = sum(len(operations) for _, operations in patches)
    console.print(
        f"Write plan: {sum(len(ids) for ids, _ in patches)} entries, "
        f"{operation_count} operations, {len(patches)} request(s), "
        "at least one second between requests; writes are partial and have no rollback."
    )
    if writes_qualified:
        console.print("Write readiness: live writes are qualified.")
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
    try:
        selected_day = date.fromisoformat(day) if day else None
        config_path = default_config_path()
        settings = load_settings(config_path)
        selected_workspace = workspace_id if workspace_id is not None else settings.workspace_id
        if selected_workspace is not None and selected_workspace <= 0:
            raise ConfigError("Workspace ID must be a positive integer.")
        effective_settings = replace(settings, workspace_id=selected_workspace)
        credentials = KeyringCredentialStore()
        token = api_token or load_token(credentials)
        if not token:
            raise ConfigError(
                "No API token is available. Provide --api-token or save one with --save-config."
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
        typer.echo(
            render_report(period, total, include_summary, summary, activity_summary), nl=False
        )
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
        settings = load_settings(default_config_path())
        selected_workspace = workspace_id if workspace_id is not None else settings.workspace_id
        if selected_workspace is not None and selected_workspace <= 0:
            raise ConfigError("Workspace ID must be a positive integer.")
        effective_settings = replace(settings, workspace_id=selected_workspace)
        credentials = KeyringCredentialStore()
        token = api_token or load_token(credentials)
        if not token:
            raise ConfigError(
                "No API token is available. Provide --api-token or save one with --save-config."
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
            writes_qualified=api.writes_qualified,
        )
        if not any(candidate.changed for candidate in reviewed):
            console.print("No changes to write.")
            return
        if not api.writes_qualified:
            console.print(
                "Live writes remain disabled pending the separately authorized disposable-entry "
                "probe described in plan issue 09. No changes were written."
            )
            return
        quota = api.get_quota()
        if quota.remaining < len(grouped_patches(reviewed)):
            console.print(
                f"Only {quota.remaining} API request(s) remain; the plan needs "
                f"{len(grouped_patches(reviewed))}. No changes were written."
            )
            return
        if not typer.confirm("Submit these changes?", default=False):
            console.print("No changes written.")
            return
        workspace = effective_settings.workspace_id or profile.default_workspace_id
        if workspace is None:
            raise ConfigError("No workspace is configured and Toggl has no default workspace.")
        result = service.submit(workspace, reviewed, quota_remaining=quota.remaining)
        retry_label = "retry" if result.retries == 1 else "retries"
        console.print(
            f"Submitted {len(result.success)} succeeded, {len(result.failures)} failed, "
            f"{len(result.uncertain)} uncertain, and {len(result.not_attempted)} not attempted "
            f"after {result.attempts} request(s) ({result.retries} {retry_label})."
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
