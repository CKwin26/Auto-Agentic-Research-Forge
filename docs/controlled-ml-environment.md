# Controlled ML environment

`rf-airs-cpu:v1` is the default Docker environment for automatic AIRS/RF-Bench loops. It turns the candidate runtime from an undocumented Python image into a verified experimental capability boundary.

## Frozen contents

- Base image: `python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`.
- Target platform: Linux AMD64, CPU only.
- Direct and transitive Python packages: NumPy, SciPy, pandas, scikit-learn, joblib, narwhals, python-dateutil, six, threadpoolctl, and tzdata.
- Every package has an exact version and Linux-wheel SHA-256 in `docker/airs-cpu/requirements.lock`.
- The CLI builds only `linux/amd64` and disables BuildKit's time-varying provenance attachment so repeated builds of unchanged inputs keep the same image manifest digest.
- Runtime installation is disabled with `PIP_NO_INDEX=1` and `PIP_REQUIRE_VIRTUALENV=1`.

This profile intentionally does not include PyTorch, Transformers, Jupyter, plotting libraries, or the Hugging Face `datasets` package. Dataset preparation stays outside the candidate runtime; activated AIRS-lite tasks expose frozen JSONL data instead.

## Build and verify

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py main.py benchmark build-env
& $py main.py benchmark doctor
```

The build runs `verify_runtime.py` once inside the image. `benchmark doctor` then runs the same verifier again in a fresh container with no network, a read-only root filesystem, all capabilities dropped, `no-new-privileges`, fixed CPU/memory/PID/tmpfs limits, and an unprivileged user. A controlled image is ready only if its labels, manifest, imports, exact installed versions, and NumPy/SciPy/pandas/scikit-learn smoke tests all agree.

Doctor reports three distinct facts:

- `docker_isolation_ready`: Docker can enforce the execution boundary;
- `controlled_ml_environment`: the image declares the Research Forge controlled profile;
- `ml_capabilities_verified`: the isolated capability probe passed.

The image ID and a canonical capability-manifest SHA-256 are copied into every run attestation. The audit rejects a controlled-environment claim without matching verification evidence. The run-loop also passes the verified package map to Codex and tells it not to assume or install unlisted dependencies.

## Real SICK replay

The TF-IDF + LinearSVC proposal that previously failed in `python:3.12-slim` because `scikit-learn` was absent was replayed in a fresh SICK project:

| Item | Accuracy | Repeats | Stddev | Integrity |
|---|---:|---:|---:|:---:|
| Constant-neutral baseline | 0.5686914 | 2 | 0.0 | pass |
| TF-IDF + LinearSVC | 0.6141459 | 2 | 0.0 | pass |

The final replay used the repeat-build-stable image ID `sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d` and capability-manifest SHA-256 `a2ad47c62d247ae9aa6c3fc573ec1e5aaf7f14325e5105813f64353d9af67dba`. Evidence is in `benchmark_runs/controlled-env-validation-20260717T024353Z-311ddd/validation.json`.

This is an environment validation, not the best SICK result: the promoted pure-standard-library Naive Bayes candidate remains higher at `0.6763147`.

## Replaying another proposal

```powershell
& $py scripts\validate_controlled_environment.py `
  "PATH_TO_TASK_PACK" `
  "PATH_TO_PROPOSAL_JSON" `
  --seed 0 `
  --repeats 2
```

The script materializes a fresh project, verifies the baseline, executes the proposal under `rf-airs-cpu:v1`, audits the full artifact chain, and writes `validation.json`. It never mutates the original project.
