# backend/tools

Deterministic checks. They read *structured* resource definitions (Terraform, CloudFormation,
Kubernetes) or the extracted architecture model and emit reproducible `ToolResult` objects. A rule
never reports a defect merely because a keyword is absent from free text.

## Purpose and responsibility

* Provide the rule registry, helpers and runner (`base.py`).
* Implement 38 built-in rules in five modules, plus a graph analyzer and an optional Checkov adapter.
* Map rules to the specialist agents that execute them (`AGENT_RULES` in `__init__.py`).

## Modules

| File | Contents |
| --- | --- |
| `base.py` | `ToolContext`, `Rule`, `REGISTRY`, `register()`, `run_rules()`, `dedupe_results()`, `make_result()`, value helpers (`get_path`, `has`, `as_bool`, `as_int`, `as_list`, `is_unresolved`, `stringify`, `references_in`, `evidence_ref`) |
| `__init__.py` | Imports every rule module (which registers rules), `AGENT_RULES`, `CHECKOV_AGENTS = ("security", "compliance")` |
| `security_checks.py` | 10 rules, `TOOL_ID = "security_checks"` |
| `resilience_checks.py` | 8 rules, `TOOL_ID = "resilience_checks"` |
| `cost_checks.py` | 7 rules, `TOOL_ID = "cost_checks"` |
| `configuration_checks.py` | 9 rules (K8s config, references, NFR contradiction, alarms, logging, compliance), `TOOL_ID = "configuration_checks"` |
| `dependency_analyzer.py` | `DependencyGraph` and 4 rules over the model, `TOOL_ID = "dependency_analyzer"` |
| `k8s.py` | `pod_spec`, `pod_labels`, `containers`, `WORKLOAD_KINDS`, `REPLICATED_KINDS` |
| `checkov_adapter.py` | `checkov_available()`, `run_checkov(files, timeout_seconds)` |

## The 38 registered rules

| Rule id | Title | Run by |
| --- | --- | --- |
| `SEC-NET-001` | Security group allows the world on non-web ports | security |
| `SEC-NET-002` | Database or cache is publicly accessible | security, compliance |
| `SEC-STO-001` | Object storage is publicly readable or writable | security, compliance |
| `SEC-ENC-001` | Storage resource is not encrypted at rest | security, compliance |
| `SEC-ENC-002` | Traffic is served without transport encryption | security |
| `SEC-SEC-001` | Credential literal committed in infrastructure definition | security, compliance |
| `SEC-IAM-001` | IAM policy grants wildcard actions or resources | security, compliance |
| `SEC-K8S-001` | Workload runs with elevated privileges | security |
| `SEC-K8S-002` | No NetworkPolicy isolates workloads | security |
| `SEC-API-001` | API method declared without authorization | security |
| `RES-HA-001` | Datastore deployed in a single availability zone | resilience |
| `RES-HA-002` | Workload runs as a single replica | resilience, performance |
| `RES-HA-003` | Container has no readiness or liveness probe | resilience, operability |
| `RES-HA-004` | Compute capacity is not spread or self-healing | resilience |
| `RES-BAK-001` | Automated backups are disabled | resilience |
| `RES-NAT-001` | Single NAT gateway serves multiple subnets | resilience |
| `RES-MSG-001` | Queue has no dead-letter configuration | resilience, operability |
| `RES-DEL-001` | Data store has no deletion safeguard | resilience |
| `RES-SPOF-001` | Shared component with no redundancy evidence | resilience |
| `CFG-NFR-001` | Declared availability requirement contradicts configuration | resilience |
| `CFG-REF-001` | Configuration references a resource that is not defined | resilience, operability |
| `CFG-K8S-001` | Container has no resource requests/limits | operability |
| `CFG-K8S-002` | Container image is not pinned | operability |
| `OPS-ALARM-001` | Infrastructure defines no alarms | operability |
| `OPS-LOG-001` | Load balancer access logging is disabled | operability |
| `COMP-LOG-001` | No audit-trail resources defined | compliance |
| `COMP-NET-001` | Network flow logging is not defined | compliance |
| `COMP-DATA-001` | Personal-data store lacks retention and backup controls | compliance |
| `COST-SIZE-001` | Large instance class without utilization evidence | finops |
| `COST-LOG-001` | Log group retains data forever | finops, compliance, operability |
| `COST-S3-001` | Bucket has no lifecycle policy | finops |
| `COST-NAT-001` | NAT egress used without private endpoints for AWS services | finops |
| `COST-SCALE-001` | Fixed capacity without autoscaling | finops, performance |
| `COST-XFER-001` | Cross-region replication adds transfer and storage cost | finops |
| `COST-TAG-001` | Resources lack cost-allocation tags | finops |
| `PERF-CHAIN-001` | Long synchronous dependency chain | performance |
| `PERF-DB-001` | Shared datastore used by many services | performance |
| `PERF-CACHE-001` | No caching layer in the architecture model | performance |

