# OpenAI and Codex web search

Local Research Forge defaults to `codex_native_web_search`, which reuses the
configured local Codex login/provider. It runs in a temporary read-only
workspace and rejects command execution and file changes.

The application can therefore run as its own process while using Codex; it does
not depend on an active chat task. The native adapter is the authoritative local
backend for `PUBLIC_WEB_READY` and requires no separate API key.

Server deployments may use `openai_web_search`. That adapter uses the Responses
API formal tool declaration:

```python
tools=[{"type": "web_search"}]
```

It does not use preview search models or `web_search_preview`. Required workflow
searches must contain an actual web-search call. Cache-only/live mode and allow/
block domain filters are preserved in the QueryPlan. Credentials and provider
base URLs remain provider-local. The adapter derives its infrastructure domain
from the actual HTTPS base URL before policy evaluation; a proxy or
OpenAI-compatible gateway must therefore be explicitly present in the Project
allowlist and is recorded in the retrieval audit. Plain HTTP endpoints and URLs
containing embedded credentials are rejected.

Formal Responses output is frozen in four layers: the provider raw response,
sanitized query records, consulted-source records, and citation records derived
from the returned result URLs. These are separate immutable artifacts and remain
separate when the response is reused through `cache_only`.

`cache_only` is a hard no-network operation. It reuses only a previously frozen
provider response and creates a new Study/Step binding; a cache miss is reported
as `resource_not_found` and never falls through to a live provider call.
Approved non-offline Workflow policies produce `live` plans.

`PUBLIC_WEB_READY` requires a recent successful policy-controlled
`codex_native_web_search` run with at least one result. The formal
`openai_web_search` Responses adapter remains an optional server backend. If
that optional backend receives `PermissionDenied`, Research Forge records the
bounded provider diagnostic without degrading a verified Codex-native public
web capability.
