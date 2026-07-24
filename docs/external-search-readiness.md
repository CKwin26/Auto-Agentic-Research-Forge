# External search readiness

Readiness is calculated, not configured by a display flag. A capability is
`READY` only when its adapter is configured, a live health probe has succeeded,
its release-test evidence is present, and migrations
`003_external_research_v1` plus `004_retrieval_artifact_context` are installed.
Open-access, institutional-access, and evidence-analysis readiness additionally
require the corresponding recent hash-bound acquisition or evidence artifact;
a successful resolver, session, or adapter call alone is only `DEGRADED`.

The current local state may contain a mixture of `READY`, `DEGRADED` and
`UNAVAILABLE` components. Package import success and the presence of an API key
do not prove provider health. `PUBLIC_WEB_READY` is satisfied by a recent
policy-controlled Codex-native Web Search run; the Responses adapter is an
optional server backend. Institutional readiness requires a real
user-controlled handoff. `public_research_loop_ready` covers the complete
public path and does not require institutional access.
`external_research_v1_ready` remains the stricter conjunction of every
component, including the optional institutional handoff.

Use `research-forge retrieval readiness --refresh` or
`GET /retrieval/readiness`. The result is append-only and time-stamped. Recent
policy-controlled Gateway failures are included as bounded diagnostics. This
lets a deployment distinguish “not probed” from cases such as a configured
Responses-compatible endpoint that rejects the formal `web_search` tool.
