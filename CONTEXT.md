# Toggl Summary

This context describes the language used to reason about work-time reporting and compatibility with the original Toggl Summary CLI.

## Language

**Behavioural parity**:
The compatibility target in which the Python port preserves the original CLI's intentional, user-observable capabilities and results without preserving obsolete mechanisms or accidental defects.
_Avoid_: Literal port, implementation parity

**Report**:
A read-only account of booked, unbooked, break, and total time for a selected day or week, optionally grouped by client and project.
_Avoid_: Timesheet, audit

**Activity type**:
The purpose of a work entry, expressed by exactly one member of a configured closed taxonomy. The default taxonomy is `reviewing`, `supporting`, `doing`, and `meeting`. Type matching is case-insensitive, while the configured spelling is canonical. Communication channels such as email and messaging do not form a separate activity type; the work is classified by its purpose.
_Avoid_: Work type, miscellaneous

**Reporting day**:
A calendar day interpreted in the authenticated Toggl user's configured timezone.
_Avoid_: Local day, UTC day

**Reporting week**:
The Toggl-defined calendar week containing a selected reporting day, using the authenticated user's configured first day of the week.
_Avoid_: Seven-day range

**Marker**:
A structural time entry tagged `marker` that opens a break at its end; additional markers leave the same break open until work resumes. Markers are not work entries and are excluded from project, activity-type, and Jira-reference quality rules and breakdowns.
_Avoid_: Break entry

**Break**:
The interval from the end of a marker to the start of the next non-marker entry on the same reporting day. An open break does not carry into another reporting day.
_Avoid_: Unbooked time, marker

**Overlap**:
The period for which two time entries cover the same instant. It contributes no negative gap; overlaps greater than five minutes are considered noteworthy.
_Avoid_: Negative unbooked time

**Running entry**:
A time entry without a stop whose duration is measured at the single instant when report generation begins.
_Avoid_: Incomplete entry

**Unbooked time**:
A gap between successive entries on the same reporting day that is not part of a break. Time before the first entry, after the last entry, and between reporting days is outside the calculation.
_Avoid_: Break, idle time
