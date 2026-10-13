# Offline stakeholder demonstration (no credentials)

**Duration:** about 10 minutes. **Prerequisite:** `python src/run.py` is running; nothing else is needed.
Everything on screen is labelled **SIMULATED DEMO MODE**: the workflow, tools, moderation, decisions, revisions and
reports are real; the critics' proposed findings come from deterministic fixtures matched against the extracted architecture.

## Script
| # | Action | What to point out |
| --- | --- | --- |
| 1 | Open the **Dashboard** | totals, risk by domain, "SIMULATED" notice, health |
| 2 | **New Review** -> keep *Offline demo mode* on -> pick **ShopSphere - AWS e-commerce platform** -> **Load this scenario** | four files (architecture.md, diagram.puml, infrastructure.tf, incident notes), parse status, document roles |
| 3 | **Start Review** | review id returned immediately; redirect to **Review Execution** |
| 4 | Watch the six agents | PENDING -> RUNNING -> COMPLETED, finding counts, tool executions, moderator status, timeline, the LangGraph diagram. All six start together (parallel fan-out) |
| 5 | **Findings Explorer** -> filter *Agent = security* -> open *Orders database endpoint is directly reachable...* | evidence (file + line + resource), tool observation (observed vs expected), attack scenario, principle origin (organizational standard), merged from Security + Compliance |
| 6 | Open a Resilience finding (*single availability zone*) | CONFIRMED vs INFERRED; link to the stated 99.95% / RPO 5 min requirement |
| 7 | **Accept** *Automated database backups are disabled* | status ACCEPTED - not remediated |
| 8 | **Dispute** *Load balancer accepts plaintext HTTP...* with a justification such as "TLS terminates on 443; port 80 only redirects at the CDN" | status DISPUTED |
| 9 | **Defer** *Very large database instance class...* (justification, owner, review date) | ADR draft generated; status DEFERRED (not resolved) |
| 10 | Press **Submit decisions** | review goes REBUTTING -> the Security critic rebuts, the moderator adjusts severity MEDIUM -> LOW; open the finding to see architect -> critic -> moderator exchange |
| 11 | *Revise architecture* -> tick **Apply predefined demo revision** | impact analysis: changed artifact, changed components, affected agents with reasons, confidence HIGH. Note that **Performance is not scheduled** |
| 12 | **Run targeted re-review** | only affected critics run; Performance shows SKIPPED ("Unaffected by architecture revision 2"); fixed findings become RESOLVED with history; a new concern (cross-region replication) appears |
| 13 | **Reports** | updated scorecard (confirmed-only vs including unverified, coverage), outstanding vs accepted risks, ADRs, evaluation harness (observed precision/recall on fixtures) |
| 14 | **Finalize review** then download the **PDF** and **JSON** | status `COMPLETED_WITH_OPEN_RISKS`: unresolved risks remain, no approval is granted |

## Things worth showing
* **Moderator diagnostics**: one fixture claim cites no evidence (*Database will saturate at about 200 requests per second*) and is marked REQUIRES_VALIDATION; another references a component that does not exist (*Order pods lack readiness probes*) and is REJECTED but retained with an explanation.
* **Deduplication**: several groups of findings are merged with all contributing critics preserved.
* **Offline vs live**: the same graph/services/reports run in both modes; only the provider differs.

## The other samples
* **PayFlow (microservices-k8s)**: Draw.io diagram + Kubernetes manifests; privileged pods, hard-coded credentials, single replicas, missing probes/limits, dangling Ingress route.
* **LedgerBridge (hybrid-onprem)**: PlantUML + CloudFormation + incident report; single VPN, open Oracle listener, public bucket, EU residency conflict, silent batch failure.

## Live (Bedrock) version
For a live-model run that shows every architect action, rebuttal rounds and the human-input guardrails, use
[live-demo-script.md](live-demo-script.md).

## Reset
With the app stopped, run `python src/scripts/demo_reset.py`. It clears `data/` (keeping the `.gitkeep` files) and checks the
demo fixtures and provider configuration. See [scripts/README.md](../src/scripts/README.md).
