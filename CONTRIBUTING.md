# Contributing to Research Forge

Research Forge accepts focused fixes and reusable product improvements. Keep
scientific authority, retrieval policy and immutable history explicit.

## Development setup

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,external-research,paper-evidence]"
pnpm --dir research-forge-ui install --frozen-lockfile
```

Run the checks used by CI:

```powershell
& .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
& .\.venv\Scripts\python.exe -m compileall -q research_forge/retrieval
pnpm --dir research-forge-ui run build
```

## Pull requests

- Open one focused PR per concern.
- Add tests for policy, sanitization, idempotency, immutable artifacts, retries
  and phase authority when changing retrieval.
- Keep provider networking inside `research_forge/retrieval/providers/`.
- Never commit generated runs, provider responses, credentials, PDFs, caches,
  screenshots or local configuration.
- Treat discovery and attention signals as suggestions, never scientific
  verdict evidence.
- Preserve legacy reads and historical frozen artifacts.

PR descriptions should explain the user impact, authority boundary and checks
run. A passing model-generated answer is not sufficient verification when a
deterministic invariant can be tested.
