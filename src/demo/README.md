# demo

Offline demonstration scenarios. They let the whole product run end to end with no API keys:
real parsing, extraction, deterministic tools, moderation, human decisions, rebuttals, revisions and
reports all execute; only the *LLM answers* are replaced by the fixture-driven `MockProvider`.

Everything produced from these files is labelled **SIMULATED DEMO** in the UI, JSON and PDF
(`Review.simulated`, `Finding.simulated`). It is not a model's discovery.

## Folder layout

```
demo/
  fixtures/<scenario_id>/
    scenario.json            metadata: name, description, files + roles, optional revision
    <file>...                the sample architecture files uploaded for the scenario
    revision-1/<file>...     the predefined hardened revision (replaces same-named files)
  seeded_findings/<scenario_id>.json    MockProvider fixture: the findings the "critics" emit
  expected_results/<scenario_id>.json   labelled expectations used by the evaluation endpoint
```

### `fixtures/<scenario>/scenario.json`

```json
{
  "scenario_id": "aws-ecommerce",
  "name": "ShopSphere - AWS e-commerce platform",
  "description": "...",
  "cloud_environment": "AWS",
  "files": [ {"filename": "architecture.md", "role": "PRIMARY_ARCHITECTURE"}, ... ],
  "revision": {
    "title": "Hardening revision 1",
    "change_notes": "text shown to the architect and fed to impact analysis",
    "files": [ {"filename": "infrastructure.tf", "role": "INFRASTRUCTURE"} ]
  }
}
```

`role` must be a `DocumentRole` value (`PRIMARY_ARCHITECTURE`, `DIAGRAM`, `INFRASTRUCTURE`,
`STANDARD`, `ADR`, `INCIDENT`, `SUPPORTING`). `DemoService` resolves files relative to the scenario
folder; revision files are always read from the fixed subfolder `revision-1/`.

### `seeded_findings/<scenario>.json` (MockProvider fixture)

Top-level: `scenario_id`, `title`, `disclaimer`, `information_gaps` (agent -> strings), `conflicts`,
`flaws[]`. Each flaw (`flaw_id`, `agent_id`, `severity`, `evidence_level`, text fields, `applies_when`,
`rebuttal`, optional `seed_type`, `moderator_verdict`, ...) is emitted by its agent only when
`applies_when` matches the *current* extracted model / tool results. Condition keys are
`tool_rule_ids`, `tool_component_contains`, `components` (matchers with `type`, `name_contains`,
`config_equals`, `config_missing`, `config_present`), `region_any`, `model_empty`, `model_nonempty`,
`nfr_category` and `always`. The full field reference is in `backend/llm/README.md`.

Two flaws per scenario are deliberate negatives:

| `seed_type` | Purpose |
| --- | --- |
| `unsupported_claim` | A confident claim that cites no evidence; the moderator should downgrade it to `NEEDS_VALIDATION` |
| `hallucination` | Refers to something not in the architecture; the moderator should reject it |

### `expected_results/<scenario>.json`

```json
{
  "scenario_id": "...",
  "expected_flaws": [
    {"flaw_id": "AWS-SEC-01", "agent_id": "security", "category": "...",
     "rule_ids": ["SEC-NET-002"], "match_keywords": []}
  ],
  "seeded_unsupported": [ {"flaw_id": "AWS-PERF-04", "title_contains": "saturate at about 200 requests"} ]
}
```

`EvaluationService.evaluate` counts a finding as matching a flaw when the agent is a contributor and
either a linked tool result has one of the `rule_ids` or all `match_keywords` appear in the finding
text. It reports precision, recall, matched/missed/unexpected, the share of flaws independently
detected by deterministic rules and whether each `seeded_unsupported` entry was rejected or
downgraded. `expected_flaws` is a subset of the seeded flaws (the ones checkable by rules or
keywords), so seeded flaws outnumber expected ones. Results are "observed on included fixtures only".

## Scenarios

| Scenario | Files | Seeded flaws | Expected |
| --- | --- | --- | --- |
| `aws-ecommerce` (ShopSphere, AWS) | `architecture.md`, `diagram.puml`, `infrastructure.tf`, `ops-incident-notes.md` | 31 (29 real + 1 unsupported claim + 1 hallucination) | 22 |
| `microservices-k8s` (PayFlow, Kubernetes) | `architecture.md`, `architecture.drawio`, `manifests.yaml` | 26 (24 + 2 negatives) | 15 |
| `hybrid-onprem` (LedgerBridge, on-premises + AWS) | `architecture.md`, `diagram.puml`, `cloudformation.yaml`, `incident-2025-11-vpn-outage.md` | 24 (22 + 2 negatives) | 14 |

### aws-ecommerce - ShopSphere

