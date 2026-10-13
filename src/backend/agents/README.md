# backend/agents

The six specialist critics, the moderator and the rebuttal request. Agents are plain Python classes;
orchestration (parallelism, retries of the workflow, persistence) belongs to `backend/graph`.

## Purpose and responsibility

* `BaseAgent` implements the process every critic follows: scope -> run tools -> retrieve context ->
  analyze (LLM) -> validate. The graph's specialist subgraph calls these methods one at a time.
* Specialists contribute a persona (from `knowledge/agent_rubrics/<id>.yaml`), the component types
  they care about, a keyword string for retrieval and a deterministic *domain brief*.
* `ModeratorAgent` deduplicates, validates evidence, reconciles severity and adjudicates rebuttals.

## Modules

| File | Contents |
| --- | --- |
| `base_agent.py` | `BaseAgent`, `ScopedView`, `doc_section_items()`, `UNTRUSTED_BEGIN` / `UNTRUSTED_END` |
| `security_agent.py` | `SecurityAgent` (`agent_id="security"`; STRIDE prompts per component type, entry points, trust-boundary flows, blast radius) |
| `resilience_agent.py` | `ResilienceAgent` (`"resilience"`) |
| `finops_agent.py` | `FinOpsAgent` (`"finops"`; qualitative cost risk only, never states prices) |
| `compliance_agent.py` | `ComplianceAgent` (`"compliance"`; adds a non-determination caveat to every finding) |
| `performance_agent.py` | `PerformanceAgent` (`"performance"`) |
| `operability_agent.py` | `OperabilityAgent` (`"operability"`) |
| `moderator_agent.py` | `ModeratorAgent`, `ModerationResult` |
| `rebuttal.py` | `build_rebuttal_catalog()`, `request_rebuttal()` |
| `__init__.py` | `AGENT_CLASSES: dict[str, type[BaseAgent]]` |

## `BaseAgent` pipeline

| Step | Method | Notes |
| --- | --- | --- |
| rules | `rule_ids` property | `AGENT_RULES[agent_id]` from `backend/tools/__init__.py` |
| scope | `scope(model, tool_results) -> ScopedView` | Components filtered by `relevant_types` plus any component named by tool results; builds `domain_brief()` |
| retrieve | `retrieve(index, model, view, tool_results, k=6)`, `retrieval_query()` | TF-IDF search, boosted to the agent's domain |
| catalog | `build_catalog(model, view, tool_results, chunks, doc_sections)`, `format_catalog()` | Evidence items with IDs; truncated to `max_catalog_chars` (derived from `MAX_INPUT_TOKENS`, about 1.8 characters per token budget, minimum 8000) in priority order TOOL, COMP, NFR, REL, DOC, KB |
| analyze | `analyze(...) -> (AgentReviewOutput, UsageRecord)` | One `generate_structured` call, task `agent_review` |
| validate | `validate(output, ...) -> (findings, warnings)` | Converts drafts to `Finding` objects, see below |

### Evidence-ID citation contract

Every finding must cite IDs from the catalog in `evidence_ids`:

| Prefix | Source | Supports `CONFIRMED`? |
| --- | --- | --- |
| `COMP:<component_id>` | Extracted component and its configuration | yes |
| `REL:<relationship_id>` | Extracted relationship | yes |
| `TOOL:<tool_result_id>` | Deterministic tool / Checkov result | yes (adds `tool_result_refs`) |
| `DOC:<artifact_id>:<n>` | Ranked section of an uploaded document | yes |
| `NFR:<nfr_id>` | Extracted non-functional requirement | yes |
| `KB:<chunk_id>` | Knowledge-base standard / control chunk | no (context only; sets principle origin) |

`validate` drops unknown IDs (with a warning), downgrades `CONFIRMED` findings without a supporting
item to `NEEDS_VALIDATION`, resolves `affected_components` to component ids, adds fingerprints
(`<rule_id>@<component>` per tool result and `title:<normalised title>@<components>`), and derives
`principle_origin` itself: an `ACME-XXX-nnn` id must exist in an `ORGANIZATIONAL_STANDARD` chunk;
GDPR/ISO/PCI patterns map to `CONTROL_MAPPING`; otherwise `GENERAL_PRACTICE`. The LLM's claim is not
trusted. The system prompt also frames catalog text as untrusted data between
`<<<BEGIN_UNTRUSTED_EVIDENCE>>>` / `<<<END_UNTRUSTED_EVIDENCE>>>` markers.

