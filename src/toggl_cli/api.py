"""Small, validated adapters for the current Toggl APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .domain import TimeEntry
from .review import PatchResult, Project

TRACK_BASE_URL = "https://api.track.toggl.com"
REPORTS_BASE_URL = "https://api.track.toggl.com"


class ApiError(RuntimeError):
    """A redacted, user-facing API failure."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


class ProfileDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timezone: str
    beginning_of_week: int
    default_workspace_id: int | None = None
    id: int | None = None


class ProjectDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    active: bool = True
    status: str | None = None
    workspace_id: int | None = None


class DetailedV2RowDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    start: datetime
    end: datetime | None = None
    dur: int
    description: str | None = None
    tags: list[str] | None = None
    pid: int | None = None
    project: str | None = None
    client: str | None = None
    tid: int | None = None
    task: str | None = None
    uid: int | None = None
    user: str | None = None
    use_stop: bool = True


class DetailedV2ResponseDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_count: int
    per_page: int
    data: list[DetailedV2RowDTO]


@dataclass(frozen=True, slots=True)
class Profile:
    timezone: ZoneInfo
    beginning_of_week: int
    default_workspace_id: int | None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class Quota:
    remaining: int
    resets_in_seconds: int


def _profile(value: ProfileDTO) -> Profile:
    if not 0 <= value.beginning_of_week <= 6:
        raise ApiError("Toggl returned an invalid beginning_of_week value.")
    try:
        configured_timezone = ZoneInfo(value.timezone)
    except ZoneInfoNotFoundError as error:
        raise ApiError(f"Toggl returned an unknown timezone: {value.timezone}.") from error
    return Profile(
        configured_timezone,
        value.beginning_of_week,
        value.default_workspace_id,
        value.id,
    )


