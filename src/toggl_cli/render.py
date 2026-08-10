"""Stable, plain-text report rendering."""

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
