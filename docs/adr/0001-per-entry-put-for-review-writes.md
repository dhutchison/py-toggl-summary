# Use per-entry PUT for review writes

**Status:** Accepted  
**Date:** 2026-08-13

Toggl's bulk PATCH endpoint applies the same patch operations to every entry ID
in a request, so it is useful only when entries share an identical change. Our
review often proposes different fields or values for different entries, and
the disposable probe showed that bulk tag operations could return success
without persisting the tags on read-back. We therefore use one PUT per changed
entry, combining that entry's changed fields in the request. This costs more
paced API calls and writes can partially succeed, but it supports the varied
changes review needs and follows the per-entry behaviour we qualified. The
[qualification runbook](../live-write-qualification.md) records the probe and
its limits.
