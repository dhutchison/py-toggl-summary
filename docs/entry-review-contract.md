# Entry review contract

The review command is there to help correct entries that do not meet the
configured quality rules. It is not a general-purpose time-entry editor. This
page records what it will review, what it may change, and what happens when a
write is attempted.

## What gets reviewed

The command requires an interactive terminal. It uses the selected reporting
day or the Toggl-defined week containing that day, interpreted in the
authenticated user's timezone, and takes one report snapshot before
prompting. It considers only non-marker entries owned by the authenticated
Toggl user, in chronological order. Entries that already meet every enabled
rule are left alone. A running entry can be reviewed, but the command does not
stop it or change its timer state.

The activity taxonomy comes from `review.activity_types` in `config.json`. If
it is absent, the defaults are `reviewing`, `supporting`, `doing`, and
`meeting`. Configured types replace that list, keep their spelling and order,
and match tags without regard to case. An entry needs exactly one configured
type: none is missing, and more than one distinct type is conflicting.
Repeated case variants of the same type count as one match. Other tags do not
affect classification.

A missing project means the entry has no project ID. It can be corrected only
by choosing an active project in the selected workspace; review does not create
or reactivate projects.

Jira-reference checking is optional. It applies only to activity types listed
in `review.jira_activity_types`; an absent or empty list disables the check. A
Jira-shaped key in the description satisfies the rule. The command does not
contact Jira to check whether the key exists, and a missing key can be skipped.

Marker entries are structural, not work entries. They are excluded from
quality checks and activity summaries, even if they also carry other tags.

## What the prompts do

For each entry needing attention, review asks about activity type, project, and
then Jira reference, where those checks apply. Choices accept an exact,
case-insensitive name or an unambiguous prefix. An ambiguous prefix narrows the
choices rather than selecting one. Enter `s` to skip the current issue or `c`
to cancel the review.

Skipping leaves that issue unresolved but does not discard other corrections.
The final summary shows proposed changes, unresolved issues, and valid,
changed, and unresolved counts. Nothing is written unless the user explicitly
confirms; declining or cancelling discards the in-memory proposals.

An activity correction replaces the configured activity-type tags with the
chosen canonical type, keeping unrelated tags and their order. A project
correction sets the selected project. A Jira correction prefixes the existing
description with the upper-case key, or uses the key alone when the description
is empty.

## Activity summary

With `report --include-summary`, each non-marker entry contributes its booked
duration to one activity row:

- one configured type match goes in that type's row;
- no matches go in `Unclassified`;
- multiple distinct matches go in `Conflicting`.

Rows follow the configured taxonomy order, then `Unclassified` and
`Conflicting`, including rows with zero duration. Markers are excluded.
Percentages use booked time as the denominator and are rounded independently
to two decimal places; when booked time is zero, every percentage is zero.
The activity section is separate from the client/project summary, and
totals-only output is unchanged when `--include-summary` is not selected.

## What happens when changes are written

The review reuses the snapshot it showed you; it does not refresh an entry
before submitting. It sends only changed fields—description, project, and/or
tags—and combines all changes for one entry into one PUT request. A tag update
replaces the complete tag array, so a tag added by someone else after the
snapshot was loaded could be overwritten. The summary warns about this when a
tag change is planned.

There is one request per changed entry, with at least a second between
requests. The command checks available quota before asking for final
confirmation. Writes can partially succeed: the final result distinguishes
succeeded, failed, uncertain, and not-attempted entries. There is no rollback,
and a reported success is not a read-back verification. The command does not
automatically undo earlier successes if a later request fails.

The one-time disposable-entry qualification and its evidence are recorded in
the [live-write qualification runbook](live-write-qualification.md). The
decision to use per-entry PUT rather than bulk PATCH is recorded in
[ADR 0001](adr/0001-per-entry-put-for-review-writes.md).
