"""Small, validated adapters for the current Toggl APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .domain import TimeEntry

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


class DetailedRowDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    start: datetime
    stop: datetime | None = None
    seconds: float | None = None
    description: str | None = None
    tag_names: list[str] | None = None
    tag_ids: list[int] | None = None
    task_id: int | None = None
    task_name: str | None = None
    user_id: int | None = None
    username: str | None = None


class DetailedWrapperDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    client_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    tag_names: list[str] | None = None
    tag_ids: list[int] | None = None
    task_id: int | None = None
    task_name: str | None = None
    user_id: int | None = None
    username: str | None = None
    time_entries: list[DetailedRowDTO] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Profile:
    timezone: ZoneInfo
    beginning_of_week: int
    default_workspace_id: int | None


def _profile(value: ProfileDTO) -> Profile:
    if not 0 <= value.beginning_of_week <= 6:
        raise ApiError("Toggl returned an invalid beginning_of_week value.")
    try:
        configured_timezone = ZoneInfo(value.timezone)
    except ZoneInfoNotFoundError as error:
        raise ApiError(f"Toggl returned an unknown timezone: {value.timezone}.") from error
    return Profile(configured_timezone, value.beginning_of_week, value.default_workspace_id)


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
                if key.lower() in {"x-toggl-quota-remaining", "x-toggl-quota-resets-in"}
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
        body: dict[str, Any] = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "enrich_response": True,
            "grouped": False,
            "order_by": "date",
            "order_dir": "ASC",
            "page_size": 50,
            "rounding": 0,
        }
        next_row: int | None = None
        seen_cursors: set[int] = set()
        entries: dict[int, TimeEntry] = {}

        while True:
            request_body = dict(body)
            if next_row is not None:
                request_body["first_row_number"] = next_row
            response = self._request(
                "POST",
                f"{self._reports_base_url}/reports/api/v3/workspace/{workspace_id}/search/time_entries",
                json=request_body,
            )
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    payload = payload.get("data", [])
                wrappers = TypeAdapter(list[DetailedWrapperDTO]).validate_python(payload)
            except (ValidationError, TypeError, ValueError) as error:
                raise ApiError("Toggl returned an invalid detailed report page.") from error

            for wrapper in wrappers:
                for row in wrapper.time_entries:
                    entries[row.id] = self._to_entry(wrapper, row, report_now)

            if self._diagnostics:
                pagination = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in {"x-next-id", "x-range-start", "x-range-end"}
                }
                if pagination:
                    self._diagnostics(f"pagination {pagination}")

            cursor_value = response.headers.get("X-Next-Row-Number")
            if cursor_value is None:
                break
            try:
                next_row = int(cursor_value)
            except ValueError as error:
                raise ApiError("Toggl returned an invalid pagination cursor.") from error
            if next_row in seen_cursors:
                raise ApiError("Toggl returned a repeated pagination cursor.")
            seen_cursors.add(next_row)

        return tuple(sorted(entries.values(), key=lambda entry: (entry.start, entry.id)))

    @staticmethod
    def _to_entry(
        wrapper: DetailedWrapperDTO, row: DetailedRowDTO, report_now: datetime
    ) -> TimeEntry:
        if row.start.tzinfo is None or (row.stop is not None and row.stop.tzinfo is None):
            raise ApiError(f"Toggl returned a timezone-naive timestamp for entry {row.id}.")
        if row.stop is not None:
            calculated_ms = int((row.stop - row.start).total_seconds() * 1000)
        else:
            calculated_ms = int((report_now - row.start).total_seconds() * 1000)
        duration_ms = (
            calculated_ms if row.seconds is None or row.seconds < 0 else int(row.seconds * 1000)
        )
        tags = tuple(row.tag_names if row.tag_names is not None else wrapper.tag_names or ())
        tag_ids = tuple(row.tag_ids if row.tag_ids is not None else wrapper.tag_ids or ())
        return TimeEntry(
            id=row.id,
            description=row.description or wrapper.description or "",
            start=row.start,
            stop=row.stop,
            duration_ms=max(0, duration_ms),
            tags=tags,
            tag_ids=tag_ids,
            client_name=wrapper.client_name,
            project_name=wrapper.project_name,
            project_id=wrapper.project_id,
            task_id=row.task_id if row.task_id is not None else wrapper.task_id,
            task_name=row.task_name if row.task_name is not None else wrapper.task_name,
            user_id=row.user_id if row.user_id is not None else wrapper.user_id,
            username=row.username if row.username is not None else wrapper.username,
        )
