# Research Forge

Research Forge is a local-first control plane for computational AI-for-science projects. It helps turn a research question or an existing codebase into a **traceable experiment-to-paper workflow**—without treating an LLM as the authority on evidence.

Its operating rule is simple: **Codex proposes; deterministic code owns truth, state, metrics, and side effects.** Codex is the default reasoning backend, but it never gets to rewrite protected evaluation, promote an experiment, or declare a paper ready by itself.

## What it is for

Research Forge is for ML, AI-agent, data-science, and computational-research projects where you want the entire chain to remain inspectable:

1. **Discovery** — retrieve, screen, and freeze a source-backed literature and novelty boundary.
2. **Protocol** — define a baseline, metrics, budget, repetitions, and a protected evaluation contract before experimentation.
3. **Experimentation** — generate bounded candidate changes, run them in a controlled environment, and preserve valid/invalid evidence and code lineage.
4. **Synthesis** — produce claim-bound drafts, audits, localization outputs, and publication-readiness decisions from the frozen evidence.

It also includes RF-Bench for comparing research-agent controllers under fixed tasks, seeds, budgets, and protected evaluators.

## What it deliberately does not claim

- It is **not** a one-click paper generator or an autonomous scientist.
- A completed run is not a peer-reviewed publication, and a readiness score is not a probability of acceptance.
- The default local runtime is for trusted development. Docker isolation is the unattended experiment boundary; neither replaces a real external benchmark or human scientific judgment.
- Human review is a first-class gate for source approval and any real publication decision.

## Public default workflow

Start with a new idea, or inspect an existing project read-only. In both routes, writing comes after a protocol and evidence are bound.

```text
idea / existing project
        │
        ▼
source-backed discovery ──► frozen protocol ──► protected experiments ──► evidence-bound draft
        │                         │                       │                       │
        └── human approval         └── hard design gates   └── valid/invalid ledger └── audit + readiness gate
```

The publication route is intentionally strict. Before a publication-intent experiment runs, it freezes a target venue contract and blocks known scientific failures such as non-isolated counterfactuals, dependent measurement, insufficient evidence breadth, and missing novelty refresh. A `pilot` route exists only as an explicitly labeled internal exception and can never be relabeled as a publication result.

## Quick start

```powershell
& "C:\Users\austa\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe main.py doctor

$py = ".\.venv\Scripts\python.exe"
& $py main.py init --name "stereo-ablation" --idea "Test whether geometry-aware augmentation improves transparent-object stereo depth"
& $py main.py literature plan stereo-ablation --focus "Transparent-object stereo depth and strong public baselines"
& $py main.py literature discover stereo-ablation --rows-per-query 20 --include 12 --from-year 2020
& $py main.py literature screen stereo-ablation --max-candidates 40 --include 12
& $py main.py literature synthesize stereo-ablation
& $py main.py literature audit stereo-ablation
```

