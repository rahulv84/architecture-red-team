# Agent workflows

## Specialist agents
Every agent subclasses `src/backend/agents/base_agent.py::BaseAgent`, has a persona/rubric in
`src/knowledge/agent_rubrics/<agent>.yaml`, its own deterministic rules (`src/backend/tools/__init__.py::AGENT_RULES`),
a scoped component view, and a **domain brief** - a deterministic digest placed in the prompt.

| Agent | Persona (rubric) | Scope | Domain brief highlights |
| --- | --- | --- | --- |
| Security | Pragmatic cloud security architect who thinks like an attacker | entry points, compute, data stores, identity, network | entry points, boundary-crossing flows and their encryption, STRIDE prompts per component type, blast radius of data stores |
| Resilience | SRE who runs failure drills in their head | LBs, compute, stores, queues, NAT, external systems | stated RTO/RPO/availability, failure-impact table from the dependency graph, longest synchronous path, external dependencies |
| FinOps | Cloud financial-operations engineer who never invents prices | sizeable compute/storage/network cost drivers | cost-driver inventory (qualitative), boundary flows, regions, workload; "state data needed to estimate spend" |
| Compliance | GRC architect separating possible control gaps from conclusions | data stores, identity, logging, regions | obligations stated, personal-data candidates, residency, control themes mapped from tool rules |
| Performance | Performance engineer reading architectures as latency budgets | request path components, caches, queues | performance targets, longest synchronous chain, fan-in hotspots, caching, autoscaling hints |
| Operability | Operations lead who has been paged at 3 a.m. | compute, queues, monitoring, probes | tooling mentioned, probe coverage, async components needing failure handling, SLO statements |

### Per-agent process (reusable subgraph)
`select_rules -> run_tools -> scope_context -> retrieve_evidence -> llm_analysis -> validate_findings`
(`src/backend/graph/builder.py::build_specialist_subgraph`, nodes in `src/backend/graph/nodes.py::sp_*`).

