# Fixture provenance

`reports_v3_page.json` is a hand-sanitised Reports API v3 detailed-response
fixture. Identifiers, client/project names, descriptions, tags, and timestamps
are synthetic; the shape, nullability, seconds unit, nested wrapper, and
running-entry representation are retained. `reports_v3_headers.json` records
the non-secret pagination/range headers used by the adapter contract.

No credential, Authorization header, personal account data, or live response
body is committed. A future opt-in live capture must replace personal values
with synthetic values, remove all authentication and quota secrets, preserve
JSON nullability/units, and record any synthetic page or running-entry
extensions separately from captured data.
