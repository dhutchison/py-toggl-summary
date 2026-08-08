from datetime import date, datetime
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