1. **Rules** - `AGENT_RULES[agent]`.
2. **Tools** - built-in rules over parsed IaC/model (+ Checkov for security/compliance if installed; otherwise `NOT_EXECUTED`). Results use stable IDs `TR-<hash>` so overlapping critics produce identical IDs.
3. **Scope** - components of the agent's types plus those named by tool results.
4. **Retrieve** - TF-IDF over the knowledge base and uploaded standards/ADRs/incident documents, plus the top document sections ranked for the agent's query. Chunks carry an `origin`.
5. **Analyze** - system prompt (persona, rubric, evidence rules, safety rules) + user prompt (brief, previous findings, *untrusted evidence block*) -> `AgentReviewOutput` structured JSON.
6. **Validate** - evidence IDs are resolved to real `SourceReference`s; unknown IDs are dropped with a warning; CONFIRMED without evidence is downgraded; `principle_origin` is derived by checking the cited control ID against the knowledge base (the model's claim is not trusted); compliance findings get the "not a compliance determination" assumption.

### Shared output contract
`FindingDraft` (agent output) -> `Finding`:
`finding_id, review_id, agent_id, title, description, category, severity, confidence, evidence_level, affected_components,
source_references, scenario (attack/failure), impact, recommendation, principle, principle_origin, validation_method,
original_status, assumptions, tool_result_refs, reasoning_summary` plus moderation/lifecycle fields
(`status, moderator_status, moderator_notes, contributing_agents, merged_from, original_severities, fingerprints,
first/last_seen_revision, history`).

## Deterministic rules
| Rule | Domain | Condition | Agents |
| --- | --- | --- | --- |
| `SEC-NET-001` | security | Security group allows the world on non-web ports | security |
| `SEC-NET-002` | security | Database or cache is publicly accessible | security, compliance |
| `SEC-STO-001` | security | Object storage is publicly readable or writable | security, compliance |
| `SEC-ENC-001` | security | Storage resource is not encrypted at rest | security, compliance |
| `SEC-ENC-002` | security | Traffic is served without transport encryption | security |
| `SEC-SEC-001` | security | Credential literal committed in infrastructure definition | security, compliance |
| `SEC-IAM-001` | security | IAM policy grants wildcard actions or resources | security, compliance |
| `SEC-K8S-001` | security | Workload runs with elevated privileges | security |
| `SEC-K8S-002` | security | No NetworkPolicy isolates workloads | security |
| `SEC-API-001` | security | API method declared without authorization | security |
| `RES-HA-001` | resilience | Datastore deployed in a single availability zone | resilience |
| `RES-HA-002` | resilience | Workload runs as a single replica | resilience, performance |
| `RES-HA-003` | resilience | Container has no readiness or liveness probe | resilience, operability |
| `RES-HA-004` | resilience | Compute capacity is not spread or self-healing | resilience |
| `RES-BAK-001` | resilience | Automated backups are disabled | resilience |
| `RES-NAT-001` | resilience | Single NAT gateway serves multiple subnets | resilience |
| `RES-MSG-001` | resilience | Queue has no dead-letter configuration | resilience, operability |
| `RES-DEL-001` | resilience | Data store has no deletion safeguard | resilience |
| `RES-SPOF-001` | resilience | Shared component with no redundancy evidence (model-derived) | resilience |
| `CFG-NFR-001` | resilience | Declared availability requirement contradicts configuration | resilience |
| `CFG-REF-001` | operability | Configuration references a resource that is not defined | resilience, operability |
| `CFG-K8S-001` | operability | Container has no resource requests/limits | operability |
| `CFG-K8S-002` | operability | Container image is not pinned | operability |
| `OPS-ALARM-001` | operability | Infrastructure defines no alarms | operability |
| `OPS-LOG-001` | operability | Load balancer access logging is disabled | operability |
| `COST-SIZE-001` | finops | Large instance class without utilization evidence | finops |
| `COST-LOG-001` | finops | Log group retains data forever | finops, compliance, operability |
| `COST-S3-001` | finops | Bucket has no lifecycle policy | finops |
| `COST-NAT-001` | finops | NAT egress used without private endpoints | finops |
| `COST-SCALE-001` | finops | Fixed capacity without autoscaling | finops, performance |
| `COST-XFER-001` | finops | Cross-region replication adds transfer and storage cost | finops |
| `COST-TAG-001` | finops | Resources lack cost-allocation tags | finops |
| `COMP-LOG-001` | compliance | No audit-trail resources defined | compliance |
| `COMP-NET-001` | compliance | Network flow logging is not defined | compliance |
| `COMP-DATA-001` | compliance | Personal-data store lacks retention/backup controls | compliance |
| `PERF-CHAIN-001` | performance | Long synchronous dependency chain | performance |
| `PERF-DB-001` | performance | Shared datastore used by many services | performance |
| `PERF-CACHE-001` | performance | No caching layer in the architecture model | performance |

Rules parse structure (resource properties, manifests, the model graph). Values that are variables/data
references (`${var.x}`, `{{resolve:...}}`) are skipped rather than guessed. Rules whose conclusion depends on
information that may live outside the supplied files report `NEEDS_VALIDATION` or `INFERRED`.

## Moderator behaviour
`src/backend/agents/moderator_agent.py::ModeratorAgent.moderate`:
1. Drop references to unknown artifacts; flag components not in the model/IaC.
2. Group duplicates: shared `rule@component` fingerprints, or overlapping components with title similarity >= 0.6 *unless backed by different deterministic rules*; merge keeping all contributing agents, original severities and evidence.
3. Reconcile severity: highest severity backed by non-`NEEDS_VALIDATION` evidence (capped at MEDIUM when everything needs validation).
4. Classify: **REJECTED** (no verifiable evidence, tool result or known component; kept with explanation), **REQUIRES_VALIDATION**, **MERGED**, **VALIDATED**.
5. Optional LLM second opinion (`moderator_review` task): may downgrade, request validation or reject findings without tool evidence; can never raise severity. Failure of this pass is reported, not fatal.
6. Reconcile with the previous round: keep IDs/history for re-detected findings, mark findings from re-run agents that no longer reproduce `RESOLVED` (pending architect verification), carry findings of agents that were not re-run unchanged, never resolve findings of a failed agent.

## Parallel execution
`select_critics` -> `fan_out` emits `Send` per agent; concurrency is capped by `MAX_CONCURRENT_AGENTS`; reducers append agent results. A provider outage in one agent marks only that agent `FAILED`.

## Human interrupts
`human_review` pauses via `interrupt()`. Resume payload: `{action: submit_decisions | re_review | finalize, decisions: [...], revision_id, notes}`. Decision application is idempotent (a decision ID already in a finding's history is ignored).

## Rebuttal workflow
Dispute -> status `DISPUTED` -> on resume the `rebuttal` node asks the **originating** specialist (`finding.agent_id`) for one rebuttal (`MAINTAIN | CONCEDE | ADJUST_SEVERITY` with cited evidence) -> deterministic adjudication:

| Critic stance | Outcome | Finding status |
| --- | --- | --- |
| CONCEDE | `DISPUTE_UPHELD` | `REJECTED` (kept in audit trail, ADR drafted) |
| ADJUST_SEVERITY (lower) | `SEVERITY_ADJUSTED` | `REBUTTED` with new severity |
| MAINTAIN with verifiable evidence | `UPHELD_FINDING` | `REBUTTED` |
| MAINTAIN without evidence | `NEEDS_VALIDATION` | `OPEN`, evidence level lowered |

The exchange (architect, critic, moderator) is stored as a `Rebuttal` with all entries. A finding can be disputed at most twice (`MAX_DISPUTES_PER_FINDING`).

## Targeted re-review
`RevisionService.create_revision` ingests revised files (same filename supersedes, hashes compared), rebuilds the model, re-runs the deterministic rules and computes a `ChangeImpact`:

| Signal | Agents added |
| --- | --- |
| IAM / role / policy property changes | security, compliance |
| ingress / CIDR / public / ACL / TLS / listener changes | security |
| encryption / KMS / secret changes | security, compliance |
| multi_az / replica / backup / zone / subnet / probe / DLQ changes | resilience (+ finops for capacity-driving ones) |
| instance size / lifecycle / retention / tags | finops |
| cache / timeout / concurrency / queue / throughput | performance |
| logging / alarms / probes / deployment | operability (+ security/compliance for logging) |
| component added/removed | by component type (`TYPE_DOMAINS`) |
| deterministic rule result changed | agents that own the rule |
| synchronous relationship added/removed | performance |
| change-note keywords | matching domains |

If an artifact changed but no structured difference explains it (for example a narrative-only edit), the impact
has `confidence = LOW`, `broadened = true` and **all** enabled agents re-run - the system broadens rather than
silently skipping domains. Skipped agents appear as `SKIPPED` ("Unaffected by architecture revision N").
