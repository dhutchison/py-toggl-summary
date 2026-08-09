import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from toggl_cli.api import ApiError, TogglApi

FIXTURE = Path(__file__).parents[1] / "fixtures" / "reports_v2_page.json"


def test_profile_and_detailed_adapter_follow_v2_pages_and_map_entries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v9/me":
            return httpx.Response(
                200,
                json={
                    "timezone": "Europe/London",
                    "beginning_of_week": 1,
                    "default_workspace_id": 42,
                },
            )
        if request.url.path == "/reports/api/v2/details":
            assert request.url.params["workspace_id"] == "42"
            assert request.url.params["since"] == "2026-08-08"
            assert request.url.params["until"] == "2026-08-08"
            assert request.url.params["user_agent"] == "toggl-cli"
            if request.url.params["page"] == "2":
                return httpx.Response(
                    200,
                    json={
                        "total_grand": 7_200_000,
                        "total_count": 2,
                        "per_page": 1,
                        "data": [
                            {
                                "id": 2,
                                "description": "Running work",
                                "start": "2026-08-08T10:00:00+01:00",
                                "end": None,
                                "dur": -1,
                                "tags": ["active"],
                                "pid": 11,
                                "project": "Project",
                                "client": "Client",
                                "tid": 21,
                                "task": "Task",
                                "uid": 31,
                                "user": "Person",
                                "updated": "2026-08-08T10:00:00+01:00",
                                "use_stop": False,
                            }
                        ],
                    },
                )
            return httpx.Response(200, json=json.loads(FIXTURE.read_text()))
        raise AssertionError(f"unexpected request path: {request.url.path}")

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
    try:
        profile = api.get_profile()
        entries = api.get_detailed_entries(
            42,
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8).date(),
            datetime(2026, 8, 8, 12, tzinfo=profile.timezone),
        )
    finally:
        api.close()

    assert profile.default_workspace_id == 42
    assert [item.id for item in entries] == [1, 2]
    assert entries[0].duration_ms == 3_599_000
    assert entries[1].duration_ms == 2 * 60 * 60 * 1000
    assert entries[0].client_name == "Client"
    assert entries[0].project_id == 11
    assert entries[0].tags == ("focus",)
    assert requests[1].headers["Authorization"].startswith("Basic ")


def test_api_error_is_classified_without_exposing_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-Toggl-Quota-Remaining": "29"},
            json={"error": "workspace denied"},
        )

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="HTTP 403") as raised:
            api.get_profile()
    finally:
        api.close()

    assert "secret-token" not in str(raised.value)
    assert raised.value.status_code == 403
    assert raised.value.headers == {"x-toggl-quota-remaining": "29"}


def test_invalid_v2_report_page_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "not-a-page"})

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"timezone": "Not/AZone", "beginning_of_week": 1})

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="unknown timezone"):
            api.get_profile()
    finally:
        api.close()


def test_invalid_v2_pagination_metadata_is_rejected() -> None:
    def bad_metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [], "total_count": 0, "per_page": "invalid"},
        )

    api = TogglApi("secret-token", transport=httpx.MockTransport(bad_metadata))
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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [], "total_count": 0, "per_page": 50},
        )

    api = TogglApi(
        "secret-token", transport=httpx.MockTransport(handler), diagnostics=messages.append
    )
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
