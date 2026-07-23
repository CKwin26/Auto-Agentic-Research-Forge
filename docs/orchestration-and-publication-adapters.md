# Durable orchestration and publication adapters

Research Forge has one operation-neutral task layer and a separate publication
adapter boundary. These are process contracts, not new scientific claims.

## Durable tasks

CLI and Web bundle operations use `TaskOrchestrator`. A task record is written
before its handler runs, and every transition is appended to
`task_events.jsonl`. The record distinguishes `pending`, `running`,
`succeeded`, `failed`, and `blocked` states.

Operations declare whether an interrupted attempt is safe to resume. Read-only
inspection, idea intake, paper expansion, and deterministic audits are
resumable. `bundle.close` is intentionally not resumable yet because it creates
an immutable run directory and does not have stage-level checkpoints. A failed
close remains visible; the operator must inspect it rather than silently
repeating the side effect.

The compatibility CLI remains available. New automation can use:

```powershell
$payload = '{"source":"C:/research/project","discover_claims":true}'
python main.py task submit --operation bundle.inspect --payload $payload --run
python main.py task list
python main.py task inspect TASK_ID
python main.py task resume TASK_ID
```

The local Web API exposes `GET /api/tasks`, `GET /api/task?id=...`, and
`POST /api/tasks/resume`. Existing idea, inspect, close-loop, and expand-paper
routes execute through the same task layer and return their task record under
`_task` without removing their prior response fields.

## Publication adapters

Publication-specific evaluator, human-audit, synthesis, supplement, and package
code is selected by a frozen `publication_adapter.json`. `ProjectSpec` may set
`publication_adapter_id` and adapter settings; a project revision cannot swap
the frozen adapter in place.

The current research-agent paper is registered as
`research-agent-evidence-publication-v1`. It is a golden-case adapter, not a
core default for unrelated projects. Working-paper and benchmark projects do
not load it unless their `ProjectSpec` selects it.

Adapter actions are also exposed as durable operations, for example:

```powershell
$payload = '{"project":"C:/research/study","parameters":{}}'
python main.py task submit --operation publication.audit-synthesis --payload $payload --run
```

Legacy `study *publication*` commands remain as compatibility aliases and now
dispatch through the adapter registry instead of importing case modules
directly.
