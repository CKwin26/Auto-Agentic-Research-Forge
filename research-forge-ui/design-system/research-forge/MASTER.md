# Research Forge design system

## Product direction

Research Forge uses a restrained scientific-workbench interface. Pages should
make the current phase, next required action, evidence state, and human gate
clear before presenting implementation detail.

## Ocean palette

| Role | Hex |
| --- | --- |
| Background | `#F3F8F8` |
| Surface | `#FFFFFF` |
| Soft surface | `#DCEBED` |
| Primary | `#0D454E` |
| Secondary | `#3A747D` |
| Muted accent | `#70A1A9` |
| Pale accent | `#A9C7CE` |
| Success | `#178F4B` |
| Warning | `#C65A13` |
| Destructive | `#C83B32` |
| Foreground | `#051D25` |

## Interaction rules

- Use one visually dominant action per decision surface.
- Show active work with a spinner, current step name, attempt, and elapsed time.
- Human gates must offer approve, request changes, and decide later.
- Collapse historical attempts; keep the current and failed step visible.
- Never show a stage as complete unless its persisted `StepInstance` records
  the required output artifacts and gate state.
- Use Lucide-style SVG icons rather than emoji.
- Keep keyboard focus visible and maintain a minimum 4.5:1 text contrast ratio.
- Respect `prefers-reduced-motion` and avoid layout-shifting hover effects.

## Layout

- Keep the four research phases visible as tabs.
- Place the ordered step route beside the current work surface.
- Use modals or drawers for evidence, audit details, and contract editing so the
  user does not lose workflow context.
- Support 375 px, 768 px, 1024 px, and 1440 px viewports without horizontal
  scrolling.
