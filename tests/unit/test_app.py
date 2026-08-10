from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from toggl_cli.api import ApiError, Profile
from toggl_cli.app import ReportService, ReviewService, reporting_period
from toggl_cli.config import ConfigError, Settings
from toggl_cli.domain import TimeEntry
from toggl_cli.review import PatchResult, Project, ReviewCandidate, candidate_for, replace_project


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


def test_review_snapshot_fails_closed_without_authenticated_user_id() -> None:
    class MissingUserApi:
        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("UTC"), 1, 42)

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            raise AssertionError("must fail before loading entries")

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            raise AssertionError("must fail before loading projects")

        def bulk_patch(
            self,
            workspace_id: int,
            entry_ids: tuple[int, ...],
            operations: tuple[dict[str, object], ...],
        ) -> PatchResult:
            raise AssertionError("must fail before writing")

    with pytest.raises(ConfigError, match="authenticated user ID"):
        ReviewService(MissingUserApi(), Settings()).snapshot(date(2026, 8, 8), False)


def test_review_snapshot_requires_a_workspace_after_authentication() -> None:
    class NoWorkspaceReviewApi:
        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("UTC"), 1, None, 7)

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            raise AssertionError("must fail before loading entries")

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            raise AssertionError("must fail before loading projects")

        def bulk_patch(
            self,
            workspace_id: int,
            entry_ids: tuple[int, ...],
            operations: tuple[dict[str, object], ...],
        ) -> PatchResult:
            raise AssertionError("must fail before writing")

    with pytest.raises(ConfigError, match="No workspace"):
        ReviewService(NoWorkspaceReviewApi(), Settings()).snapshot(date(2026, 8, 8), False)


def test_review_submit_paces_groups_and_keeps_per_id_failures() -> None:
    class WriteApi:
        def __init__(self) -> None:
            self.calls: list[tuple[int, ...]] = []

        def get_profile(self) -> Profile:
            raise AssertionError

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            raise AssertionError

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            raise AssertionError

        def bulk_patch(
            self,
            workspace_id: int,
            entry_ids: tuple[int, ...],
            operations: tuple[dict[str, object], ...],
        ) -> PatchResult:
            self.calls.append(entry_ids)
            return PatchResult(
                success=entry_ids[:1],
                failures=tuple((i, "bad") for i in entry_ids[1:]),
            )

    original = TimeEntry(
        1,
        "old",
        datetime(2026, 8, 8, 9, tzinfo=UTC),
        datetime(2026, 8, 8, 10, tzinfo=UTC),
        3_600_000,
        project_id=None,
        user_id=7,
    )
    candidate = candidate_for(original, ("doing",))
    candidate = ReviewCandidate(
        candidate.original,
        replace_project(candidate.proposed, Project(8, "Project")),
        candidate.issues,
        candidate.original_issues,
    )
    second_original = replace(original, id=2, project_id=10)
    second = candidate_for(second_original, ("doing",))
    second = ReviewCandidate(
        second.original,
        replace(second.proposed, description="new"),
        second.issues,
        second.original_issues,
    )
    api = WriteApi()
    now = [0.0]
    sleeps: list[float] = []
    service = ReviewService(api, Settings(), monotonic_fn=lambda: now[0], sleep_fn=sleeps.append)

    result = service.submit(
        42,
        (candidate, second),
        minimum_interval=1.0,
        quota_remaining=10,
    )

    assert api.calls == [(1,), (2,)]
    assert sleeps == [1.0]
    assert result.success == (1, 2)
    assert result.failures == ()


def test_review_snapshot_loads_projects_once_for_missing_project() -> None:
    class SnapshotApi:
        def get_profile(self) -> Profile:
            return Profile(ZoneInfo("UTC"), 1, 42, 7)

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            return (
                TimeEntry(
                    1,
                    "work",
                    datetime(2026, 8, 8, 9, tzinfo=UTC),
                    datetime(2026, 8, 8, 10, tzinfo=UTC),
                    3_600_000,
                    user_id=7,
                ),
            )

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            return (Project(9, "Project", workspace_id=workspace_id),)

        def bulk_patch(
            self,
            workspace_id: int,
            entry_ids: tuple[int, ...],
            operations: tuple[dict[str, object], ...],
        ) -> PatchResult:
            raise AssertionError("snapshot must not write")

    period, profile, entries, candidates, projects = ReviewService(
        SnapshotApi(),
        Settings(),
        clock=lambda: datetime(2026, 8, 8, 12, tzinfo=UTC),
    ).snapshot(date(2026, 8, 8), False)

    assert period.start == date(2026, 8, 8)
    assert profile.user_id == 7
    assert len(entries) == 1
    assert candidates[0].issues == ("missing_activity_type", "missing_project")
    assert projects == (Project(9, "Project", workspace_id=42),)


def test_review_submit_stops_after_uncertain_retry_exhaustion() -> None:
    class FailingWriteApi:
        def get_profile(self) -> Profile:
            raise AssertionError

        def get_detailed_entries(
            self, workspace_id: int, start_date: date, end_date: date, report_now: datetime
        ) -> tuple[TimeEntry, ...]:
            raise AssertionError

        def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
            raise AssertionError

        def bulk_patch(
            self,
            workspace_id: int,
            entry_ids: tuple[int, ...],
            operations: tuple[dict[str, object], ...],
        ) -> PatchResult:
            raise ApiError("temporary", status_code=500)

    original = TimeEntry(
        1,
        "old",
        datetime(2026, 8, 8, 9, tzinfo=UTC),
        datetime(2026, 8, 8, 10, tzinfo=UTC),
        3_600_000,
        project_id=None,
        user_id=7,
    )
    candidate = candidate_for(original, ("doing",))
    proposed = ReviewCandidate(
        candidate.original,
        replace_project(candidate.proposed, Project(8, "Project")),
        candidate.issues,
        candidate.original_issues,
    )
    sleeps: list[float] = []
    result = ReviewService(
        FailingWriteApi(),
        Settings(),
        monotonic_fn=lambda: 0.0,
        sleep_fn=sleeps.append,
    ).submit(42, (proposed,), minimum_interval=0, quota_remaining=10)

    assert result.uncertain == (1,)
    assert result.not_attempted == ()
    assert result.attempts == 3
    assert result.retries == 2
    assert sleeps == [1.0, 2.0]
