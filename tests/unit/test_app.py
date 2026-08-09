from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from toggl_cli.api import Profile
from toggl_cli.app import ReportService, reporting_period
from toggl_cli.config import ConfigError, Settings
from toggl_cli.domain import TimeEntry


def test_week_uses_toggl_monday_week_start() -> None:
    assert reporting_period(date(2026, 8, 5), True, 1).start == date(2026, 8, 3)
    assert reporting_period(date(2026, 8, 5), True, 1).end == date(2026, 8, 9)


def test_week_uses_toggl_sunday_week_start() -> None:
    assert reporting_period(date(2026, 8, 5), True, 0).start == date(2026, 8, 2)


def test_report_requires_a_workspace() -> None:
    class NoWorkspaceApi:
        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("UTC"), 1, None)

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            return ()

    with pytest.raises(ConfigError, match="No workspace"):
        ReportService(NoWorkspaceApi(), Settings()).run(date(2026, 8, 8), False, False)


def test_report_uses_profile_timezone_for_default_day_and_running_entry() -> None:
    observed: dict[str, object] = {}

    class LondonApi:
        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("Europe/London"), 1, 42)

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            observed.update(
                workspace_id=workspace_id,
                start_date=start_date,
                end_date=end_date,
                report_now=report_now,
            )
            return (
                TimeEntry(
                    id=1,
                    description="running",
                    start=datetime(2026, 10, 25, 0, tzinfo=UTC),
                    stop=None,
                    duration_ms=30 * 60_000,
                ),
            )

    period, total, _ = ReportService(
        LondonApi(), Settings(), clock=lambda: datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    ).run(None, False, False)

    assert period.start == date(2026, 10, 25)
    assert period.end == date(2026, 10, 25)
    assert total.booked_ms == 30 * 60_000
    assert observed == {
        "workspace_id": 42,
        "start_date": date(2026, 10, 25),
        "end_date": date(2026, 10, 25),
        "report_now": datetime(2026, 10, 25, 1, 30, tzinfo=ZoneInfo("Europe/London")),
    }