## Behaviour

* A rule is `fn(ctx: ToolContext) -> (list[ToolResult], resources_checked)`, registered with
  `@register(rule_id, TOOL_ID, TOOL_NAME, domain, title)`.
* `run_rules(rule_ids, ctx)` runs registered ids, aggregates a `ToolRunSummary` per `tool_id`
  (`EXECUTED`, or `ERROR` if a rule raised - never hidden), counts resources and violations.
* `make_result(...)` builds a `ToolResult` whose id is `TR-<sha1(rule|artifact|address|qualifier)[:10]>`,
  so the same finding produced for two agents has the same id and `dedupe_results` collapses it.
  Results carry `evidence_level` (CONFIRMED / INFERRED / NEEDS_VALIDATION), `severity_hint`,
  `observed_value`, `expected_value` and a `SourceReference` with file and line.
* Unresolved values (`var.x`, `${local...}`, `Ref`, `Fn::*`) are treated as unknown, not as
  violations (`is_unresolved`).
* `run_checkov` writes copies of Terraform/CloudFormation/Kubernetes files to a private temp
  directory and runs `checkov -d <tmp> --output json --quiet --compact --skip-download` with a
  timeout. If the executable is missing it returns `NOT_EXECUTED` with an explanatory detail; it
  never fabricates results. Checkov results are only added for `CHECKOV_AGENTS`.

## Inputs and outputs

* Input: `ToolContext(review_id, resources: list[IacResource], model: ArchitectureModel,
  artifact_names, artifact_texts, parameters)` built by `services/tool_service.build_tool_context`.
* Output: `ToolResult` list and `ToolRunSummary` list.

## Dependencies

`models.domain`, `models.architecture`, `parsers.types`. `services.architecture_service` and
`services.revision_service` import helpers and `AGENT_RULES` from here.

## Role in application execution

* `extract_architecture` runs all rules once (baseline stored per revision).
* Each specialist's `sp_run_tools` step runs only its `AGENT_RULES[agent_id]` and, for security and
  compliance, Checkov (cached per review revision in `ReviewCache`).
* `RevisionService.create_revision` re-runs all rules and compares with the stored baseline to
  decide which agents must re-run.
* Tool results become `TOOL:<id>` evidence items; the mock provider's `tool_rule_ids` conditions
  key off `rule_id`.

## Extension points

Adding a rule:
1. Write the function in an existing module or a new `backend/tools/<name>.py` and decorate it with
   `@register("XXX-YYY-001", TOOL_ID, TOOL_NAME, "<domain>", "<title>")`.
2. If it is a new module, import it in `backend/tools/__init__.py`.
3. Add the id to the relevant lists in `AGENT_RULES`. A rule not listed there never runs in a
   review (the unit test `test_all_registered_rules_are_assigned_to_an_agent_and_exist` enforces this).
4. Optionally reference it from `knowledge/control_mappings/*.yaml` (`rule_ids`).
5. Add fixture coverage: a flaw in `demo/seeded_findings/<scenario>.json` with
   `applies_when.tool_rule_ids`, an `expected_results` entry, and a unit test.

## Limitations

* Coverage is limited to the constructs the rules understand (principally AWS Terraform and
  CloudFormation, plus core Kubernetes workload kinds); other resources are not evaluated.
* No Terraform variable/module resolution; values depending on variables are skipped.
* Rules inspect configuration, not runtime state; they cannot confirm compensating controls
  outside the supplied files.
* Checkov is optional and only a subset of file kinds is passed to it.
