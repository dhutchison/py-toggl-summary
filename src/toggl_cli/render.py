"""Report rendering in Markdown, terminal tables, and JSON."""

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from .domain import (
    ActivityTypeSummary,
    ReportingPeriod,
    SummaryGroup,
    TimeSummary,
    format_duration,
)


def _render_group(group: SummaryGroup, indent: str = "") -> list[str]:
    lines = [
        f"{indent}* {group.name}: {group.percentage:.2f}% ({format_duration(group.booked_ms)})"
    ]
    for child in group.children:
        lines.extend(_render_group(child, f"{indent}  "))
    return lines


def render_report(
    period: ReportingPeriod,
    total: TimeSummary,
    include_summary: bool,
    summary: tuple[SummaryGroup, ...] = (),
    activity_summary: tuple[ActivityTypeSummary, ...] | None = None,
) -> str:
    lines = [
        f"# Totals for {period.start.isoformat()} to {period.end.isoformat()}",
        "",
        f"* Booked time: {format_duration(total.booked_ms)}",
        f"* Unbooked time: {format_duration(total.unbooked_ms)}",
        f"* Break time: {format_duration(total.break_ms)}",
        f"* Total time (booked + unbooked): {format_duration(total.time_count_ms)}",
    ]
    if include_summary:
        lines.extend(["", "# Summary", ""])
        for group in summary:
            lines.extend(_render_group(group))
            lines.append("")
        if activity_summary is not None:
            lines.extend(["# Activity Type Summary", ""])
            for activity in activity_summary:
                lines.append(
                    f"* {activity.name}: {activity.percentage:.2f}% "
                    f"({format_duration(activity.booked_ms)})"
                )
            lines.append("")
    return "\n".join(lines) + "\n"


def _duration_data(duration_ms: int) -> dict[str, int | str]:
    return {
        "milliseconds": duration_ms,
        "human_readable": format_duration(duration_ms),
    }


def _group_data(group: SummaryGroup) -> dict[str, Any]:
    return {
        "name": group.name,
        "percentage": group.percentage,
        "duration": _duration_data(group.booked_ms),
        "projects": [_group_data(child) for child in group.children],
    }


def render_report_json(
    period: ReportingPeriod,
    total: TimeSummary,
    include_summary: bool,
    summary: tuple[SummaryGroup, ...] = (),
    activity_summary: tuple[ActivityTypeSummary, ...] | None = None,
) -> str:
    """Render a structured report with raw and display-ready durations."""

    report: dict[str, Any] = {
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat()},
        "totals": {
            "booked": _duration_data(total.booked_ms),
            "unbooked": _duration_data(total.unbooked_ms),
            "break": _duration_data(total.break_ms),
            "total": _duration_data(total.time_count_ms),
        },
        "warnings": list(total.warnings),
        "summary": None,
    }
    if include_summary:
        report["summary"] = {
            "client_project": [_group_data(group) for group in summary],
            "activity_types": [
                {
                    "name": activity.name,
                    "percentage": activity.percentage,
                    "duration": _duration_data(activity.booked_ms),
                }
                for activity in (activity_summary or ())
            ],
        }
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def render_pretty_report(
    console: Console,
    period: ReportingPeriod,
    total: TimeSummary,
    include_summary: bool,
    summary: tuple[SummaryGroup, ...] = (),
    activity_summary: tuple[ActivityTypeSummary, ...] | None = None,
) -> None:
    """Print a readable terminal report using tables."""

    console.print(
        f"Totals for {period.start.isoformat()} to {period.end.isoformat()}", style="bold"
    )
    totals = Table(show_header=False, box=None, pad_edge=False)
    totals.add_column("Measure")
    totals.add_column("Duration", justify="right", style="cyan")
    totals.add_row("Booked time", format_duration(total.booked_ms))
    totals.add_row("Unbooked time", format_duration(total.unbooked_ms))
    totals.add_row("Break time", format_duration(total.break_ms))
    totals.add_row("Total time (booked + unbooked)", format_duration(total.time_count_ms))
    console.print(totals)

    if not include_summary:
        return

    console.print("\nClient / Project Summary", style="bold")
    groups = Table()
    groups.add_column("Client / Project")
    groups.add_column("%", justify="right")
    groups.add_column("Duration", justify="right")
    for group in summary:
        groups.add_row(group.name, f"{group.percentage:.2f}%", format_duration(group.booked_ms))
        for child in group.children:
            groups.add_row(
                f"  {child.name}",
                f"{child.percentage:.2f}%",
                format_duration(child.booked_ms),
            )
    console.print(groups)

    if activity_summary is not None:
        console.print("\nActivity Type Summary", style="bold")
        activities = Table()
        activities.add_column("Activity Type")
        activities.add_column("% of booked", justify="right")
        activities.add_column("Duration", justify="right")
        for activity in activity_summary:
            activities.add_row(
                activity.name,
                f"{activity.percentage:.2f}%",
                format_duration(activity.booked_ms),
            )
        console.print(activities)
