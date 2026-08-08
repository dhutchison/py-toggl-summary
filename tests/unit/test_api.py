import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from toggl_cli.api import ApiError, TogglApi

FIXTURE = Path(__file__).parents[1] / "fixtures" / "reports_v3_page.json"


def test_profile_and_detailed_adapter_follow_cursor_and_deduplicate() -> None:
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
        body = request.read().decode()
        if '"first_row_number":50' in body:
            return httpx.Response(
                200,
                json=[
                    {
                        "client_name": "Client",
                        "project_name": "Project",
                        "time_entries": [
                            {
                                "id": 1,
                                "start": "2026-08-08T09:00:00+01:00",
                                "stop": "2026-08-08T10:00:00+01:00",
                                "seconds": 3599,
                            },
                            {
                                "id": 2,
                                "start": "2026-08-08T10:00:00+01:00",
                                "stop": None,
                                "seconds": -1,
                            },
                        ],
                    }
                ],
            )
        return httpx.Response(
            200,
            headers={"X-Next-Row-Number": "50"},
            json=json.loads(FIXTURE.read_text()),
        )

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


def test_repeated_pagination_cursor_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"X-Next-Row-Number": "50"}, json=[])

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError, match="repeated pagination"):
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


def test_invalid_report_page_and_cursor_are_rejected() -> None:
    def bad_page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "not-a-page"})

    api = TogglApi("secret-token", transport=httpx.MockTransport(bad_page))
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

    def bad_cursor(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"X-Next-Row-Number": "invalid"}, json=[])

    api = TogglApi("secret-token", transport=httpx.MockTransport(bad_cursor))
    try:
        with pytest.raises(ApiError, match="invalid pagination cursor"):
            api.get_detailed_entries(
                42,
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8).date(),
                datetime(2026, 8, 8, 12, tzinfo=ZoneInfo("UTC")),
            )
    finally:
        api.close()


def test_debug_diagnostics_include_safe_pagination_headers() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Next-ID": "9002", "X-Range-Start": "start"},
            json=[],
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

    assert any("x-next-id" in message for message in messages)
