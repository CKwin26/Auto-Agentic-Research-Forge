# Research Forge paper-authoring pipeline

Research Forge owns the runtime paper workflow. External Codex skills may inform its design, but the project does not require an installed writing skill at runtime.

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

Conceptual figures use draw.io Desktop as the default renderer. Research Forge writes an editable `.drawio` source, exports a publication image through the draw.io CLI, and records both paths in the artifact manifest. Mermaid or another diagram DSL is never typeset as manuscript prose. Numeric plots may continue to use deterministic native rendering when that is the more faithful representation of frozen values.

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
