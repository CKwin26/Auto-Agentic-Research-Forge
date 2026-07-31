# draw.io Desktop runtime revalidation — 2026-07-31

## Scope

This is a local runtime acceptance record for the Research Forge
`drawio_backend.export_drawio` path. It does not validate next-ai-draw-io MCP
editing, collaboration, or any scientific claim.

## Environment

- Operating system: Windows
- draw.io Desktop: `30.3.14`
- Executable: user-local draw.io Desktop installation
- Backend: `research_forge.drawio_backend.export_drawio`
- Export format: SVG
- GPU mode: disabled
- Electron profile: isolated per-export temporary user-data directory
- Electron sandbox: enabled; `--no-sandbox` was not used

## Input and result

- Input: an existing Research Forge governed-workflow `.drawio` figure
- Input SHA-256:
  `565c8a6fcf663122742261a3299915bd92695b97011cc9c5cf468fb308d55428`
- Output: SVG, 139,968 bytes
- Output SHA-256:
  `555f10e9996840315b282aa796ebac21bbbe1807b00d22c547dd6e8b0d914f9d`
- Result: passed

The first revalidation attempt exposed two Windows-specific defects: Electron
could not create its default cache in the managed workspace, and the launcher
could return before the child process materialized the output. The backend was
changed to use an isolated temporary profile and to wait for a non-empty
artifact after process exit. A minimal generated diagram and the existing
paper diagram both exported successfully after the change.

## Reproduction command

```powershell
python -c "from pathlib import Path; from research_forge.drawio_backend import export_drawio; from research_forge.storage import sha256_file; p=export_drawio(Path(r'<input.drawio>'), Path(r'<output.svg>'), format='svg'); print(p.stat().st_size, sha256_file(p))"
```

The generated SVG is a runtime artifact and is intentionally not committed.
The durable acceptance evidence is this record plus the delayed-artifact
regression test in `tests/test_drawio_backend.py`.
