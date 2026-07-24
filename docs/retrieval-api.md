# Retrieval Gateway CLI 与本地 API

## CLI

```text
research-forge retrieval readiness
research-forge retrieval policy show PROJECT_ID
research-forge retrieval policy set PROJECT_ID --mode academic_read \
  --provider semantic_scholar --provider crossref \
  --domain api.semanticscholar.org --domain api.crossref.org \
  --resource-type publication --approved-by OWNER
research-forge retrieval plan --project-id ... --study-id ... \
  --phase discovery --step-id ... --purpose related_work_search \
  --query "..." --provider semantic_scholar \
  --resource-type publication --usage-role background_source \
  --idempotency-key ...
research-forge retrieval run REQUEST_ID
research-forge retrieval status RUN_ID
research-forge retrieval resources --study-id STUDY_ID
research-forge retrieval coverage COVERAGE_ID
research-forge retrieval promote BINDING_ID ...
research-forge retrieval freeze RESOURCE_SET_ID
research-forge retrieval retry RUN_ID
research-forge retrieval corpus build ...
research-forge retrieval corpus query CORPUS_ID --question "..."
```

The original `policy-show`, `policy-set`, `corpus-build`, and `corpus-query`
forms remain supported as compatibility aliases.

## Web API

The External Research V1 resource API is:

- `GET /retrieval/readiness`
- `GET|PUT /projects/{project_id}/retrieval-policy`
- `POST /studies/{study_id}/retrieval-runs`
- `GET /retrieval-runs/{run_id}`
- `GET /retrieval-runs/{run_id}/coverage`
- `GET /studies/{study_id}/resources`
- `GET /resources/{resource_id}`
- `GET /resources/{resource_id}/relations`
- `POST /institution-sessions`
- `GET /institution-sessions/{session_id}`
- `POST /institution-sessions/{session_id}/reauthenticate`
- `DELETE /institution-sessions/{session_id}`
- `POST /corpora`
- `POST /corpora/{corpus_id}/index`
- `POST /corpora/{corpus_id}/query`

The local UI compatibility API also exposes:

GET:

- `/api/retrieval/policy?project_id=...`
- `/api/retrieval/status?run_id=...`
- `/api/retrieval/resources?study_id=...`
- `/api/retrieval/snapshots?resource_id=...`
- `/api/retrieval/bindings?study_id=...`
- `/api/retrieval/coverage?id=...`
- `/api/retrieval/resource-sets?study_id=...`

POST：

- `/api/retrieval/policy`
- `/api/retrieval/plan`
- `/api/retrieval/run`
- `/api/retrieval/promote`
- `/api/retrieval/freeze`
- `/api/retrieval/retry`

所有计划调用都要求现存 Project、Study 和 StepInstance，且 phase 必须与步骤
一致。错误沿用本地 API 的 JSON error 格式。接口不提供 arbitrary URL、curl、
通用 HTTP、下载执行或表单提交。

## Codex facade

`CodexRetrievalTools` 只暴露 plan/search publications/datasets/code、
resolve identifier、fetch metadata/approved resource、verify citation、
check retraction、coverage 和 promotion 意图。最终执行权限由 Policy Engine
决定，Codex 不接触 Provider 凭证。
