import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from toggl_cli.api import ApiError, TogglApi

FIXTURE = Path(__file__).parents[1] / "fixtures" / "reports_v2_page.json"


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
            assert request.url.params["since"] == "2026-08-07"
            assert request.url.params["until"] == "2026-08-07"
            assert request.url.params["user_agent"] == "toggl-cli"
            return httpx.Response(200, json=json.loads(FIXTURE.read_text()))
        raise AssertionError(f"unexpected request path: {request.url.path}")

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
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
    assert requests[1].headers["Authorization"].startswith("Basic ")


def test_v2_adapter_follows_page_number_pagination() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/reports/api/v2/details":
            return httpx.Response(200, json=[])
        page = request.url.params["page"]
        hour = 8 + int(page)
        requests.append(page)
        entry = {
            "id": int(page),
            "start": f"2026-08-08T{hour:02d}:00:00+00:00",
            "end": None if page == "2" else f"2026-08-08T{hour:02d}:30:00+00:00",
            "dur": -1 if page == "2" else 1_800_000,
        }
        return httpx.Response(
            200,
            json={"data": [entry], "total_count": 2, "per_page": 1},
        )

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
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


def test_review_adapter_loads_only_active_workspace_projects_and_bulk_patches() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v9/me/projects":
            assert request.url.params["include_archived"] == "false"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 1, "name": "Active", "workspace_id": 42, "active": True},
                        {"id": 2, "name": "Archived", "workspace_id": 42, "active": False},
                        {"id": 3, "name": "Other", "workspace_id": 99, "active": True},
                    ]
                },
            )
        assert request.method == "PATCH"
        assert request.url.path.endswith("/time_entries/10,11")
        assert json.loads(request.content) == [
            {"op": "replace", "path": "/description", "value": "ABC-1"}
        ]
        return httpx.Response(200, json={"success": [10], "failure": [{"id": 11, "message": "no"}]})

    api = TogglApi(
        "secret-token",
        transport=httpx.MockTransport(handler),
        writes_qualified=True,
    )
    try:
        projects = api.get_active_projects(42)
        result = api.bulk_patch(
            42,
            (10, 11),
            ({"op": "replace", "path": "/description", "value": "ABC-1"},),
        )
    finally:
        api.close()

    assert [(project.id, project.name) for project in projects] == [(1, "Active")]
    assert result.success == (10,)
    assert result.failures == ((11, "no"),)


def test_bulk_patch_rejects_incomplete_per_id_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": [10], "failure": []})

    api = TogglApi(
        "secret-token",
        transport=httpx.MockTransport(handler),
        writes_qualified=True,
    )
    try:
        with pytest.raises(ApiError, match="invalid bulk patch response"):
            api.bulk_patch(42, (10, 11), ())
    finally:
        api.close()


def test_bulk_patch_is_disabled_until_manual_write_qualification() -> None:
    api = TogglApi("secret-token", transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    try:
        with pytest.raises(ApiError, match="Live writes are disabled"):
            api.bulk_patch(42, (10,), ())
    finally:
        api.close()


def test_quota_adapter_uses_lowest_remaining_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v9/me/quota"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"remaining": 12, "resets_in_secs": 30},
                    {"remaining": 8, "resets_in_secs": 45},
                ]
            },
        )

    api = TogglApi("secret-token", transport=httpx.MockTransport(handler))
    try:
        quota = api.get_quota()
    finally:
        api.close()

    assert quota.remaining == 8
    assert quota.resets_in_seconds == 45
