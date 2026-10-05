# Fixture provenance

`reports_v2_page.json` is a hand-sanitised Reports API v2 detailed-response
fixture used by the active adapter. Identifiers, client/project names,
descriptions, tags, and timestamps are synthetic; the response envelope,
millisecond `dur` unit, nullable `end`, and page metadata are retained.

It was captured from the authorized account on 2026-08-07 using one read-only
GET request to
`https://api.track.toggl.com/reports/api/v2/details?page=1&user_agent=toggl-cli&workspace_id=<configured-workspace>&since=2026-08-07&until=2026-08-07`.
The response contained eight rows on one page (`total_count=8`, `per_page=50`)
and no running row. The raw response was 3167 bytes with SHA-256
`48b8b87f9de63d83c879b4459f292eda0e8fe6040afcf0554387f97ef1da3f75` at capture
time.

The capture was sanitized in memory before being written here: all identifier
values were replaced with deterministic synthetic values, all
descriptive/name/tag strings were replaced with synthetic labels, and
authentication, quota headers, and raw response metadata outside the JSON body
were discarded. JSON keys, nulls, list shapes, numeric duration units,
booleans, `updated`, `use_stop`, and timestamp offset formats were retained.
The running-entry behavior remains covered by a synthetic second-page test
because the source day did not contain one; no Toggl data was created or
changed.

The `reports_v3_page.json` and `reports_v3_headers.json` fixtures remain as
synthetic research fixtures for the v3 contract that was rejected by the
configured Free workspace with HTTP 402. They are not used by the active
report path.

No credential, Authorization header, personal account data, or live response
body is committed. Any future live capture must replace personal values with
synthetic values, remove all authentication and quota secrets, preserve JSON
nullability/units, and record any synthetic page or running-entry extensions
separately from captured data.
