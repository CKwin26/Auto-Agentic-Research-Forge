# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

## Selected visual direction

The user replaced the previous academic-journal direction with an Apple-inspired interface. Use `apple-reference-macbook-air.png` in this directory as the current visual reference. Preserve its clear product hierarchy, generous whitespace, system sans-serif typography, neutral `#f5f5f7` canvas, white surfaces, restrained blue actions, soft rounded containers, and concise copy. Do not copy Apple brand assets or product imagery.

## Product structure

The product is one page with one primary work area and a two-option business-mode selector. `项目生成论文` is the default and primary mode; `端到端想法到论文` is the alternate mode on the same page. Do not turn the modes into separate pages or a sidebar information architecture. Do not restore the prior eight-section navigation or the evidence-annotation sidebar. Keep four-stage evidence details compact and subordinate inside the active mode.

## Project import and paper directions

The project source field must keep both paths available: a native Windows folder picker for choosing a project folder or text library, and a pasteable path input as fallback. Source analysis remains read-only.

Never expose raw internal experiment track IDs in the paper-direction selector. Localize every visible direction to Chinese, group tracks that resolve to the same paper direction, and keep the chosen internal track ID only as execution metadata.

When a project enters through the `derived_materials` fallback, show a visible Chinese warning before generation: the system is narrowing a question and producing an evidence-gap working draft, while the idea verdict remains `暂不可验证` until a frozen protocol-output chain exists.

## Result-state hierarchy

Keep `想法判定`, `闭环工作稿`, `论文补全资格`, `完整论文`, and `产物审计` as independent result cards. A generated pilot draft must never be presented as a depth-ready paper. The full-paper action stays disabled until idea, evidence, and frozen-literature gates pass. Show the current diagnostic owner (`想法验证阶段`, `证据整理阶段`, `文献研究阶段`, or `论文写作阶段`) and concise Chinese blockers in the primary interface; detailed machine audit messages stay in the audit artifact.

## Research diagnostic visualization

For every completed project run, show a compact real-data research dashboard inside the existing result panel. It must expose the four-stage resource counts, let the user inspect representative bound resources by stage, keep idea/evidence/literature gates independent, and show which stage currently owns the next action. Use only values from the run artifact for evidence comparisons and counts; never invent a score or upgrade a retrospective result. Keep the dashboard subordinate to the Apple-inspired page hierarchy and stack it without horizontal overflow on mobile.
