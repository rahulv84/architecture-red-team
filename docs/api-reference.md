# API reference

Base URL: `http://127.0.0.1:8000/api/v1` - interactive docs at `/docs` (Swagger) and `/openapi.json`.
Errors share one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.

| Status | Meaning |
| --- | --- |
| 400/401/429/502/504 | provider configuration, authentication, rate limit, upstream failure, timeout |
| 404 | unknown project/review/document/finding/report |
| 409 | state conflict (review not `AWAITING_HUMAN`, already running, dispute limit, max rounds) |
| 413 / 415 | file too large / unsupported or invalid file |
| 422 | validation failure (request body, role, justification length, agent ids) |

## Platform
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | status, version, snapshot restore info |
| GET | `/config` | public settings, tool availability, notices (no secrets) |
| GET | `/providers` | catalog with `configured`, `vision`, `simulated` |
| POST | `/providers/validate` | `{"provider": "anthropic", "check_connectivity": false}` |
| GET / PUT | `/settings` | PUT accepts the editable fields plus write-only `anthropic_api_key` / `openai_api_key` / `aws_bearer_token_bedrock`; GET reports `*_api_key_configured` flags only |
| GET | `/settings/tools` | built-in and Checkov availability |

## Projects, documents, demo
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/projects` | `{"name", "description", "cloud_environment"}` |
| GET | `/projects`, `/projects/{id}` | list with counts / detail with documents and reviews |
| POST | `/projects/{id}/documents` | multipart: repeated `files`, aligned repeated `roles` |
| GET | `/projects/{id}/documents`, `/projects/{id}/manifest` | active documents / ingestion manifest (sha256, type, role, status) |
| GET | `/documents/supported` | supported extensions and roles |
| GET | `/documents/{id}`, `/documents/{id}/content` | metadata / parsed text, sections, nodes, edges, resources |
| GET | `/demo/scenarios` | offline scenarios |
| POST | `/demo/scenarios/{id}/project` | creates a project preloaded with the scenario files |

```bash
curl -F "files=@infrastructure.tf" -F "roles=INFRASTRUCTURE" \
     -F "files=@architecture.md" -F "roles=PRIMARY_ARCHITECTURE" \
     http://127.0.0.1:8000/api/v1/projects/prj-1234567890/documents
```
```json
{"accepted": [{"artifact_id": "art-ab12", "filename": "infrastructure.tf", "sha256": "...", "detected_type": "terraform",
               "role": "INFRASTRUCTURE", "parse_status": "PARSED", "parse_warnings": []}],
 "rejected": [{"filename": "evil.exe", "error": "Unsupported file type '.exe'...", "code": "unsupported_file"}]}
```

## Reviews
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/reviews` | `ReviewCreate`; `demo_mode=true` forces the mock provider |
| GET | `/reviews`, `/reviews/{id}` | list with severity/domain counts / detail |
| POST | `/reviews/{id}/start` | 202; runs in the background |
| POST | `/reviews/{id}/cancel` | cooperative cancellation |
| GET | `/reviews/{id}/status` | real backend state: phase, round, agents, moderator, counts, `can_re_review`, `can_finalize` |
| GET | `/reviews/{id}/events?after=<seq>` | execution timeline (cursor by `seq`) |
| GET | `/reviews/{id}/agents` | agent executions, tool summaries, tool results |
| GET | `/reviews/{id}/architecture?revision=` | architecture model + source documents |
| GET | `/reviews/{id}/tool-results` | deterministic observations |
| GET | `/reviews/{id}/score`, `/evaluation`, `/graph` | scorecard, evaluation harness, graph topology + checkpoint state |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/reviews -H "Content-Type: application/json" \
  -d '{"project_id":"prj-1234567890","name":"ShopSphere","demo_mode":true,"scenario_id":"aws-ecommerce"}'
```
```json
{"review_id": "rev-928ebe25b6", "status": "CREATED", "provider": "mock", "model_id": "mock-deterministic",
 "enabled_agents": ["security","resilience","finops","compliance","performance","operability"], "max_rounds": 3}
```
Status excerpt:
```json
{"status": "AWAITING_HUMAN", "phase": "Awaiting architect decisions", "current_round": 1, "simulated": true,
 "agents": [{"agent_id": "security", "status": "COMPLETED", "finding_count": 7, "tool_count": 8, "tool_violations": 8}],
 "moderator": {"status": "COMPLETED", "summary": "Moderated 30 candidate finding(s) into 26 distinct finding(s) ..."},
 "pending_decisions": 0, "open_critical_high": 10, "can_re_review": false, "can_finalize": true}
```

## Findings
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/reviews/{id}/findings` | filters: `severity, agent, status, evidence_level, component, revision, moderator_status, include_rejected, q` |
| GET | `/findings/{id}` | finding + decisions + rebuttals + tool results + ADR ids |

## Human decisions and revisions
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/reviews/{id}/decisions` | `{"finding_id", "decision_type": "ACCEPT\|DISPUTE\|DEFER", "justification", "notes", "review_date", "risk_owner", "remediation_target"}` - only while `AWAITING_HUMAN` |
| GET | `/reviews/{id}/decisions?pending_only=` | decision history |
| POST | `/reviews/{id}/resume` | `{"action": "submit_decisions" \| "finalize"}` - resumes the interrupted graph (202) |
| POST | `/reviews/{id}/revisions` | multipart: `change_notes`, `use_demo_revision`, `files`, `roles`; returns the revision with `impact` |
| GET | `/reviews/{id}/revisions` | revisions with impact |
| POST | `/reviews/{id}/re-review` | `{"revision_id": null}` - targeted re-run of affected agents (202) |
| GET | `/reviews/{id}/history` | revisions, decisions, rebuttals, ADRs, impacts |

```json
POST /reviews/rev-1/decisions
{"finding_id": "fnd-1", "decision_type": "DEFER", "justification": "Waiting for load tests in Q3",
 "risk_owner": "Platform lead", "review_date": "2026-12-01", "remediation_target": "Q1 2027"}
```
Impact excerpt returned by `POST /revisions`:
```json
{"number": 2, "impact": {"affected_agents": ["security","resilience","finops","compliance","operability"],
  "confidence": "HIGH", "broadened": false, "changed_artifacts": ["infrastructure.tf"],
  "reasons": {"security": ["aws_db_instance.orders: publicly_accessible (network exposure / transport change)"]}}}
```

## Reports and ADRs
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/reviews/{id}/reports?formats=json,pdf` | generate files |
| GET | `/reviews/{id}/reports` | list |
| GET | `/reviews/{id}/report-preview` | structured `AssessmentReport` without writing files |
| GET | `/reports/{report_id}/download` | file download (`application/pdf` / `application/json`) |
| GET | `/reviews/{id}/adrs` | ADR drafts including Markdown |
