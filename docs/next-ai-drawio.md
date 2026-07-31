# next-ai-draw-io integration

Research Forge uses
[`@next-ai-drawio/mcp-server`](https://github.com/DayuanJiang/next-ai-draw-io)
as the required interactive-editor binding for conceptual figures. It is not a
model provider and it has no scientific decision authority.

## Boundary

The integration is deliberately split:

1. The configured Research Forge backend (Codex by default) converts a natural
   language request into a bounded node/edge plan.
2. Research Forge validates the graph and deterministically creates editable
   draw.io XML.
3. The pinned next-ai-draw-io MCP server can load the source into a real-time
   browser session for manual or agent-assisted edits.
4. The edited source must pass the Research Forge audit before local draw.io
   Desktop export and publication visual-integrity checks.

The model cannot submit raw XML, styles, remote images, or new evidence.
Numeric charts and tables do not use this path. Conceptual figures cannot
silently fall back to native SVG: missing `.drawio` provenance, MCP binding,
source audit, or vector export leaves the Stage 4 artifact unresolved.

## Create a concept figure

```powershell
research-forge diagram ai-draft `
  "Show the four Research Forge phases and their approval gates" `
  --output figures\workflow.drawio
```

For a manuscript figure, pass a frozen evidence text envelope and the claim IDs
that the figure is permitted to cite:

```powershell
research-forge diagram ai-draft `
  "Show how verified evidence reaches the manuscript" `
  --evidence-context frozen-figure-evidence.txt `
  --claim-id claim-method-01 `
  --output figures\evidence-flow.drawio
```

The command writes:

- the editable `.drawio` source;
- a `.drawio.plan.json` typed planning record;
- SHA-256 bindings in its JSON response.

## Configure next-ai-draw-io

Node.js with `npx` is required only for the interactive editor. Generate a
pinned MCP client configuration:

```powershell
research-forge diagram mcp-config `
  --output next-ai-drawio.mcp.json
```

The generated server entry runs:

```text
npx -y @next-ai-drawio/mcp-server@0.2.3
```

It contains no model key. The MCP server exposes `start_session`,
`create_new_diagram`, `load_diagram`, `edit_diagram`, `get_diagram`, and
`export_diagram`. In an MCP-capable Codex client, start a session and use
`load_diagram` with the Research Forge `.drawio` path.

The upstream default editor UI is loaded from `embed.diagrams.net`. Private
deployments can generate a configuration for an approved self-hosted endpoint:

```powershell
research-forge diagram mcp-config `
  --drawio-base-url https://drawio.example.org `
  --output next-ai-drawio.private.mcp.json
```

## Audit edited sources

AI or manual browser editing is treated as an untrusted proposal. Audit before
reuse:

```powershell
research-forge diagram audit-ai-edit figures\workflow.drawio
```

Visible numeric tokens are rejected by default. A frozen, evidence-bound number
must be explicitly allowed:

```powershell
research-forge diagram audit-ai-edit figures\results-flow.drawio `
  --allow-number 40 --allow-number 80
```

The audit also rejects malformed XML, active content, external links, remote
images, and oversized diagrams. A passing diagram is still a visual artifact,
not evidence and not a scientific verdict.
