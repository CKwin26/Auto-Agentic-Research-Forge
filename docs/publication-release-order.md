# Publication release order

This is a system invariant, enforced in code, CLI, and PDF compilation rather
than being an instruction for an agent to remember.

1. **Freeze and run the experiment.** The protocol, shared-artifact pair
   audit, protected evaluator outputs, and frozen evidence bindings must be
   complete.
2. **Blind scientific and fixed-venue readiness review.** The selected venue is
   fixed; the readiness report must reach at least 0.60 and contain zero
   automated critical/high blockers. Acceptance estimates are descriptive only
   and never gate inputs.
3. **Layout, PDF, and review submission package.** Only a passing step 2 can
   authorize typeset sources, PDF compilation, or a package for review.

The readiness report is additionally bound to the exact Markdown manuscript
hash and the project's fixed-venue contract. Editing the evidence manuscript
after review requires a fresh readiness run; a report from another project
cannot unlock this project.

`HUMAN_GATE_PENDING` is not silently erased. It permits a readable package for
the preregistered human audit, but still forbids external submission until the
actual human gate completes. Therefore `review_submission_package_allowed` and
`external_submission_allowed` are intentionally separate fields.

Enforcement locations:

- `research_forge.publication_synthesis.publication_layout_gate`
- `research_forge.publication_synthesis.render_publication_layout`
- `study publication-layout-gate <project> --readiness <report>`
- `manuscript finalize-pdf` for a canonical
  `synthesis/publication_manuscript.tex`
