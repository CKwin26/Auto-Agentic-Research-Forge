# Correctly blocked Research Contract case

Run from the Research Forge repository root:

```powershell
python examples/public-blocked-contract-case/verify.py
```

The case deliberately omits an executable metric formula and authorized data
resource, leaves the target and sampling rules undefined, and makes baseline
and treatment operationally identical.  The expected outcome is a structured
Stage 2 block with no Run Specification and no scientific Verdict.  A pass
means the platform refused to turn incomplete scientific prose into an
experiment; it does not mean the hypothesis was refuted.
