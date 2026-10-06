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
    entry_changes,
    matching_prefix,
    normalize_description,
    ordered_candidates,
    replace_activity_type,
    resolve_unique_prefix,
    review_groups,
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


def test_normalize_description_collapses_whitespace_and_case_but_keeps_wording() -> None:
    assert normalize_description(" Review\t change\n") == "review change"
    assert normalize_description("review changes") != normalize_description("review change")
    assert normalize_description("review-change") != normalize_description("review change")


def test_review_groups_share_nonadjacent_references_but_keep_blank_descriptions_separate() -> None:
    flagged = make_entry(1, description=" Review\t change ", project_id=None)
    unrelated = make_entry(2, description="Other", tags=("doing",))
    reference = replace(
        make_entry(3, description="review  CHANGE", tags=("doing",)),
        start=datetime(2026, 8, 8, 11, tzinfo=UTC),
    )
    blank_one = make_entry(4, description=" ", project_id=None)
    blank_two = make_entry(5, description="\n", project_id=None)
    marker = make_entry(6, description="Review change", tags=("marker",))
    candidates = ordered_candidates(
        (flagged, unrelated, reference, blank_one, blank_two, marker), 7, ("doing",)
    )

    groups = review_groups(
        (flagged, unrelated, reference, blank_one, blank_two, marker), candidates, 7
    )

    assert [[entry.id for entry in group.members] for group in groups] == [[1, 3], [4], [5]]
    assert [[candidate.original.id for candidate in group.candidates] for group in groups] == [
        [1],
        [4],
        [5],
    ]


def test_group_reuses_fields_from_different_partial_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toggl_cli.cli import _review_group

    activity_reference = make_entry(1, tags=("doing",), project_id=None, description="Review")
    project_reference = make_entry(2, tags=("email",), project_id=9, description=" review ")
    entries = (activity_reference, project_reference)
    candidates = ordered_candidates(entries, 7, ("doing", "reviewing"))
    group = review_groups(entries, candidates, 7)[0]
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail(prompt))

    reviewed = _review_group(
        Console(file=StringIO(), no_color=True),
        group,
        ("doing", "reviewing"),
        (),
        (Project(9, "Client"),),
        UTC,
    )

    assert [candidate.proposed.project_id for candidate in reviewed] == [9, 9]
    assert [candidate.proposed.tags for candidate in reviewed] == [("doing",), ("email", "doing")]
    assert all(candidate.valid for candidate in reviewed)


def test_group_uses_valid_reference_without_reprompting_and_preserves_target_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toggl_cli.cli import _review_group

    reference = make_entry(1, tags=("doing", "email"), project_id=9, description="Review")
    target = make_entry(2, tags=("email",), project_id=None, description=" Review ")
    entries = (reference, target)
    candidates = ordered_candidates(entries, 7, ("doing",))
    group = review_groups(entries, candidates, 7)[0]
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail(prompt))
    output = StringIO()

    reviewed = _review_group(
        Console(file=output, no_color=True),
        group,
        ("doing",),
        (),
        (Project(9, "Client"),),
        UTC,
    )

    assert len(reviewed) == 1
    assert reviewed[0].proposed.description == " Review "
    assert reviewed[0].proposed.tags == ("email", "doing")
    assert reviewed[0].proposed.project_id == 9
    assert reference.description == "Review"
    assert reference.tags == ("doing", "email")
    assert output.getvalue().splitlines()[0] == "Review — entries 1, 2"
    assert "Entry 1 (reference)" in output.getvalue()
    assert "Entry 2 (needs changes)" in output.getvalue()
    assert "Inferred shared activity type: doing" in output.getvalue()
    assert "Inferred shared project: Client (9)" in output.getvalue()


def test_group_prompts_once_per_conflicting_field(monkeypatch: pytest.MonkeyPatch) -> None:
    from toggl_cli.cli import _review_group

    first = make_entry(1, tags=("doing",), project_id=9, description="Work")
    second = make_entry(2, tags=("reviewing",), project_id=10, description=" work ")
    target = make_entry(3, tags=(), project_id=None, description="WORK")
    entries = (first, second, target)
    candidates = ordered_candidates(entries, 7, ("doing", "reviewing"))
    group = review_groups(entries, candidates, 7)[0]
    prompts: list[str] = []
    answers = iter(("doing", "Client B"))

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    reviewed = _review_group(
        Console(file=StringIO(), no_color=True),
        group,
        ("doing", "reviewing"),
        (),
        (Project(9, "Client A"), Project(10, "Client B")),
        UTC,
    )

    assert len(prompts) == 2
    assert "activity type" in prompts[0]
    assert "project" in prompts[1]
    assert [candidate.proposed.project_id for candidate in reviewed] == [10]
    assert [candidate.proposed.tags for candidate in reviewed] == [("doing",)]


def test_group_asks_one_shared_jira_key_for_all_affected_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toggl_cli.cli import _review_group

    activity_partial = make_entry(1, tags=("doing",), project_id=None, description="Work")
    project_partial = make_entry(2, tags=(), project_id=9, description=" Work ")
    entries = (activity_partial, project_partial)
    candidates = ordered_candidates(entries, 7, ("doing",), ("doing",))
    group = review_groups(entries, candidates, 7)[0]
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return "xyz-42"

    monkeypatch.setattr("builtins.input", answer)
    reviewed = _review_group(
        Console(file=StringIO(), no_color=True),
        group,
        ("doing",),
        ("doing",),
        (Project(9, "Client"),),
        UTC,
    )

    assert len(prompts) == 1
    assert "Jira key" in prompts[0]
    assert [candidate.proposed.description for candidate in reviewed] == [
        "XYZ-42 Work",
        "XYZ-42  Work ",
    ]


