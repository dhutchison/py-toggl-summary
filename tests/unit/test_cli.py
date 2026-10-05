from datetime import UTC, date, datetime
from io import StringIO
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from toggl_cli import cli
from toggl_cli.api import Profile, Quota
from toggl_cli.config import Settings
from toggl_cli.domain import TimeEntry
from toggl_cli.review import Project


def test_format_countdown_uses_only_needed_time_units() -> None:
    assert cli._format_countdown(0) == "0s"
    assert cli._format_countdown(42) == "42s"
    assert cli._format_countdown(125) == "2m 5s"
    assert cli._format_countdown(3600) == "1h"
    assert cli._format_countdown(3661) == "1h 1m 1s"


class FakeApi:
    def __init__(self, token: str, diagnostics: Any = None, writes_qualified: bool = False) -> None:
        self.token = token
        self.diagnostics = diagnostics
        self.writes_qualified = writes_qualified

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


def test_rich_terminal_output_requires_a_tty_and_is_disabled_in_ci(
    monkeypatch: Any,
) -> None:
    class TTY(StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdout", TTY())
    monkeypatch.setattr("sys.stderr", TTY())
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert cli._rich_terminal_output_enabled() is True

    monkeypatch.setenv("CI", "true")
    assert cli._rich_terminal_output_enabled() is False

    monkeypatch.delenv("CI")
    monkeypatch.setattr("sys.stdout", StringIO())
    assert cli._rich_terminal_output_enabled() is False


def test_review_enables_qualified_writes_only_after_confirmation(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("CI", "true")

    class TTY(StringIO):
        def isatty(self) -> bool:
            return True

    class ReviewApi:
        instances: ClassVar[list["ReviewApi"]] = []

        def __init__(
            self, token: str, diagnostics: Any = None, writes_qualified: bool = False
        ) -> None:
            self.writes_qualified = writes_qualified
            self.put_states: list[bool] = []
            type(self).instances.append(self)

        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("UTC"), 1, 42, 7)

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
                    user_id=7,
                ),
            )

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            return (Project(9, "Project", workspace_id),)

        def get_quota(self) -> Quota:
            return Quota(remaining=2, resets_in_seconds=3661)

        def put_time_entry(
            self, workspace_id: int, entry_id: int, changes: dict[str, object]
        ) -> Quota:
            self.put_states.append(self.writes_qualified)
            return Quota(remaining=1, resets_in_seconds=3661)

        def close(self) -> None:
            pass

    monkeypatch.setattr("sys.stdin", TTY())
    output = TTY()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(cli, "TogglApi", ReviewApi)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    monkeypatch.setattr(cli, "load_token", lambda credentials: "secret-token")
    answers = iter(("doing", "Project"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)

    cli.review("2026-08-08", False, False, None, None)

    assert ReviewApi.instances[0].writes_qualified is True
    assert ReviewApi.instances[0].put_states == [True]
    assert "last known quota 1 (resets in 1h 1m 1s)" in output.getvalue()
