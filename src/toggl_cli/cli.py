"""Typer command-line adapter."""

from __future__ import annotations

from datetime import date

import typer
from rich.console import Console

from .api import ApiError, TogglApi
from .app import ReportService
from .config import (
    ConfigError,
    KeyringCredentialStore,
    Settings,
    default_config_path,
    load_settings,
    load_token,
    save_settings,
)
from .render import render_report

app = typer.Typer(help="Read-only Toggl reports.")


@app.callback()
def root() -> None:
    """Read-only Toggl reports."""


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
    stderr = Console(stderr=True, no_color=True)
    try:
        selected_day = date.fromisoformat(day) if day else None
        config_path = default_config_path()
        settings = load_settings(config_path)
        selected_workspace = workspace_id if workspace_id is not None else settings.workspace_id
        if selected_workspace is not None and selected_workspace <= 0:
            raise ConfigError("Workspace ID must be a positive integer.")
        effective_settings = Settings(selected_workspace)
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
            period, total, summary = ReportService(api, effective_settings).run(
                selected_day, week, include_summary
            )
        finally:
            api.close()
        typer.echo(render_report(period, total, include_summary, summary), nl=False)
        for warning in total.warnings:
            stderr.print(f"Warning: {warning}")
    except (ApiError, ConfigError, ValueError) as error:
        stderr.print(f"Error: {error}")
        raise typer.Exit(code=2) from error


def main() -> None:
    app()
