"""Pure report models and calculations.

Nothing in this module knows about HTTP, configuration, or terminal output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

MARKER_TAG = "marker"
UNKNOWN_GROUP = "Unknown Client/Project"
OVERLAP_WARNING_THRESHOLD = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ReportingPeriod:
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class TimeEntry:
    id: int
    description: str
    start: datetime
    stop: datetime | None
    duration_ms: int
    tags: tuple[str, ...] = ()
    tag_ids: tuple[int, ...] = ()
    client_name: str | None = None
    project_name: str | None = None
    project_id: int | None = None
    task_id: int | None = None
    task_name: str | None = None
    user_id: int | None = None
    username: str | None = None

    @property
    def is_marker(self) -> bool:
        return MARKER_TAG in self.tags


@dataclass(frozen=True, slots=True)
class TimeSummary:
    time_count_ms: int
    break_ms: int
    booked_ms: int
    unbooked_ms: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryGroup:
    name: str
    booked_ms: int
    percentage: float
    children: tuple[SummaryGroup, ...] = ()


def format_duration(duration_ms: int) -> str:
    """Format a non-negative duration as an unbounded ``HH:mm:ss`` value."""

    total_seconds = max(0, duration_ms) // 1000
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def calculate_time_totals(
    entries: tuple[TimeEntry, ...] | list[TimeEntry],
    report_now: datetime,
    reporting_timezone: tzinfo,
) -> TimeSummary:
    """Calculate booked, break, and same-day unbooked time.

    Entries are copied and sorted, so callers do not observe an in-place mutation.
    A marker excludes its own duration from booked time and keeps the break open
    until the next non-marker on the same reporting day.
    """

    ordered = sorted(entries, key=lambda entry: (entry.start.astimezone(UTC), entry.id))
    booked_ms = break_ms = unbooked_ms = 0
    warnings: list[str] = []

    def effective_stop(entry: TimeEntry) -> datetime:
        return entry.stop if entry.stop is not None else report_now

    for index, entry in enumerate(ordered):
        if entry.stop is None:
            warnings.append(f"Running entry {entry.id} measured at report time.")
        if not entry.is_marker:
            booked_ms += entry.duration_ms
        if index == 0:
            continue

        previous = ordered[index - 1]
        previous_stop = effective_stop(previous)
        gap = entry.start.astimezone(UTC) - previous_stop.astimezone(UTC)
        if gap < timedelta():
            overlap = previous_stop.astimezone(UTC) - entry.start.astimezone(UTC)
            if overlap > OVERLAP_WARNING_THRESHOLD:
                warnings.append(
                    f"Overlap of {format_duration(int(overlap.total_seconds() * 1000))} "
                    f"between entries {previous.id} and {entry.id}."
                )
            gap = timedelta()

        previous_day = previous_stop.astimezone(reporting_timezone).date()
        current_day = entry.start.astimezone(reporting_timezone).date()
        if previous.is_marker and previous_day == current_day:
            break_ms += int(gap.total_seconds() * 1000)
        elif previous_day == current_day:
            unbooked_ms += int(gap.total_seconds() * 1000)

    return TimeSummary(
        time_count_ms=booked_ms + unbooked_ms,
        break_ms=break_ms,
        booked_ms=booked_ms,
        unbooked_ms=unbooked_ms,
        warnings=tuple(warnings),
    )


def _group_name(value: str | None) -> str:
    return value or UNKNOWN_GROUP


def _sorted_groups(groups: dict[str, int], denominator: int) -> tuple[SummaryGroup, ...]:
    result: list[SummaryGroup] = []
    for name, booked_ms in sorted(groups.items(), key=lambda item: (-item[1], item[0])):
        result.append(
            SummaryGroup(
                name=name,
                booked_ms=booked_ms,
                percentage=(100 * booked_ms / denominator if denominator > 0 else 0),
            )
        )
    return tuple(result)


def calculate_summary(
    entries: tuple[TimeEntry, ...] | list[TimeEntry], total: TimeSummary
) -> tuple[SummaryGroup, ...]:
    """Group non-marker booked entries by client and project."""

    client_projects: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for entry in entries:
        if not entry.is_marker:
            client_projects[_group_name(entry.client_name)][_group_name(entry.project_name)] += (
                entry.duration_ms
            )

    groups: list[SummaryGroup] = []
    for client, projects in client_projects.items():
        client_total = sum(projects.values())
        children = _sorted_groups(projects, client_total)
        groups.append(
            SummaryGroup(
                name=client,
                booked_ms=client_total,
                percentage=(
                    100 * client_total / total.time_count_ms if total.time_count_ms > 0 else 0
                ),
                children=children,
            )
        )

    groups.append(
        SummaryGroup(
            name="Unbooked Time",
            booked_ms=total.unbooked_ms,
            percentage=(
                100 * total.unbooked_ms / total.time_count_ms if total.time_count_ms > 0 else 0
            ),
        )
    )
    return tuple(sorted(groups, key=lambda group: (-group.booked_ms, group.name)))
