import json
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import Message
from io import BytesIO
from json import dumps as json_dumps
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from toggl_cli.api import ApiError, Quota, TogglApi

FIXTURE = Path(__file__).parents[1] / "fixtures" / "reports_v2_page.json"


class _ParsedUrl:
    def __init__(self, value: str) -> None:
        parsed = urlsplit(value)
        self.path = parsed.path
        self.params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}


class MockRequest:
    def __init__(self, request: Request) -> None:
        self._request = request
        self.full_url = request.full_url
        self.url = _ParsedUrl(request.full_url)
        self.headers = {key.lower(): value for key, value in request.header_items()}
        self.content = request.data if isinstance(request.data, bytes) else b""
        self.method = request.get_method()


class MockResponse(BytesIO):
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
    ) -> None:
        super().__init__(b"" if json is None else json_dumps(json).encode("utf-8"))
        self._status = status_code
        self.code = status_code
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    @property
    def status(self) -> int:
        return self._status


class MockTransport:
    def __init__(self, handler: Callable[[MockRequest], MockResponse]) -> None:
        self.handler = handler

    def __call__(self, request: Request) -> MockResponse:
        return self.handler(MockRequest(request))


def test_captured_v2_fixture_preserves_contract_shape() -> None:
    payload = json.loads(FIXTURE.read_text())
    rows = payload["data"]

    assert payload["total_count"] == 8
    assert payload["per_page"] == 50
    assert payload["total_grand"] == sum(row["dur"] for row in rows)
    assert len(rows) == payload["total_count"]
    assert all("end" in row and (row["end"] is None or isinstance(row["end"], str)) for row in rows)
    assert all(isinstance(row["updated"], str) for row in rows)
    assert all(isinstance(row["use_stop"], bool) for row in rows)
    assert all(
        datetime.fromisoformat(row["start"]).utcoffset() == timedelta(hours=1) for row in rows
    )


def test_profile_and_detailed_adapter_maps_captured_v2_page() -> None:
    requests: list[MockRequest] = []

    def handler(request: MockRequest) -> MockResponse:
        requests.append(request)
        if request.url.path == "/api/v9/me":
            return MockResponse(
                200,
                json={
                    "timezone": "Europe/London",
                    "beginning_of_week": 1,
                    "default_workspace_id": 42,
                },
            )
        if request.url.path == "/reports/api/v2/details":
            assert request.url.params["workspace_id"] == "42"
            assert request.url.params["since"] == "2026-08-07"
            assert request.url.params["until"] == "2026-08-07"
            assert request.url.params["user_agent"] == "toggl-cli"
            return MockResponse(200, json=json.loads(FIXTURE.read_text()))
        raise AssertionError(f"unexpected request path: {request.url.path}")

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        profile = api.get_profile()
        entries = api.get_detailed_entries(
            42,
            datetime(2026, 8, 7).date(),
            datetime(2026, 8, 7).date(),
            datetime(2026, 8, 7, 18, tzinfo=profile.timezone),
        )
    finally:
        api.close()

    assert profile.default_workspace_id == 42
    assert [item.id for item in entries] == list(range(1, 9))
    assert entries[0].duration_ms == 1_800_000
    assert entries[-1].duration_ms == 4_228_000
    assert entries[0].description == "Synthetic description 1"
    assert entries[0].start.utcoffset() == timedelta(hours=1)
    assert entries[0].client_name is None
    assert entries[0].project_id is None
    assert entries[0].tags == ("synthetic-tags",)
    assert requests[1].headers["authorization"].startswith("Basic ")


def test_v2_adapter_follows_page_number_pagination() -> None:
    requests: list[str] = []

    def handler(request: MockRequest) -> MockResponse:
        if request.url.path != "/reports/api/v2/details":
            return MockResponse(200, json=[])
        page = request.url.params["page"]
        hour = 8 + int(page)
        requests.append(page)
        entry = {
            "id": int(page),
            "start": f"2026-08-08T{hour:02d}:00:00+00:00",
            "end": None if page == "2" else f"2026-08-08T{hour:02d}:30:00+00:00",
            "dur": -1 if page == "2" else 1_800_000,
        }
        return MockResponse(
            200,
            json={"data": [entry], "total_count": 2, "per_page": 1},
        )

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        entries = api.get_detailed_entries(
            42,
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8, 12, tzinfo=ZoneInfo("UTC")),
        )
    finally:
        api.close()

    assert requests == ["1", "2"]
    assert [item.id for item in entries] == [1, 2]
    assert entries[1].stop is None
    assert entries[1].duration_ms == 2 * 60 * 60 * 1000


def test_api_error_is_classified_without_exposing_authentication() -> None:
    def handler(request: MockRequest) -> MockResponse:
        return MockResponse(
            403,
            headers={"X-Toggl-Quota-Remaining": "29"},
            json={"error": "workspace denied"},
        )

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="HTTP 403") as raised:
            api.get_profile()
    finally:
        api.close()

    assert "secret-token" not in str(raised.value)
    assert raised.value.status_code == 403
    assert raised.value.headers == {"x-toggl-quota-remaining": "29"}