Seeded weaknesses: publicly accessible PostgreSQL RDS; security groups open to the world on
database/SSH ports; IAM role with `*`/`*`; hard-coded database password; public-read media bucket;
plaintext HTTP listener; unencrypted database storage; single-AZ database; backups disabled;
application tier in one subnet/AZ; SQS queue without DLQ; single NAT gateway; oversized instance
class; S3 traffic via NAT; unbounded log/object retention; missing cost tags; synchronous checkout
chain; shared database; no cache; no alarms; no audit/flow logging; no invoice retention
enforcement. Negatives: `AWS-PERF-04` (database saturation at 200 rps, unsupported) and
`AWS-OPS-05` (Kubernetes pod probes in a non-Kubernetes system, hallucination).
Revision: database made private, encrypted, multi-AZ with backups; security groups and IAM scoped;
DLQ and alarms added; ASG spread across two AZs; a cross-region DR bucket added (introduces new
cost/transfer findings).

### microservices-k8s - PayFlow

Seeded weaknesses: privileged payments container with host networking; credential literals in
manifests; Ingress without TLS; no NetworkPolicy; no service-to-service authentication; single
replicas for gateway, orders and data stores; no probes; no resource requests/limits; unpinned
images; Ingress route to a non-existent service; dashboards without alert rules; `kubectl apply`
deployments without progressive rollout; PCI-style segmentation and audit-log gaps; shared
PostgreSQL; synchronous checkout chain. Negatives: `K8S-PERF-04` (cache stampede at 95% miss rate)
and `K8S-RES-05` (misconfigured failover to a standby cluster in eu-west-3).
Revision: replicas and probes added, limits set, images pinned, privilege removed, secrets by
reference, TLS and default-deny NetworkPolicy added, dangling route removed.

### hybrid-onprem - LedgerBridge

Seeded weaknesses: internet-open reporting database listener; public partner bucket; wildcard
gateway role; API key in function environment; single VPN path; standby sharing the primary data
centre; no DLQ on the settlement queue; single-AZ reporting database; on-premises Nagios not
covering cloud; silent batch hand-off failures; stale manual failover runbook; personal data
replicated outside the EU; retention not technically enforced; unquantified VPN data-transfer cost.
Negatives: `HYB-PERF-03` (VPN capped at 100 Mbps) and `HYB-SEC-06` (unauthenticated mainframe SNA
gateway).
Revision: listener restricted to the VPN CIDR, database encrypted and Multi-AZ, partner bucket
private, gateway role scoped, API key moved to an SSM secure parameter, DLQ and log retention added.

## How demos run

1. `GET /demo/scenarios` lists the folders found under `fixtures/` via `scenario.json`.
2. `POST /demo/scenarios/{id}/project` creates a project named `DEMO: <name>` (with
   `scenario_id`) and ingests the scenario files with their roles.
3. `POST /reviews` with `demo_mode=true` (provider forced to `mock`) and the scenario id, then
   `/start`. `AppContainer.provider_for` returns `MockProvider` for demo reviews.
4. Optional: `POST /reviews/{id}/revisions` with `use_demo_revision=true` loads `revision-1/`,
   `POST /reviews/{id}/re-review` runs only the affected agents.

## Adding a scenario

1. Create `demo/fixtures/<new-id>/` with the architecture files and a `scenario.json`
   (`scenario_id` must equal the folder name; use supported extensions, see
   `backend/parsers/README.md`).
2. Optionally add `revision-1/` containing hardened copies of changed files and the `revision`
   block in `scenario.json`.
3. Create `demo/seeded_findings/<new-id>.json` with flaws whose `applies_when` conditions reference
   rule ids from `backend/tools` and component names/types that your fixture files produce.
   Use `{components}` placeholders in text, cite `principle` ids that exist in `knowledge/`, and
   include at least one `unsupported_claim` and one `hallucination` if you want to exercise the
   moderator.
4. Create `demo/expected_results/<new-id>.json` listing the flaws that rules or keywords can detect.
5. Add the scenario to the parametrised test in
   `tests/integration/test_api_and_scenarios.py` (minimum finding count) and, if relevant, to the
   extraction tests in `tests/unit/test_extraction_and_tools.py`.
6. Validate: run the scenario through the UI or `pytest tests/integration -k scenario`; check the
   evaluation endpoint reports matched flaws and that the hardened revision resolves some of them.

## Inputs, outputs, dependencies

* Read by `DemoService`, `MockProvider` (`seeded_findings`), `EvaluationService` and the tests.
* Depends on rule ids in `backend/tools` and knowledge ids in `knowledge/`; if a rule is renamed the
  matching `applies_when` and `expected_results` entries must be updated.

## Limitations

* Fixtures are static: the simulated critics cannot discover anything that is not seeded.
* Only one predefined revision (`revision-1`) per scenario is supported.
* Evaluation numbers describe these three fixtures only and say nothing about live-model quality.
* Seeded text is written for the sample architectures; it is not a security assessment of any real
  system.