def test_jira_only_correction_preserves_already_valid_classifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toggl_cli.cli import _review_group

    entry = make_entry(
        1,
        tags=("email", "DOING"),
        project_id=9,
        description="Work",
    )
    entries = (entry,)
    candidates = ordered_candidates(entries, 7, ("doing",), ("doing",))
    group = review_groups(entries, candidates, 7)[0]
    monkeypatch.setattr("builtins.input", lambda prompt: "ABC-1")

    reviewed = _review_group(
        Console(file=StringIO(), no_color=True),
        group,
        ("doing",),
        ("doing",),
        (Project(9, "Client"),),
        UTC,
    )

    assert reviewed[0].proposed.tags == ("email", "DOING")
    assert reviewed[0].proposed.project_id == 9
    assert reviewed[0].proposed.project_name is None
    assert entry_changes(entry, reviewed[0].proposed) == {"description": "ABC-1 Work"}


def test_single_entry_group_does_not_label_existing_activity_as_inferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toggl_cli.cli import _review_group

    entry = make_entry(1, tags=("meeting",), project_id=None, description="Work")
    entries = (entry,)
    candidates = ordered_candidates(entries, 7, ("meeting",))
    group = review_groups(entries, candidates, 7)[0]
    output = StringIO()
    monkeypatch.setattr("builtins.input", lambda prompt: "Client")

    _review_group(
        Console(file=output, no_color=True),
        group,
        ("meeting",),
        (),
        (Project(9, "Client"),),
        UTC,
    )

    assert "activity=meeting" in output.getvalue()
    assert "Inferred shared activity type" not in output.getvalue()


def test_entry_changes_include_only_changed_fields() -> None:
    original = make_entry(1, tags=("email",), project_id=None)
    candidate = candidate_for(original, ("doing",))
    proposed = candidate.__class__(
        candidate.original,
        replace_activity_type(original, "doing", ("doing",)),
        candidate.issues,
        candidate.original_issues,
    )

    changes = entry_changes(proposed.original, proposed.proposed)

    assert changes == {"tags": ["email", "doing"]}


def test_entry_changes_coalesce_description_project_and_complete_tags() -> None:
    original = make_entry(1, tags=("keep",), project_id=None, description="Existing")
    proposed = replace(
        original,
        description="ABC-123 Existing",
        project_id=42,
        tags=("keep", "doing"),
    )

    assert entry_changes(original, proposed) == {
        "description": "ABC-123 Existing",
        "project_id": 42,
        "tags": ["keep", "doing"],
    }


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


def test_single_entry_group_correction_recalculates_all_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = candidate_for(
        make_entry(1, project_id=None, description="No key"),
        ("doing",),
        ("doing",),
    )
    answers = iter(("doing", "Project", "ABC-123"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    result = cli._review_group(
        Console(file=StringIO(), no_color=True),
        review_groups((candidate.original,), (candidate,), 7)[0],
        ("doing",),
        ("doing",),
        (Project(9, "Project"),),
        UTC,
    )
    corrected = result[0]

    assert corrected.valid
    assert corrected.proposed.tags == ("doing",)
    assert corrected.proposed.project_id == 9
    assert corrected.proposed.description == "ABC-123 No key"


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

    assert "Prepare client proposal — entry 4513239478" in prompts[0]


def test_project_prompt_excludes_inactive_projects() -> None:
    output = StringIO()
    candidate = candidate_for(make_entry(1, project_id=None), ("doing",))

    selected = cli._choose_project(
        Console(file=output, no_color=True),
        candidate,
        (Project(9, "Archived", active=False),),
    )

    assert selected is None
    assert "no active projects are available" in output.getvalue()


def test_single_entry_group_handles_ambiguous_choices_and_skip(
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

    result = cli._review_group(
        Console(file=output, no_color=True),
        review_groups((candidate.original,), (candidate,), 7)[0],
        ("doing", "documentation"),
        ("doing",),
        (Project(9, "alpha"), Project(10, "alpine")),
        UTC,
    )
    corrected = result[0]

    assert "Ambiguous choice" in output.getvalue()
    assert corrected.proposed.tags == ("doing",)
    assert corrected.proposed.project_id == 9
    assert MISSING_JIRA_REFERENCE in corrected.issues


def test_review_summary_and_cancellation_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    original = make_entry(1, project_id=None, description="Prepare client proposal")
    candidate = candidate_for(original, ("doing",))
    candidate = candidate.__class__(
        candidate.original,
        replace(original, project_id=9, tags=("doing",)),
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
    assert "2 field update(s), 1 per-entry PUT request(s)" in rendered
    assert "complete tag array" in rendered
    assert "Write readiness: blocked" in rendered
    assert "plan issue 09" in rendered
    assert "Prepare client proposal — entry 1:" in rendered
    assert "project: (none) -> Client work (9)" in rendered

    monkeypatch.setattr("builtins.input", lambda prompt: "c")
    with pytest.raises(cli.ReviewCancelled):
        cli._prompt(Console(file=StringIO(), no_color=True), "prompt")