def test_urlopen_http_error_preserves_safe_quota_headers() -> None:
    def handler(request: MockRequest) -> MockResponse:
        headers = Message()
        headers["X-Toggl-Quota-Remaining"] = "3"
        raise HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            headers,
            BytesIO(b'{"message":"slow down"}'),
        )

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match=r"HTTP 429. slow down") as raised:
            api.get_profile()
    finally:
        api.close()

    assert raised.value.headers == {"x-toggl-quota-remaining": "3"}
    assert "secret-token" not in str(raised.value)


def test_urlopen_network_errors_do_not_expose_reason() -> None:
    def handler(request: MockRequest) -> MockResponse:
        raise URLError("connection failed while handling secret-token")

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="request failed: URLError") as raised:
            api.get_profile()
    finally:
        api.close()

    assert "secret-token" not in str(raised.value)


def test_invalid_v2_report_page_is_rejected() -> None:
    def handler(request: MockRequest) -> MockResponse:
        return MockResponse(200, json={"data": "not-a-page"})

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="invalid detailed report page"):
            api.get_detailed_entries(
                42,
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8, 12, tzinfo=ZoneInfo("UTC")),
            )
    finally:
        api.close()


def test_invalid_profile_is_rejected() -> None:
    def handler(request: MockRequest) -> MockResponse:
        return MockResponse(200, json={"timezone": "Not/AZone", "beginning_of_week": 1})

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="unknown timezone"):
            api.get_profile()
    finally:
        api.close()


def test_invalid_v2_pagination_metadata_is_rejected() -> None:
    def bad_metadata(request: MockRequest) -> MockResponse:
        return MockResponse(
            200,
            json={"data": [], "total_count": 0, "per_page": "invalid"},
        )

    api = TogglApi("secret-token", transport=MockTransport(bad_metadata))
    try:
        with pytest.raises(ApiError, match="invalid detailed report page"):
            api.get_detailed_entries(
                42,
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8, 12, tzinfo=ZoneInfo("UTC")),
            )
    finally:
        api.close()


def test_debug_diagnostics_include_safe_v2_pagination() -> None:
    messages: list[str] = []

    def handler(request: MockRequest) -> MockResponse:
        return MockResponse(
            200,
            json={"data": [], "total_count": 0, "per_page": 50},
        )

    api = TogglApi("secret-token", transport=MockTransport(handler), diagnostics=messages.append)
    try:
        api.get_detailed_entries(
            42,
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8, 12, tzinfo=ZoneInfo("UTC")),
        )
    finally:
        api.close()

    assert any("pagination page=1" in message for message in messages)


def test_review_adapter_loads_only_active_workspace_projects_and_puts_one_entry() -> None:
    requests: list[MockRequest] = []

    def handler(request: MockRequest) -> MockResponse:
        requests.append(request)
        if request.url.path == "/api/v9/me/projects":
            assert request.url.params["include_archived"] == "false"
            return MockResponse(
                200,
                json={
                    "items": [
                        {"id": 1, "name": "Active", "workspace_id": 42, "active": True},
                        {"id": 2, "name": "Archived", "workspace_id": 42, "active": False},
                        {"id": 3, "name": "Other", "workspace_id": 99, "active": True},
                    ]
                },
            )
        assert request.method == "PUT"
        assert request.url.path.endswith("/time_entries/10")
        assert json.loads(request.content) == {
            "description": "ABC-1",
            "tags": ["keep", "doing"],
        }
        return MockResponse(
            200,
            headers={
                "X-Toggl-Quota-Remaining": "7",
                "X-Toggl-Quota-Resets-In": "60",
            },
            json={"id": 10},
        )

    api = TogglApi(
        "secret-token",
        transport=MockTransport(handler),
        writes_qualified=True,
    )
    try:
        projects = api.get_active_projects(42)
        quota = api.put_time_entry(42, 10, {"description": "ABC-1", "tags": ["keep", "doing"]})
    finally:
        api.close()

    assert [(project.id, project.name) for project in projects] == [(1, "Active")]
    assert [request.url.path for request in requests] == [
        "/api/v9/me/projects",
        "/api/v9/workspaces/42/time_entries/10",
    ]
    assert quota == Quota(7, 60)


def test_put_time_entry_is_disabled_until_manual_write_qualification() -> None:
    calls: list[MockRequest] = []

    def handler(request: MockRequest) -> MockResponse:
        calls.append(request)
        return MockResponse(500)

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="Live writes are disabled"):
            api.put_time_entry(42, 10, {"description": "new"})
    finally:
        api.close()

    assert calls == []


def test_quota_adapter_uses_lowest_remaining_window() -> None:
    def handler(request: MockRequest) -> MockResponse:
        assert request.url.path == "/api/v9/me/quota"
        return MockResponse(
            200,
            json={
                "items": [
                    {"remaining": 12, "resets_in_secs": 30},
                    {"remaining": 8, "resets_in_secs": 45},
                ]
            },
        )

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        quota = api.get_quota()
    finally:
        api.close()

    assert quota.remaining == 8
    assert quota.resets_in_seconds == 45


def test_quota_adapter_accepts_top_level_list_response() -> None:
    def handler(request: MockRequest) -> MockResponse:
        assert request.url.path == "/api/v9/me/quota"
        return MockResponse(
            200,
            json=[
                {"remaining": 12, "resets_in_secs": 30},
                {"remaining": 8, "resets_in_secs": 45},
            ],
        )

    api = TogglApi("secret-token", transport=MockTransport(handler))
    try:
        quota = api.get_quota()
    finally:
        api.close()

    assert quota == Quota(8, 45)
