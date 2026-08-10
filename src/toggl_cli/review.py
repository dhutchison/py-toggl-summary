"""Pure review planning and correction logic.

The terminal wizard and Toggl adapter depend on this module; this module
deliberately performs no I/O and never mutates a source ``TimeEntry``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC
from typing import Literal

from .domain import TimeEntry

IssueCode = Literal[
    "missing_activity_type",
    "conflicting_activity_type",
    "missing_project",
    "missing_jira_reference",
]

MISSING_ACTIVITY_TYPE: IssueCode = "missing_activity_type"
CONFLICTING_ACTIVITY_TYPE: IssueCode = "conflicting_activity_type"
MISSING_PROJECT: IssueCode = "missing_project"
MISSING_JIRA_REFERENCE: IssueCode = "missing_jira_reference"
JIRA_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    name: str
    workspace_id: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class ReviewState:
    entry: TimeEntry
    issues: tuple[IssueCode, ...]

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    original: TimeEntry
    proposed: TimeEntry
    issues: tuple[IssueCode, ...]
    original_issues: tuple[IssueCode, ...]

    @property
    def changed(self) -> bool:
        return self.original != self.proposed

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class PatchResult:
    success: tuple[int, ...] = ()
    failures: tuple[tuple[int, str], ...] = ()
    uncertain: tuple[int, ...] = ()
    not_attempted: tuple[int, ...] = ()
    attempts: int = 0
    retries: int = 0


def _matches(entry: TimeEntry, activity_types: tuple[str, ...]) -> tuple[str, ...]:
    by_key = {value.casefold(): value for value in activity_types}
    matches = {by_key[tag.casefold()] for tag in entry.tags if tag.casefold() in by_key}
    return tuple(value for value in activity_types if value in matches)


def activity_issue(entry: TimeEntry, activity_types: tuple[str, ...]) -> IssueCode | None:
    count = len(_matches(entry, activity_types))
    if count == 0:
        return MISSING_ACTIVITY_TYPE
    if count > 1:
        return CONFLICTING_ACTIVITY_TYPE
    return None


def effective_activity_type(entry: TimeEntry, activity_types: tuple[str, ...]) -> str | None:
    matches = _matches(entry, activity_types)
    return matches[0] if len(matches) == 1 else None


def has_jira_reference(description: str) -> bool:
    return JIRA_PATTERN.search(description) is not None


def review_state(
    entry: TimeEntry,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...] = (),
) -> ReviewState:
    """Return ordered quality issues for one owned, non-marker entry."""

    if entry.is_marker:
        return ReviewState(entry, ())
    issues: list[IssueCode] = []
    activity_problem = activity_issue(entry, activity_types)
    if activity_problem:
        issues.append(activity_problem)
    if entry.project_id is None:
        issues.append(MISSING_PROJECT)
    activity = effective_activity_type(entry, activity_types)
    if (
        activity is not None
        and activity.casefold() in {value.casefold() for value in jira_activity_types}
        and not has_jira_reference(entry.description)
    ):
        issues.append(MISSING_JIRA_REFERENCE)
    return ReviewState(entry, tuple(issues))


def replace_activity_type(
    entry: TimeEntry, activity: str, activity_types: tuple[str, ...]
) -> TimeEntry:
    """Replace all configured tags at their first position, preserving other tags."""

    configured = {value.casefold() for value in activity_types}
    tags = list(entry.tags)
    positions = [index for index, tag in enumerate(tags) if tag.casefold() in configured]
    if positions:
        first = positions[0]
        new_tags = [tag for index, tag in enumerate(tags) if index not in positions]
        new_tags.insert(first, activity)
    else:
        new_tags = [*tags, activity]
    return replace(entry, tags=tuple(new_tags))


def replace_project(entry: TimeEntry, project: Project) -> TimeEntry:
    return replace(entry, project_id=project.id, project_name=project.name)


def add_jira_reference(entry: TimeEntry, value: str) -> TimeEntry:
    key_match = JIRA_PATTERN.fullmatch(value.strip())
    if key_match is None:
        raise ValueError("Enter a Jira key such as ABC-123, or s to skip.")
    key = key_match.group(0).upper()
    description = f"{key} {entry.description}" if entry.description else key
    return replace(entry, description=description)


def candidate_for(
    entry: TimeEntry,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...] = (),
) -> ReviewCandidate:
    original_issues = review_state(entry, activity_types, jira_activity_types).issues
    return ReviewCandidate(entry, entry, original_issues, original_issues)


def recalculate(
    candidate: ReviewCandidate,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...],
) -> ReviewCandidate:
    issues = review_state(candidate.proposed, activity_types, jira_activity_types).issues
    return replace(candidate, issues=issues)


def propose(
    candidate: ReviewCandidate,
    proposed: TimeEntry,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...],
) -> ReviewCandidate:
    return recalculate(replace(candidate, proposed=proposed), activity_types, jira_activity_types)


def ordered_candidates(
    entries: tuple[TimeEntry, ...] | list[TimeEntry],
    user_id: int,
    activity_types: tuple[str, ...],
    jira_activity_types: tuple[str, ...] = (),
) -> tuple[ReviewCandidate, ...]:
    """Select owned, non-marker entries needing review in chronological order."""

    selected = [entry for entry in entries if not entry.is_marker and entry.user_id == user_id]
    selected.sort(key=lambda entry: (entry.start.astimezone(UTC), entry.id))
    candidates = [candidate_for(entry, activity_types, jira_activity_types) for entry in selected]
    return tuple(candidate for candidate in candidates if candidate.issues)


def patch_operations(original: TimeEntry, proposed: TimeEntry) -> tuple[dict[str, object], ...]:
    operations: list[dict[str, object]] = []
    if original.description != proposed.description:
        operations.append({"op": "replace", "path": "/description", "value": proposed.description})
    if original.project_id != proposed.project_id:
        operations.append({"op": "replace", "path": "/project_id", "value": proposed.project_id})
    if original.tags != proposed.tags:
        operations.append({"op": "replace", "path": "/tags", "value": list(proposed.tags)})
    return tuple(operations)


def matching_prefix(query: str, choices: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return exact matches first, otherwise all case-insensitive prefix matches."""

    normalized = query.casefold()
    exact = tuple(choice for choice in choices if choice.casefold() == normalized)
    if exact:
        return exact
    return tuple(choice for choice in choices if choice.casefold().startswith(normalized))


def resolve_unique_prefix(query: str, choices: tuple[str, ...] | list[str]) -> str | None:
    matches = matching_prefix(query, choices)
    return matches[0] if len(matches) == 1 else None


def grouped_patches(
    candidates: tuple[ReviewCandidate, ...] | list[ReviewCandidate],
) -> tuple[tuple[tuple[int, ...], tuple[dict[str, object], ...]], ...]:
    """Group identical patch lists and split each request at the API's 100-ID limit."""

    groups: dict[str, list[int]] = {}
    operation_sets: dict[str, tuple[dict[str, object], ...]] = {}
    for candidate in candidates:
        operations = patch_operations(candidate.original, candidate.proposed)
        if not operations:
            continue
        key = json.dumps(operations, sort_keys=True)
        groups.setdefault(key, []).append(candidate.original.id)
        operation_sets[key] = operations
    result: list[tuple[tuple[int, ...], tuple[dict[str, object], ...]]] = []
    for key, ids in groups.items():
        operations = operation_sets[key]
        for offset in range(0, len(ids), 100):
            result.append((tuple(ids[offset : offset + 100]), operations))
    return tuple(result)
