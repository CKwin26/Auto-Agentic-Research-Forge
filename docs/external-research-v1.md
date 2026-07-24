# External Research Capability V1

Research Forge remains a standalone local application. External research is
horizontal Retrieval Gateway infrastructure, not a fifth research phase.

## Local default: invoke Codex

The default public-web backend is `codex_native_web_search`:

```text
Research Forge
  -> Retrieval Gateway policy and query sanitizer
  -> local Codex SDK (existing Codex login)
  -> Codex native Web Search
  -> immutable raw response
  -> deterministic normalization and audit
```

The Codex worker runs in an isolated temporary directory with a read-only
sandbox. It may use Web Search only. A command execution or file-change item
causes the provider attempt to fail with `policy_denied`.

This arrangement does not embed Research Forge inside Codex. Research Forge can
start, display local projects, run offline workflow steps, and preserve its DAG
without Codex. When Codex is absent or its login/search cannot be verified, the
public-web capability is reported as `degraded` or `unavailable`.

`openai_web_search` is the optional server backend. It uses the Responses API
web-search tool and reads `OPENAI_API_KEY` only inside its provider adapter.
Credentials are never copied into requests, prompts, artifacts, or audit logs.

## Providers

| Capability | Provider | Current safety boundary |
|---|---|---|
| Scholarly search | `paper_search_mcp` | `search_papers` only; safe source allowlist; Google Scholar and all Sci-Hub/download tools prohibited |
| Code | `github` | Official REST API; discovered repositories pinned to a full commit SHA |
| Models and datasets | `huggingface` | Official Hub SDK; downloads require explicit authorization and a pinned revision |
| Public web | `codex_native_web_search` | Local Codex login; Web Search only |
| Public web (server) | `openai_web_search` | Responses API web search; optional |
| Open access | `open_access` | Unpaywall resolution plus bounded public-PDF acquisition; no paywall bypass |
| Subscription access | `institutional_access` | Visible local browser and user-bound document handoff; no credential capture, MFA bypass, sharing, or redistribution |
| Evidence analysis | `paperqa` | PaperQA 2026.3.18 over a frozen, rights-approved corpus; every persisted answer requires source spans and has no verdict authority |

## Readiness

Readiness is computed per capability from four independent conditions:

1. the provider is registered;
2. live health has been verified;
3. release-test evidence exists;
4. the External Research V1 storage migration is present.

Only all four conditions produce `ready`. Installing a package or finding an
environment variable is insufficient. The aggregate capability is ready only
when every component capability is ready.

The Codex-native provider has been live-smoke-tested through the Gateway and is
the authoritative local backend for `PUBLIC_WEB_READY`. The optional
`openai_web_search` provider remains available for server deployments but is not
required for local readiness. Institutional readiness still requires an actual
user-authenticated document handoff, not only a working browser launcher.

Inspect the current state:

```powershell
research-forge --workflow-root .rfab retrieval readiness --refresh
```

The project network policy remains `offline` by default. Enabling retrieval
requires owner approval metadata and explicit providers, infrastructure domains,
HTTP/tool methods, resource types, and budgets.

## Scientific authority

External discovery signals cannot decide a hypothesis or Study verdict.
Deterministic integrity checks and frozen experiment outputs retain higher
authority. PaperQA and NLI outputs can identify evidence or trigger review, but
cannot rewrite historical verdicts.
