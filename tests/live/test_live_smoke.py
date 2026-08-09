import os
from datetime import date

import pytest

from toggl_cli.api import TogglApi
from toggl_cli.app import ReportService
from toggl_cli.config import default_config_path, load_settings, load_token

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("TOGGL_CLI_LIVE_SMOKE") != "1",
        reason="set TOGGL_CLI_LIVE_SMOKE=1 to enable live smoke tests",
    ),
]


def test_read_only_reports_v2_smoke() -> None:
    day_text = os.environ.get("TOGGL_CLI_LIVE_DAY")
    if not day_text:
        pytest.fail("TOGGL_CLI_LIVE_DAY must be set to a narrow YYYY-MM-DD date")
    try:
        day = date.fromisoformat(day_text)
    except ValueError as error:
        pytest.fail("TOGGL_CLI_LIVE_DAY must be a YYYY-MM-DD date", pytrace=False)
        raise AssertionError from error

    token = load_token()
    if not token:
        pytest.fail(f"No API token is available in the configured keyring: {default_config_path()}")

    settings = load_settings()
    api = TogglApi(token)
    try:
        period, total, _ = ReportService(api, settings).run(day, False, False)
    finally:
        api.close()

    assert period.start == day
    assert period.end == day
    assert total.booked_ms >= 0
    assert total.unbooked_ms >= 0
    assert total.break_ms >= 0
