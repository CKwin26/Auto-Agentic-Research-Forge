# Paper Search MCP integration

- Upstream: `openags/paper-search-mcp`
- Pin: version `0.1.4`, commit
  `c8b642183bb725f0a7faec89e58b558df09079d1`
- License: MIT
- Transport: local stdio MCP subprocess

Research Forge exposes only `search_papers`. It supplies an explicit public
source allowlist and rejects any response claiming use of Google Scholar.
Upstream Sci-Hub and fallback download tools are never callable through the
adapter. Raw MCP results are frozen before normalization.

The adapter provides bounded `start`/`stop`, health probing, timeout
classification, publication search, identifier-oriented metadata resolution,
open-copy candidate filtering, and underlying-provider coverage reporting.
Each real operation owns its stdio context, so success, timeout, cancellation,
or failure closes the subprocess rather than leaving a background MCP server.

Full-text bytes are intentionally not downloaded by the MCP adapter. An
open-copy URL returned by Paper Search is handed back to the Retrieval Gateway,
which applies the project policy, byte budget, URL/redirect validation, rights
decision, and the dedicated Open Access provider before creating a Snapshot.
This is the V1 `fetch_open_full_text` path at the product boundary and prevents
the upstream fallback/Sci-Hub tools from bypassing Forge policy.

The capability is not ready merely because the dependency is installed. A
release smoke test must also prove MCP handshake, timeout/cancellation, safe
source selection, provider coverage, and restart behavior.
