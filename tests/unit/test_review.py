from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from toggl_cli import cli
from toggl_cli.domain import TimeEntry
from toggl_cli.review import (
    MISSING_ACTIVITY_TYPE,
    MISSING_JIRA_REFERENCE,
    MISSING_PROJECT,
    Project,
    add_jira_reference,
    candidate_for,
    grouped_patches,
    matching_prefix,
    ordered_candidates,
    replace_activity_type,
    resolve_unique_prefix,
    review_state,
)


def make_entry(
    identifier: int,
    *,
    tags: tuple[str, ...] = (),
    project_id: int | None = 10,
    description: str = "Existing",
) -> TimeEntry:
    return TimeEntry(
        id=identifier,
        description=description,
        start=datetime(2026, 8, 8, 9, tzinfo=UTC),
        stop=datetime(2026, 8, 8, 10, tzinfo=UTC),
        duration_ms=3_600_000,
        tags=tags,
        project_id=project_id,
        user_id=7,
    )


def test_review_state_orders_quality_issues_and_skips_jira_until_activity_is_known() -> None:
    state = review_state(make_entry(1, project_id=None), ("doing",), ("doing",))

    assert state.issues == (MISSING_ACTIVITY_TYPE, MISSING_PROJECT)
    assert state.needs_review
    assert not review_state(make_entry(2, tags=("marker",)), ("doing",)).needs_review


def test_activity_replacement_preserves_unrelated_tags_and_first_position() -> None:
    result = replace_activity_type(
        make_entry(1, tags=("keep", "DOING", "email", "supporting")),
        "Reviewing",
        ("reviewing", "doing", "supporting"),
    )

    assert result.tags == ("keep", "Reviewing", "email")


def test_jira_reference_is_uppercased_and_prefixed() -> None:
    assert add_jira_reference(make_entry(1), "abc-123").description == "ABC-123 Existing"
    with pytest.raises(ValueError):
        add_jira_reference(make_entry(1), "not-a-key")


def test_prefix_matching_requires_unique_selection() -> None:
    choices = ("Doing", "Documentation")

    assert matching_prefix("do", choices) == choices
    assert resolve_unique_prefix("doing", choices) == "Doing"
    assert resolve_unique_prefix("do", choices) is None


def test_grouped_patches_keep_only_net_changes() -> None:
    original = make_entry(1, tags=("email",), project_id=None)
    candidate = candidate_for(original, ("doing",))
    proposed = candidate.__class__(
        candidate.original,
        replace_activity_type(original, "doing", ("doing",)),
        candidate.issues,
        candidate.original_issues,
    )

    grouped = grouped_patches((proposed,))

    assert grouped == (((1,), ({"op": "replace", "path": "/tags", "value": ["email", "doing"]},)),)


def test_jira_issue_is_applied_only_for_known_activity() -> None:
    state = review_state(
        make_entry(1, tags=("doing",), description="No key"),
        ("doing",),
        ("doing",),
    )

    assert state.issues == (MISSING_JIRA_REFERENCE,)


def test_ordered_candidates_excludes_markers_and_entries_not_owned_by_user() -> None:
    owned = make_entry(2, tags=(), project_id=10)
    other = make_entry(1, tags=(), project_id=10)
    other = TimeEntry(
        other.id,
        other.description,
        other.start,
        other.stop,
        other.duration_ms,
        tags=other.tags,
        project_id=other.project_id,
        user_id=8,
    )
    marker = make_entry(3, tags=("marker",), project_id=None)

    candidates = ordered_candidates((owned, other, marker), 7, ("doing",))

    assert [candidate.original.id for candidate in candidates] == [2]


def test_interactive_candidate_correction_recalculates_all_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = candidate_for(
        make_entry(1, project_id=None, description="No key"),
        ("doing",),
        ("doing",),
    )
    answers = iter(("doing", "Project", "ABC-123"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    result = cli._review_candidate(
        Console(file=StringIO(), no_color=True),
        candidate,
        ("doing",),
        ("doing",),
        (Project(9, "Project"),),
    )

    assert result.valid
    assert result.proposed.tags == ("doing",)
    assert result.proposed.project_id == 9
    assert result.proposed.description == "ABC-123 No key"


def test_project_prompt_includes_entry_description(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return "Project"

    monkeypatch.setattr("builtins.input", answer)
    candidate = candidate_for(
        make_entry(4513239478, project_id=None, description="Prepare client proposal"),
        ("doing",),
    )

    cli._choose_project(
        Console(file=StringIO(), no_color=True), candidate, (Project(9, "Project"),)
    )

    assert "Prepare client proposal" in prompts[0]


def test_interactive_candidate_handles_ambiguous_choices_and_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = candidate_for(
        make_entry(1, tags=("doing", "documentation"), project_id=None, description="No key"),
        ("doing", "documentation"),
        ("doing",),
    )
    answers = iter(("do", "doing", "al", "alpha", "s"))
    output = StringIO()
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    result = cli._review_candidate(
        Console(file=output, no_color=True),
        candidate,
        ("doing", "documentation"),
        ("doing",),
        (Project(9, "alpha"), Project(10, "alpine")),
    )

    assert "Ambiguous choice" in output.getvalue()
    assert result.proposed.tags == ("doing",)
    assert result.proposed.project_id == 9
    assert MISSING_JIRA_REFERENCE in result.issues


def test_review_summary_and_cancellation_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    original = make_entry(1, project_id=None, description="Prepare client proposal")
    candidate = candidate_for(original, ("doing",))
    candidate = candidate.__class__(
        candidate.original,
        replace(original, project_id=9),
        candidate.issues,
        candidate.original_issues,
    )
    output = StringIO()
    cli._render_review_summary(
        Console(file=output, no_color=True),
        (candidate,),
        projects=(Project(9, "Client work"),),
    )
    rendered = output.getvalue()
    assert "unresolved=1" in rendered
    assert "Write readiness: blocked" in rendered
    assert "plan issue 09" in rendered
    assert "Entry 1 — Prepare client proposal:" in rendered
    assert "project: (none) -> Client work (9)" in rendered

    monkeypatch.setattr("builtins.input", lambda prompt: "c")
    with pytest.raises(cli.ReviewCancelled):
        cli._prompt(Console(file=StringIO(), no_color=True), "prompt")
