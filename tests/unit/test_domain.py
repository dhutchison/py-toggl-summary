import json
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from rich.console import Console

from toggl_cli.domain import (
    ActivityTypeSummary,
    ReportingPeriod,
    TimeEntry,
    calculate_activity_summary,
    calculate_summary,
    calculate_time_totals,
    format_duration,
)
from toggl_cli.render import render_pretty_report, render_report, render_report_json

UTC = ZoneInfo("UTC")
REPORT_NOW = datetime(2026, 8, 8, 17, 0, tzinfo=UTC)


def entry(
    identifier: int,
    start: str,
    stop: str | None,
    duration_ms: int,
    *,
    tags: tuple[str, ...] = (),
    client: str | None = "Client",
    project: str | None = "Project",
) -> TimeEntry:
    return TimeEntry(
        id=identifier,
        description=f"entry {identifier}",
        start=datetime.fromisoformat(start),
        stop=datetime.fromisoformat(stop) if stop else None,
        duration_ms=duration_ms,
        tags=tags,
        client_name=client,
        project_name=project,
    )


def test_marker_state_stays_open_across_consecutive_markers() -> None:
    entries = [
        entry(
            1,
            "2026-08-08T09:00:00+00:00",
            "2026-08-08T09:05:00+00:00",
            5 * 60_000,
            tags=("marker",),
        ),
        entry(
            2, "2026-08-08T10:00:00+00:00", "2026-08-08T10:01:00+00:00", 60_000, tags=("marker",)
        ),
        entry(3, "2026-08-08T11:00:00+00:00", "2026-08-08T12:00:00+00:00", 60 * 60_000),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, UTC)

    assert result.booked_ms == 60 * 60_000
    assert result.break_ms == 114 * 60_000
    assert result.unbooked_ms == 0


def test_overlaps_are_clamped_and_long_overlaps_warn() -> None:
    entries = [
        entry(1, "2026-08-08T09:00:00+00:00", "2026-08-08T10:00:00+00:00", 60 * 60_000),
        entry(2, "2026-08-08T09:50:00+00:00", "2026-08-08T10:30:00+00:00", 40 * 60_000),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, UTC)

    assert result.unbooked_ms == 0
    assert result.time_count_ms == result.booked_ms
    assert "Overlap" in result.warnings[0]


def test_cross_day_gaps_are_not_counted() -> None:
    entries = [
        entry(1, "2026-08-08T23:00:00+00:00", "2026-08-08T23:30:00+00:00", 30 * 60_000),
        entry(2, "2026-08-09T09:00:00+00:00", "2026-08-09T10:00:00+00:00", 60 * 60_000),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, UTC)

    assert result.unbooked_ms == 0
    assert result.time_count_ms == 90 * 60_000


def test_spring_forward_gap_uses_elapsed_time() -> None:
    timezone = ZoneInfo("Europe/London")
    entries = [
        TimeEntry(
            1,
            "entry 1",
            datetime(2026, 3, 29, 0, tzinfo=timezone),
            datetime(2026, 3, 29, 0, 30, tzinfo=timezone),
            30 * 60_000,
        ),
        TimeEntry(
            2,
            "entry 2",
            datetime(2026, 3, 29, 2, 30, tzinfo=timezone),
            datetime(2026, 3, 29, 3, tzinfo=timezone),
            30 * 60_000,
        ),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, timezone)

    assert result.unbooked_ms == 60 * 60_000


def test_fall_back_gap_uses_elapsed_time() -> None:
    timezone = ZoneInfo("Europe/London")
    entries = [
        TimeEntry(
            1,
            "entry 1",
            datetime(2026, 10, 25, 0, tzinfo=timezone),
            datetime(2026, 10, 25, 0, 30, tzinfo=timezone),
            30 * 60_000,
        ),
        TimeEntry(
            2,
            "entry 2",
            datetime(2026, 10, 25, 2, 30, tzinfo=timezone),
            datetime(2026, 10, 25, 3, tzinfo=timezone),
            30 * 60_000,
        ),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, timezone)

    assert result.unbooked_ms == 3 * 60 * 60_000


def test_fall_back_overlap_warning_uses_elapsed_time() -> None:
    timezone = ZoneInfo("Europe/London")
    entries = [
        TimeEntry(
            1,
            "entry 1",
            datetime(2026, 10, 25, 0, tzinfo=timezone),
            datetime(2026, 10, 25, 1, 30, tzinfo=timezone, fold=1),
            90 * 60_000,
        ),
        TimeEntry(
            2,
            "entry 2",
            datetime(2026, 10, 25, 1, 15, tzinfo=timezone),
            datetime(2026, 10, 25, 2, tzinfo=timezone),
            45 * 60_000,
        ),
    ]

    result = calculate_time_totals(entries, REPORT_NOW, timezone)

    assert "Overlap of 01:15:00" in result.warnings[0]


def test_summary_uses_booked_entries_and_has_stable_rendering() -> None:
    entries = [
        entry(
            1,
            "2026-08-08T09:00:00+00:00",
            "2026-08-08T10:00:00+00:00",
            60 * 60_000,
            project="Alpha",
        ),
        entry(
            2, "2026-08-08T10:30:00+00:00", "2026-08-08T11:00:00+00:00", 30 * 60_000, project="Beta"
        ),
    ]
    total = calculate_time_totals(entries, REPORT_NOW, UTC)
    summary = calculate_summary(entries, total)

    assert [group.name for group in summary] == ["Client", "Unbooked Time"]
    assert [child.name for child in summary[0].children] == ["Alpha", "Beta"]
    rendered_summary = render_report(
        ReportingPeriod(datetime(2026, 8, 8).date(), datetime(2026, 8, 8).date()),
        total,
        True,
        summary,
    )
    assert "# Summary" in rendered_summary
    assert "  * Alpha:" in rendered_summary
    assert rendered_summary.endswith("\n\n")
    assert render_report(
        ReportingPeriod(datetime(2026, 8, 8).date(), datetime(2026, 8, 8).date()), total, False
    ) == (
        "# Totals for 2026-08-08 to 2026-08-08\n\n"
        "* Booked time: 01:30:00\n"
        "* Unbooked time: 00:30:00\n"
        "* Break time: 00:00:00\n"
        "* Total time (booked + unbooked): 02:00:00\n"
    )


