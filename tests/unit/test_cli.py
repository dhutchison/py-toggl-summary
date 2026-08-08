from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from toggl_cli import cli
from toggl_cli.api import Profile
from toggl_cli.domain import TimeEntry


class FakeApi:
    def __init__(self, token: str, diagnostics: Any = None) -> None:
        self.token = token
        self.diagnostics = diagnostics

    def get_profile(self) -> Profile:
        return Profile(ZoneInfo("UTC"), 1, 7)

    def get_detailed_entries(
        self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
    ) -> tuple[TimeEntry, ...]:
        return (
            TimeEntry(
                id=1,
                description="work",
                start=datetime(2026, 8, 8, 9, tzinfo=UTC),
                stop=datetime(2026, 8, 8, 10, tzinfo=UTC),
                duration_ms=3_600_000,
            ),
        )

    def close(self) -> None:
        pass


def test_report_writes_markdown_to_stdout_and_diagnostics_to_stderr(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli, "TogglApi", FakeApi)
    monkeypatch.setattr(cli, "load_token", lambda credentials: "secret-token")
    result = CliRunner().invoke(cli.app, ["report", "--day", "2026-08-08", "--debug"])

    assert result.exit_code == 0
    assert "# Totals for 2026-08-08 to 2026-08-08" in result.stdout
    assert "secret-token" not in result.output


def test_report_without_credentials_returns_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli, "load_token", lambda credentials: None)
    result = CliRunner().invoke(cli.app, ["report", "--day", "2026-08-08"])

    assert result.exit_code == 2
    assert "No API token is available" in result.output
