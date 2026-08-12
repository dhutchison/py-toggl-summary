"""Application service for report generation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from time import monotonic, sleep
from typing import Protocol

from .api import ApiError, Profile, Quota
from .config import ConfigError, Settings
from .domain import (
    DEFAULT_ACTIVITY_TYPES,
    ActivityTypeSummary,
    ReportingPeriod,
    SummaryGroup,
    TimeEntry,
    TimeSummary,
    calculate_activity_summary,
    calculate_summary,
    calculate_time_totals,
)
from .review import (
    Project,
    ReviewCandidate,
    WriteResult,
    changed_entries,
    ordered_candidates,
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
        period, total, summary, _ = self.run_with_entries(
            selected_day, week, include_summary, DEFAULT_ACTIVITY_TYPES
        )
        return period, total, summary

    def run_with_entries(
        self,
        selected_day: date | None,
        week: bool,
        include_summary: bool,
        activity_types: tuple[str, ...] = DEFAULT_ACTIVITY_TYPES,
    ) -> tuple[
        ReportingPeriod,
        TimeSummary,
        tuple[SummaryGroup, ...],
        tuple[ActivityTypeSummary, ...],
    ]:
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
        activity_summary = (
            calculate_activity_summary(entries, total, activity_types) if include_summary else ()
        )
        return period, total, summary, activity_summary


class ReviewApiPort(TogglApiPort, Protocol):
    def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]: ...

    def put_time_entry(
        self, workspace_id: int, entry_id: int, changes: dict[str, object]
    ) -> Quota | None: ...


class ReviewService:
    def __init__(
        self,
        api: ReviewApiPort,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] = sleep,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        self._api = api
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn

    def snapshot(
        self, selected_day: date | None, week: bool
    ) -> tuple[
        ReportingPeriod,
        Profile,
        tuple[TimeEntry, ...],
        tuple[ReviewCandidate, ...],
        tuple[Project, ...],
    ]:
        report_now = self._clock()
        profile = self._api.get_profile()
        if profile.user_id is None:
            raise ConfigError(
                "Toggl did not provide the authenticated user ID; review is disabled."
            )
        day = selected_day or report_now.astimezone(profile.timezone).date()
        period = reporting_period(day, week, profile.beginning_of_week)
        workspace_id = self._settings.workspace_id or profile.default_workspace_id
        if workspace_id is None:
            raise ConfigError("No workspace is configured and Toggl has no default workspace.")
        entries = self._api.get_detailed_entries(
            workspace_id, period.start, period.end, report_now.astimezone(profile.timezone)
        )
        candidates = ordered_candidates(
            entries,
            profile.user_id,
            self._settings.review.activity_types,
            self._settings.review.jira_activity_types,
        )
        projects = (
            self._api.get_active_projects(workspace_id)
            if any("missing_project" in candidate.issues for candidate in candidates)
            else ()
        )
        return period, profile, entries, candidates, projects

    def submit(
        self,
        workspace_id: int,
        candidates: tuple[ReviewCandidate, ...] | list[ReviewCandidate],
        *,
        minimum_interval: float = 1.0,
        quota_remaining: int | None = None,
        quota_resets_in_seconds: int | None = None,
    ) -> WriteResult:
        """Submit one PUT per changed entry, pacing requests and retaining partial results."""

        success: list[int] = []
        failures: list[tuple[int, str]] = []
        uncertain: list[int] = []
        not_attempted: list[int] = []
        attempts = retries = 0
        last_request: float | None = None
        planned = changed_entries(candidates)
        planned_ids = tuple(entry_id for entry_id, _ in planned)
        if quota_remaining is None:
            return WriteResult(not_attempted=planned_ids)
        if quota_remaining < len(planned):
            return WriteResult(
                not_attempted=planned_ids,
                quota_remaining=quota_remaining,
                quota_resets_in_seconds=quota_resets_in_seconds,
            )
        remaining = quota_remaining
        resets_in_seconds = quota_resets_in_seconds
        for entry_index, (entry_id, changes) in enumerate(planned):
            for attempt in range(3):
                if remaining < 1:
                    later_ids = tuple(
                        later_entry_id for later_entry_id, _ in planned[entry_index + 1 :]
                    )
                    return WriteResult(
                        success=tuple(success),
                        failures=tuple(failures),
                        uncertain=tuple(uncertain),
                        not_attempted=(entry_id, *later_ids),
                        attempts=attempts,
                        retries=retries,
                        quota_remaining=remaining,
                        quota_resets_in_seconds=resets_in_seconds,
                    )
                if last_request is not None:
                    wait = minimum_interval - (self._monotonic() - last_request)
                    if wait > 0:
                        self._sleep(wait)
                last_request = self._monotonic()
                attempts += 1
                try:
                    observed_quota = self._api.put_time_entry(workspace_id, entry_id, changes)
                    remaining = (
                        observed_quota.remaining if observed_quota else max(0, remaining - 1)
                    )
                    if observed_quota:
                        resets_in_seconds = observed_quota.resets_in_seconds
                except ApiError as error:
                    observed_remaining = error.headers.get("x-toggl-quota-remaining")
                    observed_resets = error.headers.get("x-toggl-quota-resets-in")
                    try:
                        remaining = (
                            int(observed_remaining)
                            if observed_remaining is not None
                            else max(0, remaining - 1)
                        )
                    except ValueError:
                        remaining = max(0, remaining - 1)
                    if observed_resets is not None:
                        with suppress(ValueError):
                            resets_in_seconds = int(observed_resets)
                    transient = remaining > 0 and (
                        error.status_code is None
                        or error.status_code
                        in {
                            408,
                            425,
                            500,
                            502,
                            503,
                            504,
                        }
                    )
                    if error.status_code == 429 and remaining > 0:
                        transient = True
                    if transient and attempt < 2:
                        retries += 1
                        retry_after = error.headers.get("retry-after")
                        try:
                            retry_wait = float(retry_after) if retry_after is not None else 0.0
                        except ValueError:
                            retry_wait = 0.0
                        self._sleep(max(1.0, retry_wait, float(2**attempt)))
                        continue
                    reason = "uncertain outcome" if transient else str(error)
                    if error.status_code in {401, 403}:
                        not_attempted.extend(
                            later_entry_id for later_entry_id, _ in planned[entry_index + 1 :]
                        )
                        failures.append((entry_id, reason))
                        return WriteResult(
                            tuple(success),
                            tuple(failures),
                            tuple(uncertain),
                            tuple(not_attempted),
                            attempts,
                            retries,
                            remaining,
                            resets_in_seconds,
                        )
                    if reason == "uncertain outcome":
                        uncertain.append(entry_id)
                        not_attempted.extend(
                            later_entry_id for later_entry_id, _ in planned[entry_index + 1 :]
                        )
                        return WriteResult(
                            tuple(success),
                            tuple(failures),
                            tuple(uncertain),
                            tuple(not_attempted),
                            attempts,
                            retries,
                            remaining,
                            resets_in_seconds,
                        )
                    failures.append((entry_id, reason))
                    break
                else:
                    success.append(entry_id)
                    break
        return WriteResult(
            tuple(success),
            tuple(failures),
            tuple(uncertain),
            tuple(not_attempted),
            attempts,
            retries,
            remaining,
            resets_in_seconds,
        )
