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
    MISSING_JIRA_REFERENCE,
    Project,
    ReviewCandidate,
    ReviewGroup,
    activity_issue,
    add_jira_reference,
    changed_entries,
    effective_activity_type,
    matching_prefix,
    normalize_description,
    propose,
    replace_activity_type,
    replace_project,
    review_groups,
    review_state,
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
    description = " ".join(candidate.proposed.description.split()) or "(no description)"
    return f"{description} — entry {candidate.original.id}"


def _project_label(project_id: int | None, projects: tuple[Project, ...]) -> str:
    if project_id is None:
        return "(none)"
    project = next((project for project in projects if project.id == project_id), None)
    return f"{project.name} ({project.id})" if project else f"(unknown project {project_id})"


def _choose_activity(
    console: Console,
    candidate: ReviewCandidate,
    activity_types: tuple[str, ...],
    prompt_label: str | None = None,
) -> str | None:
    while True:
        label = prompt_label or _entry_prompt_label(candidate)
        value = _prompt(console, f"{label} activity type (s to skip): ")
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
    console: Console,
    candidate: ReviewCandidate,
    projects: tuple[Project, ...],
    prompt_label: str | None = None,
) -> Project | None:
    label = prompt_label or _entry_prompt_label(candidate)
    active_projects = tuple(project for project in projects if project.active)
    if not active_projects:
        console.print(f"{label}: no active projects are available.")
        return None
    choices = tuple(project.name for project in active_projects)
    while True:
        value = _prompt(console, f"{label} project (s to skip): ")
        if value.casefold() == "s":
            return None
        matches = matching_prefix(value, choices)
        if len(matches) == 1:
            return next(project for project in active_projects if project.name == matches[0])
        if matches:
            console.print("Ambiguous choice: " + ", ".join(matches))
        else:
            console.print("Choose an active project by name or unique prefix.")


def _review_group(
    console: Console,
    group: ReviewGroup,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...],
    projects: tuple[Project, ...],
    display_timezone: tzinfo,
) -> tuple[ReviewCandidate, ...]:
    """Resolve shared group fields once, then apply them to flagged entries only."""

    representative = group.candidates[0]
    description = (
        group.description if normalize_description(group.description) else "(no description)"
    )
    description = " ".join(description.split())
    entry_ids = ", ".join(str(entry.id) for entry in group.members)
    label = f"{description} — entries {entry_ids}"
    console.print(label)
    active_projects = {project.id: project for project in projects if project.active}
    fully_valid = tuple(
        entry
        for entry in group.members
        if not review_state(entry, activity_types, jira_activity_types).issues
    )
    sources = fully_valid or group.members

    for entry in group.members:
        start = entry.start.astimezone(display_timezone)
        stop = entry.stop.astimezone(display_timezone) if entry.stop else None
        entry_status = "reference" if entry in fully_valid else "needs changes"
        existing_activity = effective_activity_type(entry, activity_types) or "(unclassified)"
        existing_project = _project_label(entry.project_id, projects)
        console.print(
            f"  Entry {entry.id} ({entry_status}): {start.isoformat()} - "
            f"{stop.isoformat() if stop else 'running'}; "
            f"project={existing_project}; activity={existing_activity}"
        )

    def shared_activity() -> tuple[str | None, bool]:
        values = [
            value
            for entry in sources
            if (value := effective_activity_type(entry, activity_types)) is not None
        ]
        distinct = {value.casefold(): value for value in values}
        conflict = len(distinct) > 1
        return (next(iter(distinct.values())) if len(distinct) == 1 else None, conflict)

    def shared_project() -> tuple[Project | None, bool, bool]:
        unavailable_reference = any(
            entry.project_id not in active_projects for entry in fully_valid
        )
        values = [
            active_projects[entry.project_id]
            for entry in sources
            if entry.project_id in active_projects
        ]
        distinct = {project.id: project for project in values}
        conflict = len(distinct) > 1
        if unavailable_reference:
            return None, conflict, True
        return (next(iter(distinct.values())) if len(distinct) == 1 else None, conflict, False)

    activity, activity_conflict = shared_activity()
    project, project_conflict, project_forced_prompt = shared_project()
    activity_needs_prompt = activity is None or activity_conflict
    project_needs_prompt = project is None or project_conflict or project_forced_prompt
    if activity is not None and not activity_needs_prompt:
        console.print(f"  Inferred shared activity type: {activity}")
    if project is not None and not project_needs_prompt:
        console.print(f"  Inferred shared project: {project.name} ({project.id})")

    current = {candidate.original.id: candidate for candidate in group.candidates}
    selected_activity = activity
    if activity_needs_prompt:
        selected_activity = _choose_activity(
            console, representative, activity_types, prompt_label=label
        )
    selected_project = project
    if project_needs_prompt:
        selected_project = _choose_project(console, representative, projects, prompt_label=label)

    for candidate in group.candidates:
        proposed = candidate.proposed
        if selected_activity is not None:
            current_activity = effective_activity_type(proposed, activity_types)
            if (
                activity_issue(proposed, activity_types) is not None
                or current_activity != selected_activity
            ):
                proposed = replace_activity_type(proposed, selected_activity, activity_types)
        if selected_project is not None and proposed.project_id != selected_project.id:
            proposed = replace_project(proposed, selected_project)
        current[candidate.original.id] = propose(
            candidate, proposed, activity_types, jira_activity_types
        )

    jira_targets = tuple(
        entry_id
        for entry_id, candidate in current.items()
        if MISSING_JIRA_REFERENCE in candidate.issues
    )
    if jira_targets:
        while True:
            value = _prompt(console, f"{label} Jira key (s to skip): ")
            if value.casefold() == "s":
                break
            try:
                for entry_id in jira_targets:
                    candidate = current[entry_id]
                    current[entry_id] = propose(
                        candidate,
                        add_jira_reference(candidate.proposed, value),
                        activity_types,
                        jira_activity_types,
                    )
            except ValueError as error:
                console.print(str(error))
                continue
            break

    disagreement_source = "references" if fully_valid else "entries"
    if activity_conflict and (selected_activity is None or fully_valid):
        console.print(f"  Group warning: matching {disagreement_source} disagree on activity type.")
    if project_conflict and (selected_project is None or fully_valid):
        console.print(f"  Group warning: matching {disagreement_source} disagree on project.")
    if project_forced_prompt:
        console.print(
            "  Group warning: a reference project is unavailable in active choices; "
            "reference entries remain unchanged."
        )
    return tuple(current[candidate.original.id] for candidate in group.candidates)


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
        authenticated_user_id = profile.user_id
        if authenticated_user_id is None:
            raise ConfigError(
                "Toggl did not provide the authenticated user ID; review is disabled."
            )
        groups = review_groups(_entries, candidates, authenticated_user_id)
        reviewed = tuple(
            candidate
            for group in groups
            for candidate in _review_group(
                console,
                group,
                effective_settings.review.activity_types,
                effective_settings.review.jira_activity_types,
                projects,
                profile.timezone,
            )
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
