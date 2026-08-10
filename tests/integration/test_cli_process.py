import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]


def test_module_help_is_plain_text_in_a_non_tty_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "toggl_cli", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "GITHUB_ACTIONS": "true"},
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: " in result.stdout
    assert "report" in result.stdout
    assert "\x1b[" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Python's pty module is Unix-only")
def test_module_help_uses_rich_format_in_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    import pty

    for variable in ("CI", "GITHUB_ACTIONS", "FORCE_COLOR", "NO_COLOR"):
        monkeypatch.delenv(variable, raising=False)

    output = bytearray()

    def read_output(file_descriptor: int) -> bytes:
        data = os.read(file_descriptor, 4096)
        output.extend(data)
        return data

    spawn = pty.__dict__["spawn"]
    status = spawn([sys.executable, "-m", "toggl_cli", "--help"], read_output)
    help_text = output.decode()

    assert os.waitstatus_to_exitcode(status) == 0
    assert "Usage: " in help_text
    assert "\x1b[" in help_text


def test_invalid_report_day_has_stable_process_diagnostics() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "toggl_cli", "report", "--day", "not-a-date"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Error: Invalid isoformat string: 'not-a-date'\n"


def test_review_requires_a_tty_before_reading_credentials() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "toggl_cli", "review", "--day", "2026-08-08"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Error: review requires an interactive terminal.\n"


@pytest.mark.skipif(os.name == "nt", reason="Python's pty module is Unix-only")
def test_invalid_report_day_has_plain_diagnostics_in_a_tty() -> None:
    import pty

    output = bytearray()

    def read_output(file_descriptor: int) -> bytes:
        data = os.read(file_descriptor, 4096)
        output.extend(data)
        return data

    spawn = pty.__dict__["spawn"]
    status = spawn(
        [sys.executable, "-m", "toggl_cli", "report", "--day", "not-a-date"],
        read_output,
    )

    assert os.waitstatus_to_exitcode(status) == 2
    assert output.decode().replace("\r\n", "\n") == (
        "Error: Invalid isoformat string: 'not-a-date'\n"
    )
