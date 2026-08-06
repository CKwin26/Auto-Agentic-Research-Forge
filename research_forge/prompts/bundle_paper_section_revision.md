Write only the bounded manuscript-section material requested for the sections explicitly listed in `open_sections`.

Rules:

1. Return exactly three fields: `results`, `discussion`, and `conclusion`. Each field is supplemental prose to append to that existing section, not a replacement.
2. Use the requested manuscript language and satisfy each supplied length target with substantive, evidence-bound academic prose.
3. Preserve the scientific claim boundary, uncertainty, unfavorable findings, numeric tokens, verified citation keys, and figure/table callouts across the opened sections as a group.
4. Results report observations, denominators, uncertainty, robustness, and failure cases. Discussion interprets them without inventing a new result. Conclusion directly answers the research question in one or two paragraphs and contains at most two numeric tokens.
5. Do not invent evidence, references, statistics, experiments, mechanisms, causal claims, authors, or venues.
6. Do not expose internal identifiers, hashes, workflow states, production commentary, or audit-report narration.
7. When the task requests additions, return additions rather than rewritten sections and obey any prohibition on new numerals, citation keys, or visual callouts.
8. Do not echo any other manuscript field, a bibliography, or Markdown headings around the returned JSON fields. Semantic `###` subsection headings inside Results or Discussion are allowed when required by the depth contract.

Return the bounded section revision only.
