# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

## Selected visual direction

The user replaced the previous academic-journal direction with an Apple-inspired interface. Preserve its clear product hierarchy, generous whitespace, system sans-serif typography, neutral `#f5f5f7` canvas, white surfaces, restrained blue actions, soft rounded containers, and concise copy. Do not copy Apple brand assets or product imagery.

The user subsequently selected an ocean palette as the durable color system while retaining the Apple-inspired layout and hierarchy. Use `#A9C7CE` for quiet borders and pale selected surfaces, `#70A1A9` for secondary accents, `#051D25` for primary text and deepest hover states, `#3A747D` for secondary text and emphasis, and `#0D454E` for primary actions. Derived tints may be used for canvas and soft surfaces. Keep red, amber, and green only where semantic error, warning, and success differentiation is necessary.

## Product structure

The first-run experience is a deployment wizard. It must let the user either reuse a verified local Codex/ChatGPT login or configure a compatible AI API through a secret-safe local endpoint. Credentials must never enter task payloads, browser history, audit events, or Git.

After deployment, the primary application is one persistent four-stage tab workspace: `方向发现`, `协议与可行性`, `实验与判定`, and `论文与审计`. These tabs are the top-level navigation and must preserve state when switching. Phase modals must not replace primary navigation; reserve dialogs and secondary windows for evidence details, approval confirmation, focused comparisons, and other bounded subflows.

Stage 1 has two peer entry modes in the same tab: local project/folder intake and idea-only intake. Project intake remains the default. When a phase Gate is approved, freeze the handoff, automatically activate the next phase tab, and keep the completed phase accessible as read-only history. A scientific backfill discovered in Stage 4 must route directly back to the Stage 3 tab while preserving the historical Run and Verdict.

## Project import and paper directions

The project source field must keep both paths available: a native Windows folder picker for choosing a project folder or text library, and a pasteable path input as fallback. Source analysis remains read-only.

Never expose raw internal experiment track IDs in the paper-direction selector. Localize every visible direction to Chinese, group tracks that resolve to the same paper direction, and keep the chosen internal track ID only as execution metadata.

When a project enters through the `derived_materials` fallback, show a visible Chinese warning before generation: the system is narrowing a question and producing an evidence-gap working draft, while the idea verdict remains `暂不可验证` until a frozen protocol-output chain exists.

## Result-state hierarchy

Keep `想法判定`, `闭环工作稿`, `论文补全资格`, `完整论文`, and `产物审计` as independent result cards. A generated pilot draft must never be presented as a depth-ready paper. The full-paper action stays disabled until idea, evidence, and frozen-literature gates pass. Show the current diagnostic owner (`想法验证阶段`, `证据整理阶段`, `文献研究阶段`, or `论文写作阶段`) and concise Chinese blockers in the primary interface; detailed machine audit messages stay in the audit artifact.

After full-paper expansion starts, show the subordinate authoring chain in this order: submission genre, Evidence–Claim Map, reviewed hierarchical outline, reviewed/revised draft, frozen-evidence figures and tables, and claim-preserving polish. Surface unresolved figure/table slot IDs and their reasons; never present a pending slot as a finished figure or dataset.

## Research diagnostic visualization

For every completed project run, show a compact real-data research dashboard inside the existing result panel. It must expose the four-stage resource counts, let the user inspect representative bound resources by stage, keep idea/evidence/literature gates independent, and show which stage currently owns the next action. Use only values from the run artifact for evidence comparisons and counts; never invent a score or upgrade a retrospective result. Keep the dashboard subordinate to the Apple-inspired page hierarchy and stack it without horizontal overflow on mobile.

## Claim discovery

Keep claim discovery inside the same project-to-paper page and subordinate to the primary project action. Combine two visibly distinct inputs: exact, hashed author statements from the local project and external attention signals from RedFox/academic providers. External attention may recommend what to validate, but it must never be labeled or counted as scientific evidence, and it must not upgrade an idea verdict. Show degraded providers without blocking local claim extraction, deduplicate localized directions, and preserve source provenance in the run artifact.

## Workflow state machine

Treat the approved user journey as a persisted task state machine, not a sequence of disconnected forms. The primary project path is intake → read-only inspection → direction selection → research-contract confirmation → execution → verdict. Idea intake begins at discovery and must state that no scientific verdict exists yet.

Long-running work must support safe checkpoint pauses. When execution lacks data, environment setup, configuration, API credentials, or authorization, enter `waiting_for_user` with a structured requirement: what is missing, why it is needed, the affected step, acceptable alternatives, and a resume action. Never collect or persist secret values in ordinary task payloads or browser history.

## First-run usability

For a new user with no Study, keep the primary Stage 1 action above provider diagnostics and advanced research capability details. Future phase tabs may explain the four-stage model, but they must not look actionable until a Study exists.

Translate internal workflow states, profile identifiers, lock filenames, step codes, and terms such as `Gate`, `Run Plan`, and `vNext` into plain user-facing Chinese. Preserve the exact internal values only in expandable audit details.

Every owner confirmation must state what is being decided, what changes after approval, and how to propose a revision. A failed optional dependency installation or validation must offer a one-click offline continuation path and explain which local capabilities remain available.

Research verdict, evidence maturity, and paper readiness are independent outputs. A verdict page must expose the gate trace behind the decision and route the next action to the stage that owns the blocker. `unverifiable` and `inconclusive` are valid scientific outcomes and must never be presented as generic execution failures.

Do not label an existing experiment track, report, or pipeline configuration as a “研究方向”. In the selection step, present a concrete Chinese “待验证研究问题” as the primary card title and show the associated existing validation task only as secondary provenance. Do not expose raw English novelty seeds or blocker strings in the Chinese interface.

A selectable paper topic is not a renamed protocol and not merely a sentence ending in a question mark. First group related experiment tracks, reports, claims, and infrastructure freezes into a small number of independently publishable themes. Present each theme as a paper-style title, one answerable primary research question, a bounded scope, a candidate contribution, and the count of complete supporting evidence chains. Baseline freezes and implementation freezes are supporting provenance; they must never appear as standalone topics.

Show external topic-attention channels separately from project evidence. The topic-selection screen must report the real provider status and signal counts for WeChat/public-account attention and scholarly attention. A degraded or empty provider must say “本轮未取得” instead of implying that the topic is hot. External attention may inform topic discovery, but it never counts as scientific evidence or upgrades a verdict.
