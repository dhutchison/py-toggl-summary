# Live-write qualification

`TogglApi` defaults to `writes_qualified=False`. The interactive review command
explicitly opts into writes because the disposable per-entry PUT qualification
probe has passed; other adapter callers remain fail-closed. This repository
does not provide a runtime flag or environment variable that bypasses that
boundary.

Issue 09 required a separate, human-authorized probe before live writes could
be enabled. That probe passed on 2026-08-13. This is release evidence, not an
automated test. Do not rerun it against production data, real work entries, or
the normal `review` command unless requalification is needed.

## Preconditions

Set up the disposable workspace through the Toggl UI. Create two active
projects named `probe-source-<nonce>` and `probe-target-<nonce>`, and three
known tags named `probe-keep-<nonce>`, `probe-unrelated-<nonce>`, and
`probe-known-<nonce>`. Create one disposable seed entry and four completed
non-marker entries on one narrow reporting day. Save an exact local snapshot
of the four entries, including all fields required for read-back and
restoration. All four entries must initially be in the source project with the
`probe-keep-<nonce>` and `probe-unrelated-<nonce>` tags and unique sentinel
descriptions. The snapshot must cover
description, project, complete tag array, timestamps, duration, timer state,
billability, workspace, user, client, task, and every other field that the
read-back contract promises to preserve. Use aliases in the operator note;
keep raw IDs and the snapshot outside the repository and discard them after the
note is reviewed.

Use the OS credential store only. Never put a token in an argument, environment
capture, shell history, debug trace, redirected output, or a committed file.
Do not enable debug diagnostics.

## Fixed probe budget

The probe consists of exactly ten mutation/report requests:

1. PUT `{"description": "<sentinel>"}` on entry 1.
2. PUT `{"project_id": <target-project-id>}` on entry 2.
3. PUT the complete original tag array plus the known tag on entry 3.
4. PUT the complete original tag array plus a new tag on entry 4.
5. Read the same narrow report once and compare every field in the snapshot.
6. PUT entry 1's complete original changed-field values.
7. PUT entry 2's complete original changed-field values.
8. PUT entry 3's complete original tag array.
9. PUT entry 4's complete original tag array.
10. Read the report once more.

Pace every request by at least one second. Do not retry, paginate, issue an
invalid-ID request, refresh entries, or add diagnostics. The initial snapshot
is prepared during the UI setup and the starting quota value must be supplied
by the operator before the ten-request window begins. This repository does not
silently spend an extra API request for either preflight; if the operator
cannot establish at least twelve quota units out-of-band, stop rather than
starting the probe. If local policy counts that preflight request in the
budget, resolve that policy before running because the defined probe window is
exactly ten requests.

Review every response manually. Require HTTP 200 and a recorded remaining/reset
quota value. Each PUT must target exactly one entry and contain only the
changed fields; a tag update must contain the complete desired tag array and
must not use `tag_action: "delete"`. Before request `n`, the recorded
remaining quota must cover the current request and all remaining requests; the
first preflight must be at least 12, and after response `n` the header must be
at least `10 - n`. A missing header or a value below that threshold stops the
probe immediately. A failure for the new-tag case is an expected observation,
but it means tag creation is not qualified: the implementation must require
pre-existing tags or report that limitation. Any other ambiguous or
unclassified response stops the probe; do not retry blindly.

## Qualification evidence

The probe qualifies the write adapter only if every supported mutation reads
back exactly, all four restorations read back exactly, the request budget was
not exceeded, and no response was ambiguous. A successful HTTP response alone
is not evidence of mutation.

After the final read-back succeeds, tear down through the Toggl UI: delete the
four disposable entries and the seed entry, then remove the disposable source
and target projects and all probe tags. Confirm that no disposable object
remains. If any mutation, restoration, read-back, or teardown operation is
ambiguous, stop and preserve the workspace for an operator to inspect rather
than retrying or guessing.

Record only a redacted operator note outside the repository:

```text
date:
workspace alias:
source/target project aliases:
entry aliases:
request count and pacing:
mutation status and read-back for each alias:
quota remaining/reset values:
new-tag outcome:
restoration status and final read-back:
qualification: PASS or FAIL
```

Do not store authorization headers, raw request/response bodies, raw IDs, or
the original snapshot in the note. If a future API-contract change invalidates
this evidence, or requalification fails, remove the review command's explicit
opt-in and preserve the disposable workspace for an operator to inspect.
