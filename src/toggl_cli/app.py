"""Application service for report generation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from .api import Profile
from .config import ConfigError, Settings
from .domain import (
    ReportingPeriod,
    SummaryGroup,
    TimeEntry,
    TimeSummary,
    calculate_summary,
    calculate_time_totals,
)


class TogglApiPort(Protocol):
    def get_profile(self) -> Profile: ...

    def get_detailed_entries(
        self,
        workspace_id: int,
        start_date: date,
        end_date: date,
        report_now: datetime,
    ) -> tuple[TimeEntry, ...]: ...


def reporting_period(selected_day: date, week: bool, beginning_of_week: int) -> ReportingPeriod:
    if not week:
        return ReportingPeriod(selected_day, selected_day)
    first_weekday = (beginning_of_week - 1) % 7
    offset = (selected_day.weekday() - first_weekday) % 7
    start = selected_day - timedelta(days=offset)
    return ReportingPeriod(start, start + timedelta(days=6))


class ReportService:
    def __init__(
        self,
        api: TogglApiPort,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._api = api
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        selected_day: date | None,
        week: bool,
        include_summary: bool,
    ) -> tuple[ReportingPeriod, TimeSummary, tuple[SummaryGroup, ...]]:
        report_now = self._clock()
        profile = self._api.get_profile()
        day = selected_day or report_now.astimezone(profile.timezone).date()
        period = reporting_period(day, week, profile.beginning_of_week)
        workspace_id = self._settings.workspace_id or profile.default_workspace_id
        if workspace_id is None:
            raise ConfigError("No workspace is configured and Toggl has no default workspace.")
        entries = self._api.get_detailed_entries(
            workspace_id, period.start, period.end, report_now.astimezone(profile.timezone)
        )
        total = calculate_time_totals(entries, report_now, profile.timezone)
        summary = calculate_summary(entries, total) if include_summary else ()
        return period, total, summary