Read the resulting literature review, explicitly approve its exact ID, then create and freeze the experiment contract. The complete command sequence is below in [A complete first loop](#a-complete-first-loop).

## Reproducible reference material

The repository contains reference fixtures and archived, hash-bound records to demonstrate the gates—not as proof that every future project is publication-ready:

- [Four-stage contract](docs/four-stage-closed-loop.md) and [productization contract](docs/productization-contract.md)
- [Project-bundle workflow](docs/project-bundle-workflow.md) for an existing code or text project
- [RF-Bench guide](docs/benchmark.md) and [controlled ML environment](docs/controlled-ml-environment.md)
- [Second independent case-study scaffold](case_studies/sick-lexical-replication-v1/README.md), which is intentionally labeled non-publication-ready until its isolation and literature gates are met
- [Historical Stage 2 record](docs/stage2-protected-evaluation-2026-07-18.md), which remains pipeline-complete but not publication-ready because human validation was deferred

## Core guarantees

| Guarantee | How it is enforced |
|---|---|
| Model output cannot become evidence by itself | Structured outputs are bound to frozen run/source IDs; protected code checks numeric claims. |
| Experiment state is auditable | Evidence, events, failures, and promotions are append-only and hash-bound. |
| Candidate execution is bounded | Python owns run budget, timeout, repeats, accepted files, parameter contract, and evaluator boundary. |
| Known design failures are repaired upstream | Scientific hard-gate failures create a root-cause repair artifact, enforcement rule, and regression test target. |
| Long prompts do not become hidden state | Prompt envelopes have explicit size and fragment limits; durable state lives in artifacts. |
| Reviewer diversity is supplementary | Distilled scientific-review personas are isolated, persisted as separate opinions, and aggregated by deterministic veto/abstention rules; they do not replace human validation. |

For the detailed ownership split, installation, backend settings, and operating commands, continue below.

## What is hard-coded vs agentic

| Concern | Owner | Enforcement |
|---|---|---|
| Research framing and next-experiment idea | Codex | Read-only ephemeral thread plus strict JSON Schema |
| Search-query design, relevance judgment, and related-work synthesis | Codex | Bounded records only; exact candidate/source IDs; no network or file writes |
| Scholarly retrieval and canonical metadata verification | Python | Crossref and Semantic Scholar adapters, raw-response hashes, deduplication, quotas, and hard exclusions |
| Literature acceptance and novelty gate | Human + Python | Exact review-ID confirmation; approval is bound to review, screening, and source hashes |
| Stage graph and human gates | Python | Legal transition table |
| Command, timeout, repeats, total budget | Python | Frozen execution contract |
| File access | Python | `experiment/` containment, suffix and size allow-list |
| Experiment process | Python | Per-run copy plus local or Docker runtime; no shell |
| Secrets | Python | API-key and secret-like environment variables removed from child process |
| Metrics and comparison | Python | Required numeric finite values and fixed direction/minimum delta |
| Scientific memory | Python | Append-only evidence ledger; failures and negative results remain |
| Code lineage | Python | Manual mode requires run-ID confirmation; run-loop auto-promotes only evaluator-verified improvements |
| Source registry | Python | Explicit verification attestation plus hashes frozen into the contract manifest |
| Numerical paper claims | Python | Structured claim values must exactly match valid run records |
| Completion vs publication readiness | Python | Pipeline closure and publication blockers are reported separately |
| Review root causes and claim ceiling | Python | Measurement dependence, counterfactual isolation, construct coverage, maturity, reporting, and telemetry are checked before routing |
| Manuscript depth and official PDF finalization | Python | Journal/short-report profiles enforce total and section depth, paragraph/subsection coverage, citations, numeric grounding, references, and duplicate-paragraph limits; compilation occurs in staging and publishes only with a hash-bound manifest |
| Publication target and 60% readiness contract | Python | Venue-first nine-dimensional score plus hard scientific vetoes; readiness and acceptance probability are stored and reported separately |

In the intended system budget, roughly 65–75% of behavior is deterministic orchestration and validation, 20–25% is model reasoning/prompts, and 5–10% is CLI/configuration. Safety, budgets, metrics, state, and evidence are 100% code-owned.

## Install

PowerShell:

```powershell
& "C:\Users\austa\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe main.py doctor
```

The default Codex backend reuses the local ChatGPT/Codex login and explicitly blanks API-key variables in the Codex child process. It does not read `.env.local`. The ignored `.env.local` is used only when the optional API backend is selected.

## Academic web interface

The local interface is one academic work page with a two-option business selector. `I already have a project` is the default mode; the user can switch the same work area to `idea to paper` without navigating to another page:

- **I have an idea** creates a durable idea-to-paper task and starts it at the evidence-bound discovery stage. The UI reports the real next gate instead of treating task creation as scientific validation.
- **I already have a project** reads a local project folder, converges one paper-worthy contribution, runs the four-stage bundle loop, and separates idea verdict, paper readiness, internal audit, and publication readiness.

```powershell
Set-Location .\research-forge-ui
pnpm install
pnpm build
Set-Location ..
& .\.venv\Scripts\python.exe main.py web --open
```

The server binds to `127.0.0.1:8765` by default and serves the built React interface plus both local APIs. Source-project inspection is read-only; project-bundle runs are written under `bundle_runs\`, while idea intakes are written under `idea_runs\`.

## Model backend

Codex is the default; no environment variable is required:

```powershell
& .\.venv\Scripts\python.exe main.py doctor
```

Planning and proposal turns use the Python Codex SDK with a read-only sandbox, denied approval escalation, strict output schema, and ephemeral threads. The beta SDK currently pins a Codex runtime that is compatible with `gpt-5.4`, so Research Forge uses that explicit default instead of inheriting a potentially newer local default. After upgrading the SDK/runtime, override it when desired:

```powershell
$env:RESEARCH_FORGE_CODEX_MODEL = "gpt-5.6"
```

The former Agents SDK path remains an explicit fallback. It is never selected automatically after a Codex failure:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[api]"
$env:RESEARCH_FORGE_BACKEND = "api"
$env:AUTORESEARCH_MODEL = "gpt-5.6-terra"
```

Only this fallback loads `OPENAI_API_KEY` from `.env.local`.

## A complete first loop

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py main.py init --name "stereo-ablation" --idea "Test whether geometry-aware augmentation improves transparent-object stereo depth"

& $py main.py literature plan stereo-ablation `
  --focus "Transparent-object stereo depth, geometry-aware augmentation, and strong public baselines"

& $py main.py literature discover stereo-ablation `
  --rows-per-query 20 `
  --include 12 `
  --from-year 2020

& $py main.py literature screen stereo-ablation `
  --max-candidates 40 `
  --include 12

& $py main.py literature synthesize stereo-ablation
& $py main.py literature audit stereo-ablation

# Read literature/review.md, then copy its exact review ID into the approval command.
& $py main.py literature approve stereo-ablation `
  --confirm "REVIEW_ID" `
  --novelty "NOVELTY_ID" `
  --note "Shortlist and bounded novelty map reviewed by the operator."

& $py main.py plan stereo-ablation `
  --message "Use the approved novelty map, one public dataset, one GPU, and a 24-hour first-study budget."

& $py main.py literature audit stereo-ablation

& $py main.py configure stereo-ablation `
  --primary score `
  --direction maximize `
  --entrypoint run_experiment.py `
  --timeout 3600 `
  --max-runs 30 `
  --required-repeats 1 `
  --max-repeats 3

& $py main.py freeze stereo-ablation
& $py main.py baseline stereo-ablation
& $py main.py propose stereo-ablation --focus "Choose the smallest high-information ablation."
& $py main.py run stereo-ablation --proposal latest
& $py main.py status stereo-ablation
& $py main.py report stereo-ablation
```

## Existing project bundle to working paper

Inspect without changing the source project:

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py main.py bundle inspect "C:\path\to\existing-project"
```

Close the four-stage evidence loop for the highest-ranked declared research track or, when none is complete, the automatically derived project-material track:

```powershell
& $py main.py bundle close-loop "C:\path\to\existing-project" `
  --output-root bundle_runs `
  --track auto
```

The command does not copy `.env*`, credentials, private keys, build directories, virtual environments, or `node_modules`. It produces separate stage manifests, a frozen scope, a protocol/evidence boundary lock, an idea verdict, a claim registry, a working manuscript, a paper-expansion decision, an audit, and a hash-bound completion certificate. A completed working-paper loop is not automatically a usable full paper: derived materials without an exact experiment binding, retrospective evidence, mixed/inconclusive results, insufficient bound numeric evidence, and missing frozen literature remain explicit blockers.

The full-paper writer is a second, separately certified action. It unlocks only after a clean prospective idea verdict, exact protocol-output binding, at least 15 bound numeric results, and a manifest binding at least 15 explicitly verified paper records under `literature/sources/`:

```powershell
& $py main.py bundle prepare-paper "C:\path\to\bundle_runs\RUN_ID"
& $py main.py bundle expand-paper "C:\path\to\bundle_runs\RUN_ID"
& $py main.py bundle audit-paper "C:\path\to\bundle_runs\RUN_ID"
```

The writer returns structured prose, while the local renderer owns the bibliography and frozen numeric table. A separate audit checks 10,000-Chinese-character journal depth, citation resolution, numeric preservation, and verbatim conclusion binding. Passing this gate means a usable complete draft, not journal acceptance or publication approval.

The Stage 1 workflow is required for scientific projects. `source add` remains available for manually verified datasets, software, specifications, or supplemental papers, but unapproved records are not shown to the research planner and are not frozen into the scientific evidence set. See [the Stage 1 literature workflow](docs/stage1-literature-workflow.md).

If a terminal or machine stops during a run, `status` retains the active run ID. After confirming that the process is no longer running, close the interrupted attempt explicitly; it is recorded as invalid evidence rather than silently deleted:

```powershell
& $py main.py recover stereo-ablation --confirm ACTIVE_RUN_ID
```

If a candidate is valid and improves over the current best by more than `min_delta`, inspect its run directory and promote it explicitly:

```powershell
& $py main.py promote stereo-ablation RUN_ID --confirm RUN_ID
& $py main.py synthesize stereo-ablation
& $py main.py audit-synthesis stereo-ablation
& $py main.py complete stereo-ablation
```

The repeated run ID is the human confirmation gate. Promotion copies only changed experiment files and the accepted parameter state. A pre-promotion snapshot remains under `lineage/`.

## Simplified-Chinese manuscript localization

After synthesis, generate a terminology plan and a derived `zh-CN` Markdown manuscript without
changing `synthesis/manuscript.md`, the claim registry, the synthesis audit, or the completion
certificate:

```powershell
& $py main.py terminology prepare stereo-ablation --language zh-CN
& $py main.py localize stereo-ablation --language zh-CN
& $py main.py audit-localization stereo-ablation --language zh-CN
```

Outputs are written under `synthesis/localized/zh-CN/`. The frozen `term-plan.json` records the
source-manuscript hash, termbase hashes, contextual sense decisions, and provisional model choices.
`manuscript.md`, `manifest.json`, and `audit.json` preserve block-level lineage and check protected
numbers, citations, identifiers, Markdown headings, code blocks, and terminology consistency.

Unknown terms do not block localization. They remain `provisional_model_choice` entries and are copied
to `term-review.yaml`; they are never added to the persistent project termbase automatically. To keep
an edited batch of approved translations for future runs, mark selected items `approved` and import it:

```powershell
& $py main.py terminology import stereo-ablation `
  --language zh-CN `
  --file "workspaces\stereo-ablation\synthesis\localized\zh-CN\term-review.yaml"
```

Bundled academic-writing, machine-learning, and AI-agent terminology is versioned with the package.
Human-approved project entries live in `terminology/project.zh-CN.yaml` and take precedence over the
bundled packs. A changed English manuscript or termbase invalidates the old plan, and `localize`
automatically prepares a fresh one before generating the next derived manuscript.

## Docker-isolated automatic loop

Unattended operation uses two separate containers per trial. The candidate container receives only the experiment copy, parameter file, agent-visible `data/`, and one writable output directory. The evaluator container receives the protected evaluator, the candidate submission as a read-only file, and a different writable metrics directory. Neither container has network access; both use a read-only root filesystem, dropped Linux capabilities, `no-new-privileges`, and fixed CPU, memory, PID, timeout, temporary-storage, and accepted-file size limits.

Install and start Docker first, then build and verify the frozen CPU ML environment:

```powershell
& .\.venv\Scripts\python.exe main.py benchmark build-env
& .\.venv\Scripts\python.exe main.py benchmark doctor
```

`rf-airs-cpu:v1` pins the base-image digest plus exact versions and wheel hashes for NumPy, SciPy, pandas, scikit-learn, and all transitive dependencies. Doctor performs real imports and ML smoke tests inside the same isolated boundary used for experiments. The verified package map is recorded in each run and supplied to Codex as a hard capability manifest.

Run the deterministic calibration loop:

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py main.py benchmark run-loop rf-quadratic-max `
  --strategy grid `
  --runtime docker `
  --seeds 0,1,2 `
  --iterations 3
```

Run the real Codex proposal path on an activated AIRS-lite task pack:

```powershell
& $py main.py benchmark run-loop "PATH_TO_TASK_PACK" `
  --strategy codex `
  --runtime docker `
  --seeds 0,1,2 `
  --iterations 10 `
  --candidate-pool-size 3 `
  --proposal-attempts-per-iteration 6 `
  --patience 5
```

The controller, not Codex, diagnoses the evidence ledger, rejects equivalent target states, fills a three-candidate pool with contract-valid proposals bound to the current canonical-state fingerprint, ranks the pool by information value, executes one candidate, validates metrics, auto-promotes only a valid improvement, and stops on target, iteration budget, invalid-run budget, proposal-space exhaustion, or non-improvement patience. After promotion it recomputes every pending target against the new canonical state: invariant targets are revalidated, while targets that drift are discarded before the pool is refilled. It persists `loop_state.json` and `loop_events.jsonl` before side effects. If the process stops between selection, execution, and promotion, `--resume OUTPUT_DIRECTORY` reconciles or recovers the recorded run instead of silently repeating it.

For development without Docker, pass `--runtime local`. Such reports remain `publishable: false` even when every artifact-integrity check passes.

## Experiment interface

The configured Python entrypoint receives:

```text
--params  <JSON file containing the canonical parameters plus proposal overrides>
--metrics <path where the experiment must write a JSON object>
```

It may read `AUTORESEARCH_PROJECT_DIR` and `AUTORESEARCH_RUN_DIR`. The metrics file must contain the frozen primary metric and every frozen required metric as finite numbers. Stdout text is never parsed as scientific evidence.

The generated `run_experiment.py` is only a smoke-test fixture. Replace it with your real train/evaluate wrapper before freezing the first real project.

## Project artifacts

```text
workspaces/<project>/
  project.json
  state.json
  research_contract.json       # immutable after freeze
  execution_contract.json      # immutable after freeze
  literature_manifest.json     # immutable source registry digest
  frozen_manifest.json
  current_parameters.json
  experiment/                  # canonical promoted implementation
  literature/search_plans/     # bounded Codex-generated query strategies
  literature/discoveries/      # normalized candidates plus hashed raw API responses
  literature/screening.json    # exact one-decision-per-candidate screening record
  literature/review.json       # typed related-work and novelty map
  literature/review.md         # human-readable review surface
  literature/approval.json     # exact review-ID human gate
  literature/stage1_manifest.json # hashes the complete approved Stage 1 chain
  literature/sources/          # verified source metadata selected by Stage 1
  plans/                       # research contract drafts plus evidence bindings
  plan_evidence_binding.json   # latest plan -> review hash -> approved source IDs
  proposals/
  runs/<run-id>/               # copied code, trials, logs, metrics, record
  evidence.jsonl               # append-only scientific ledger
  lineage.jsonl                # append-only promotion ledger
  events.jsonl                 # state transition ledger
  report.md
  synthesis/claims.json        # structured claim-to-run/source bindings
  synthesis/manuscript.md      # deterministic evidence-bound draft
  synthesis/audit.json         # integrity and publication-readiness checks
  completion_certificate.json  # hashes the completed four-stage artifact set
```

## Current boundary

This is a runnable MVP, not yet a universal autonomous scientist. Stage 1 now queries real Crossref and Semantic Scholar metadata, verifies canonical identifiers, records raw responses, performs bounded semantic screening, and creates a source-bound novelty map. It does not treat metadata/abstract review as full-paper review or proof of novelty, and it cannot approve its own shortlist. It deliberately does not let an agent install packages, launch an application-owned shell, change evaluators, or mutate literature records directly. The next safe expansions are full-text acquisition and extraction, dataset adapters, GPU/process monitoring, statistical comparison policies, and bounded prose generation whose claims remain subordinate to the claim registry.

The local Windows runtime is **not an OS sandbox**. Use it only for trusted fixtures or development. The Docker runtime is the unattended boundary for tasks with a separate evaluator, but it is still a local benchmark harness rather than AIRS's official leaderboard container protocol. The state store is designed for one local CLI process at a time and must not be run concurrently; it does not yet use a cross-process file lock. Post-run consistency gates reject proposal-journal/file mismatches if an external launcher violates this rule.

## RF-Bench quick start

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py main.py benchmark list
& $py main.py benchmark run rf-quadratic-max --strategy grid --seed 0 --seed 1 --seed 2 --iterations 3
& $py main.py benchmark run rf-quadratic-max --strategy codex --seed 0 --iterations 3
& $py main.py benchmark run-loop rf-quadratic-max --strategy grid --runtime local --seeds 0,1,2 --iterations 3
```

The grid strategy calibrates the harness without an LLM. The Codex strategy exercises the real proposal path. Reports without an enforced container boundary are correctly marked non-publishable even when all local integrity checks pass.

See [docs/design-notes.md](docs/design-notes.md) for the patterns borrowed from other research agents and [docs/prompt.md](docs/prompt.md) for the prompt/control split.
