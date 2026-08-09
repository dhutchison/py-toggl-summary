# Fixture provenance

`reports_v2_page.json` is a hand-sanitised Reports API v2 detailed-response
fixture used by the active adapter. Identifiers, client/project names,
descriptions, tags, and timestamps are synthetic; the response envelope,
millisecond `dur` unit, nullable `end`, and page metadata are retained.

The `reports_v3_page.json` and `reports_v3_headers.json` fixtures remain as
synthetic research fixtures for the v3 contract that was rejected by the
configured Free workspace with HTTP 402. They are not used by the active
report path.

No credential, Authorization header, personal account data, or live response
body is committed. Any future live capture must replace personal values with
synthetic values, remove all authentication and quota secrets, preserve JSON
nullability/units, and record any synthetic page or running-entry extensions
separately from captured data.
