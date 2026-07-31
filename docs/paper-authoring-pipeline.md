# Research Forge paper-authoring pipeline

Research Forge owns the runtime paper workflow. External Codex skills may inform its design, but the project does not require an installed writing skill at runtime.

Stage 4 does not disguise missing evidence as a finished paper. It first
freezes an `EvaluationTransparencyRegister` and runs an evidence-sufficiency
gate. A material experimental gap creates an immutable diagnosis and
scientific-successor request, then redirects the Study to Stage 3 for
owner-approved evidence backfill. See
[Stage 4 evidence transparency and backfill](stage4-evaluation-transparency-and-backfill.md).

## Canonical order

1. Freeze the submission genre independently from the evidence.
2. Build an evidence–claim map from project artifacts and verified literature.
3. Generate a hierarchical outline before prose.
4. Review the outline with the Feynman-, Tukey-, Shannon-, and Popper-inspired panel.
5. Draft against the approved outline and explicit figure/table slots.
6. Run deterministic citation, number, conclusion, artifact, and genre checks.
7. Review and revise the draft; halt on insufficient context.
8. Apply claim-preserving academic prose editing with automatic revert if numbers, citations, artifact slots, or the frozen conclusion change.
9. Render headings, numbering, references, figures, tables, and LaTeX locally.
10. Repeat integrity and depth gates before PDF compilation.

## Figures and tables

Conceptual figures must use the next-ai-draw-io governed source path, with
draw.io Desktop performing the final local vector export. Research Forge writes
an editable `.drawio` source and records both the source and exported artifact
in the manifest. Mermaid or another diagram DSL is never typeset as manuscript
prose. Numeric plots continue to use deterministic native rendering when that
is the more faithful representation of frozen values.

The next-ai-draw-io editing bridge is mandatory for architecture, process,
mind-map, qualitative-panel, and graphical-abstract figures. The model may
propose only a typed node/edge plan; Research Forge owns XML generation and
audits any browser edits before the source can re-enter publication assets.
Missing editor provenance or export capability blocks Stage 4 rather than
falling back to an untracked native diagram. See
[next-ai-drawio.md](next-ai-drawio.md).

Markdown tables require an explicit `Table: ...` caption. The LaTeX renderer assigns the table number, uses ragged-right `tabularx` columns, reduces padding and font size, and rejects uncaptained tables. This prevents anonymous tables and pathological word splitting in narrow fixed-width columns.

## Abstract contract

The canonical research manuscript uses an unstructured abstract unless an explicit venue profile requires a structured abstract. Its abstract must be exactly one prose paragraph. `Background:`, `Methods:`, `Results:`, `Conclusion:` and equivalent Chinese labels are internal semantic moves, not rendered headings. Keywords are metadata outside the abstract paragraph.

A venue adapter may later create a structured abstract for a journal that explicitly requires one. That derivative layout must not replace or rewrite the canonical scientific manuscript.

## Existing reviewed manuscripts

Migrate a reviewed Markdown draft into the canonical form with:

```powershell
research-forge manuscript canonicalize manuscript.rev6.md `
  --output manuscript.rev7.md `
  --language en
```

The command removes internal block/status scaffolds, normalizes the abstract, splits declarations into canonical sections, expands compact citation clusters, moves references to the end, produces LaTeX, and emits a hash-bound audit report. It does not rewrite scientific claims.

Compile only after the canonicalization and depth gates pass:

```powershell
research-forge manuscript finalize-pdf manuscript.rev7.tex `
  --engine pdflatex `
  --language en `
  --output-dir canonical_submission
```

Use XeLaTeX for manuscripts that intentionally retain Unicode or CJK glyphs not mapped by the local renderer.

## Failure ownership

- Missing or invalid evidence–claim binding: `evidence_packaging`
- Missing or unverified literature grounding: `literature_grounding`
- Outline, prose, genre, or typesetting contract failure: `paper_writer`
- Scientific review abstention caused by missing context: halt and roll back to the earliest supplying stage

The publication-ready state remains separate from a paper-draft-ready state and still requires author metadata, venue compliance, and explicit submission approval.