## `ModeratorAgent`

`moderate(review_id, candidates, previous, model, artifact_ids, round_no, revision, rerun_agents,
failed_agents, scenario_id, use_llm, known_resources) -> ModerationResult`:

1. `_validate_references` drops references to unknown artifacts, notes unknown components.
2. `_merge` groups duplicates (union-find) by shared fingerprints, or same component with title
   Jaccard >= 0.6 unless backed by different tool rules; `_merge_group` keeps the best-evidenced
   member, takes the highest severity among non-`NEEDS_VALIDATION` members (capped at MEDIUM if all
   need validation), records `contributing_agents`, `merged_from`, `original_severities`.
3. `_classify` -> `VALIDATED` / `REQUIRES_VALIDATION`, or `REJECTED` when no tool result, source
   reference or known component backs the claim.
4. `_llm_pass` (task `moderator_review`): verdicts may downgrade, require validation or reject;
   rejection of a finding with tool evidence is converted to validation; severity is never raised.
   Failures are reported as a warning and deterministic moderation stands.
5. `_reconcile` against `previous`: keeps finding ids and history across revisions, reopens
   findings that were resolved but are detected again, marks findings **not reproduced by a
   re-run agent** `RESOLVED`, and carries over findings of agents that did not run or failed.

`adjudicate(finding, rebuttal, valid_evidence)` is deterministic: `CONCEDE` -> `DISPUTE_UPHELD`
(finding `REJECTED`); lower `ADJUST_SEVERITY` -> `SEVERITY_ADJUSTED` (`REBUTTED`); evidence or tool
refs present -> `UPHELD_FINDING` (`REBUTTED`); otherwise `NEEDS_VALIDATION`.

## Inputs and outputs

* Inputs: `ArchitectureModel`, `ToolResult` list, `KnowledgeChunk`s, document sections, a provider.
* Outputs: `AgentReviewOutput` (drafts, `information_gaps`, `coverage_summary`), `Finding` list,
  `ModerationResult`, `RebuttalOutput`.

## Dependencies

`llm` (provider, contracts), `models`, `services.context_service` (rubrics, index),
`tools` (`AGENT_RULES`, `DependencyGraph`). No dependency on `graph` or the API.

## Role in application execution

`AppContainer.agent_for` instantiates `AGENT_CLASSES[agent_id](provider, context)`. The graph's
specialist subgraph nodes (`sp_select_rules`, `sp_run_tools`, `sp_scope`, `sp_retrieve`,
`sp_analyze`, `sp_validate`) call the pipeline above; `nodes.moderator` creates a `ModeratorAgent`;
`nodes.rebuttal` calls `request_rebuttal` and `ModeratorAgent.adjudicate`.

## Extension points

Adding a specialist:
1. Subclass `BaseAgent` with `agent_id`, `display_name`, `relevant_types`, `query_keywords` and
   `domain_brief()`.
2. Register it in `AGENT_CLASSES` and add the id to `AgentId` / `AGENT_LABELS` in `models/domain.py`.
3. Add its rule list to `AGENT_RULES` (`backend/tools/__init__.py`).
4. Create `knowledge/agent_rubrics/<id>.yaml` (same schema as the existing ones).
5. Update places that hard-code the six ids: the PDF section order in
   `reporting/pdf_generator.py`, `revision_service.PROPERTY_DOMAINS`/`TYPE_DOMAINS`, the frontend and
   seeded fixtures for demo scenarios.

Changing critic behaviour without code: edit the rubric YAML (persona, focus areas, severity
guidance, anti-patterns, `domain_prompt_addendum`).

## Limitations

* Prompts are bounded by a character budget, not a tokenizer; large architectures are truncated by
  priority order.
* Merging is lexical (fingerprints and title tokens); semantically identical findings with different
  wording and components may stay separate.
* Moderator LLM pass is advisory and can only lower severity or request validation.
