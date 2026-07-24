# Retrieval Gateway threat model

## Protection goals

- Provider credentials and local secrets never leave provider adapters.
- Project paths, private code, customer names, and private data never enter
  external queries.
- External content has no instruction authority and cannot change contracts,
  phase permissions, or scientific verdicts.
- The system never fabricates sources, DOI values, citations, licenses,
  coverage, hashes, or provider responses.
- Frozen snapshots, ResourceSets, runs, and historical verdicts remain
  immutable.

## Main threats and controls

| Threat | Control |
|---|---|
| Prompt injection | External content is marked untrusted, raw responses are frozen before deterministic normalization, and external text has no instruction authority. |
| Secret exfiltration | Credentials resolve inside adapters; query sanitization rejects secrets, local paths, email addresses, and internal identifiers; credentials never enter prompts or ordinary logs. |
| SSRF and arbitrary browsing | Provider registries and policy-bound domains restrict destinations; HTTPS targets and redirects are resolved and checked before download. |
| Proxy fake-IP ambiguity | `198.18.0.0/15` is allowed only with owner approval, a loopback HTTPS proxy, and a public hostname. Literal IP targets, local suffixes, private addresses, and mixed resolutions remain blocked. |
| Result-driven protocol changes | Stage profiles prevent open-ended Experimentation retrieval; scientific changes require a contract vNext or Repair Contract. |
| Supply-chain execution | Retrieval never downloads and executes code. Sandbox execution accepts only pinned, hashed, licensed, contract-authorized resources. |
| Metadata poisoning | Multi-provider deduplication retains conflicts instead of selecting a convenient value silently. |
| Retractions and corrections | Synthesis creates warnings or evidence conflicts; it never rewrites historical verdicts. |
| Replay and duplicate billing | Idempotency keys bind requests; completed runs are reused; successful steps are not repeated. |

## Residual risks

The proxy fake-IP exception trusts the configured local proxy to route an
approved public hostname correctly. It therefore requires explicit owner
approval and is visible in every relevant audit event. Institutional access
still depends on a user-controlled authenticated browser session and cannot be
declared ready until a real, hash-bound document handoff succeeds.