class TogglApi:
    """Synchronous Toggl API adapter with a narrow domain-facing interface."""

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        track_base_url: str = TRACK_BASE_URL,
        reports_base_url: str = REPORTS_BASE_URL,
        diagnostics: Callable[[str], None] | None = None,
        writes_qualified: bool = False,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            auth=(token, "api_token"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=httpx.Timeout(20.0),
        )
        self._track_base_url = track_base_url.rstrip("/")
        self._reports_base_url = reports_base_url.rstrip("/")
        self._diagnostics = diagnostics
        self.writes_qualified = writes_qualified

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._diagnostics:
            self._diagnostics(f"{method} {url}")
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise ApiError(f"Toggl request failed: {error.__class__.__name__}.") from error
        if response.is_error:
            message = f"Toggl request failed with HTTP {response.status_code}."
            quota_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in {"x-toggl-quota-remaining", "x-toggl-quota-resets-in", "retry-after"}
            }
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("error") or body.get("message")
                    if isinstance(detail, str) and detail:
                        message = f"{message} {detail[:240]}"
            except (ValueError, TypeError):
                pass
            raise ApiError(message, response.status_code, quota_headers)
        return response

    def get_profile(self) -> Profile:
        response = self._request("GET", f"{self._track_base_url}/api/v9/me")
        try:
            return _profile(ProfileDTO.model_validate(response.json()))
        except (ValidationError, TypeError, ValueError) as error:
            raise ApiError("Toggl returned an invalid user profile.") from error

    def get_detailed_entries(
        self,
        workspace_id: int,
        start_date: date,
        end_date: date,
        report_now: datetime,
    ) -> tuple[TimeEntry, ...]:
        page = 1
        entries: dict[int, TimeEntry] = {}

        while True:
            response = self._request(
                "GET",
                f"{self._reports_base_url}/reports/api/v2/details",
                params={
                    "page": page,
                    "user_agent": "toggl-cli",
                    "workspace_id": workspace_id,
                    "since": start_date.isoformat(),
                    "until": end_date.isoformat(),
                },
            )
            try:
                report = DetailedV2ResponseDTO.model_validate(response.json())
                if report.total_count < 0 or report.per_page <= 0:
                    raise ValueError("invalid pagination metadata")
            except (ValidationError, TypeError, ValueError) as error:
                raise ApiError("Toggl returned an invalid detailed report page.") from error

            for row in report.data:
                entries[row.id] = self._to_v2_entry(row, report_now)

            if self._diagnostics:
                self._diagnostics(
                    f"pagination page={page} per_page={report.per_page} "
                    f"total_count={report.total_count}"
                )

            if (
                not report.data
                or len(report.data) < report.per_page
                or len(entries) >= report.total_count
            ):
                break
            page += 1

        return tuple(sorted(entries.values(), key=lambda entry: (entry.start, entry.id)))

    def get_active_projects(self, workspace_id: int) -> tuple[Project, ...]:
        response = self._request(
            "GET",
            f"{self._track_base_url}/api/v9/me/projects",
            params={"include_archived": "false"},
        )
        try:
            payload = response.json()
            items = payload.get("items", payload) if isinstance(payload, dict) else payload
            projects = [ProjectDTO.model_validate(item) for item in items]
        except (ValidationError, TypeError, ValueError, AttributeError) as error:
            raise ApiError("Toggl returned an invalid project collection.") from error
        return tuple(
            Project(
                id=project.id,
                name=project.name,
                workspace_id=project.workspace_id,
                active=project.active and project.status not in {"archived", "deleted"},
            )
            for project in projects
            if project.workspace_id == workspace_id
            and project.active
            and project.status not in {"archived", "deleted"}
        )

    def get_quota(self) -> Quota:
        response = self._request("GET", f"{self._track_base_url}/api/v9/me/quota")
        try:
            payload = response.json()
            items = payload["items"]
            quotas = [
                item
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("remaining"), int)
                and isinstance(item.get("resets_in_secs"), int)
            ]
            if not quotas:
                raise ValueError("missing quota items")
            return Quota(
                remaining=min(item["remaining"] for item in quotas),
                resets_in_seconds=max(item["resets_in_secs"] for item in quotas),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ApiError("Toggl returned an invalid API quota response.") from error

    def bulk_patch(
        self,
        workspace_id: int,
        entry_ids: tuple[int, ...],
        operations: tuple[dict[str, object], ...],
    ) -> PatchResult:
        if not self.writes_qualified:
            raise ApiError(
                "Live writes are disabled until the separately authorized Toggl probe is complete."
            )
        if not entry_ids or len(entry_ids) > 100:
            raise ValueError("Bulk patch requires between one and 100 entry IDs.")
        response = self._request(
            "PATCH",
            f"{self._track_base_url}/api/v9/workspaces/{workspace_id}/time_entries/"
            + ",".join(str(entry_id) for entry_id in entry_ids),
            json=list(operations),
        )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response is not an object")
            successes = tuple(int(value) for value in payload.get("success", []))
            failures = tuple(
                (int(item["id"]), str(item.get("message", "unknown failure"))[:240])
                for item in payload.get("failure", [])
            )
            submitted = set(entry_ids)
            returned = [*successes, *(entry_id for entry_id, _ in failures)]
            if (
                len(returned) != len(set(returned))
                or set(returned) != submitted
                or any(entry_id not in submitted for entry_id in returned)
            ):
                raise ValueError("bulk patch response did not cover submitted IDs")
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ApiError("Toggl returned an invalid bulk patch response.") from error
        return PatchResult(success=successes, failures=failures)

    @staticmethod
    def _to_v2_entry(row: DetailedV2RowDTO, report_now: datetime) -> TimeEntry:
        if row.start.tzinfo is None or (row.end is not None and row.end.tzinfo is None):
            raise ApiError(f"Toggl returned a timezone-naive timestamp for entry {row.id}.")
        if row.end is not None and row.dur >= 0:
            duration_ms = row.dur
        else:
            duration_ms = int((report_now - row.start).total_seconds() * 1000)
        return TimeEntry(
            id=row.id,
            description=row.description or "",
            start=row.start,
            stop=row.end,
            duration_ms=max(0, duration_ms),
            tags=tuple(row.tags or ()),
            client_name=row.client,
            project_name=row.project,
            project_id=row.pid,
            task_id=row.tid,
            task_name=row.task,
            user_id=row.uid,
            username=row.user,
        )