def test_activity_summary_rendering_is_separate_from_client_summary() -> None:
    total = calculate_time_totals(
        [entry(1, "2026-08-08T09:00:00+00:00", "2026-08-08T10:00:00+00:00", 60 * 60_000)],
        REPORT_NOW,
        UTC,
    )
    activity = calculate_activity_summary([], total, ("doing", "meeting"))

    rendered = render_report(
        ReportingPeriod(datetime(2026, 8, 8).date(), datetime(2026, 8, 8).date()),
        total,
        True,
        (),
        activity,
    )

    assert "# Activity Type Summary" in rendered
    assert "* doing: 0.00% (00:00:00)" in rendered
    assert "* meeting: 0.00% (00:00:00)" in rendered
    assert "* Unclassified: 0.00% (00:00:00)" in rendered
    assert "* Conflicting: 0.00% (00:00:00)" in rendered


def test_format_duration_supports_more_than_a_day() -> None:
    assert format_duration(100 * 60 * 60 * 1000 + 2 * 60 * 1000 + 3 * 1000) == "100:02:03"


def test_json_report_has_millisecond_and_human_readable_durations_for_totals_and_summaries() -> (
    None
):
    entries = [
        entry(1, "2026-08-08T09:00:00+00:00", "2026-08-08T10:00:00+00:00", 60 * 60_000),
        entry(2, "2026-08-08T11:00:00+00:00", "2026-08-08T11:30:00+00:00", 30 * 60_000),
    ]
    total = calculate_time_totals(entries, REPORT_NOW, UTC)
    summary = calculate_summary(entries, total)
    activities = calculate_activity_summary(entries, total)

    result = json.loads(
        render_report_json(
            ReportingPeriod(datetime(2026, 8, 8).date(), datetime(2026, 8, 8).date()),
            total,
            True,
            summary,
            activities,
        )
    )

    assert result["period"] == {"start": "2026-08-08", "end": "2026-08-08"}
    assert result["totals"]["booked"] == {
        "milliseconds": 90 * 60_000,
        "human_readable": "01:30:00",
    }
    assert result["totals"]["unbooked"] == {
        "milliseconds": 60 * 60_000,
        "human_readable": "01:00:00",
    }
    client_group = result["summary"]["client_project"][0]
    assert client_group["duration"] == {
        "milliseconds": 90 * 60_000,
        "human_readable": "01:30:00",
    }
    assert client_group["projects"][0]["duration"]["human_readable"] == "01:30:00"
    assert result["summary"]["activity_types"][0]["duration"]["milliseconds"] == 0


def test_pretty_report_renders_totals_and_both_optional_summaries() -> None:
    entries = [
        entry(1, "2026-08-08T09:00:00+00:00", "2026-08-08T10:00:00+00:00", 60 * 60_000),
    ]
    total = calculate_time_totals(entries, REPORT_NOW, UTC)
    summary = calculate_summary(entries, total)
    activities = calculate_activity_summary(entries, total)
    output = StringIO()
    console = Console(file=output, width=100, force_terminal=False, color_system=None)

    render_pretty_report(
        console,
        ReportingPeriod(datetime(2026, 8, 8).date(), datetime(2026, 8, 8).date()),
        total,
        True,
        summary,
        activities,
    )

    rendered = output.getvalue()
    assert "Totals for 2026-08-08 to 2026-08-08" in rendered
    assert "Booked time" in rendered and "01:00:00" in rendered
    assert "Client / Project Summary" in rendered
    assert "Activity Type Summary" in rendered
    assert "Unclassified" in rendered


def test_activity_summary_accounts_for_configured_unclassified_and_conflicting() -> None:
    entries = [
        entry(
            1,
            "2026-08-08T09:00:00+00:00",
            "2026-08-08T10:00:00+00:00",
            60 * 60_000,
            tags=("DOING", "email"),
        ),
        entry(
            2,
            "2026-08-08T10:00:00+00:00",
            "2026-08-08T10:30:00+00:00",
            30 * 60_000,
            tags=("supporting", "Doing"),
        ),
        entry(
            3,
            "2026-08-08T10:30:00+00:00",
            "2026-08-08T11:00:00+00:00",
            30 * 60_000,
            tags=("email",),
        ),
        entry(
            4,
            "2026-08-08T11:00:00+00:00",
            "2026-08-08T11:05:00+00:00",
            5 * 60_000,
            tags=("marker", "doing"),
        ),
    ]
    total = calculate_time_totals(entries, REPORT_NOW, UTC)

    result = calculate_activity_summary(entries, total, ("Doing", "Supporting"))

    assert result == (
        ActivityTypeSummary("Doing", 60 * 60_000, 50.0),
        ActivityTypeSummary("Supporting", 0, 0.0),
        ActivityTypeSummary("Unclassified", 30 * 60_000, 25.0),
        ActivityTypeSummary("Conflicting", 30 * 60_000, 25.0),
    )
