# knowledge

The local knowledge base. It is read-only at runtime, loaded by `ContextService`
(`backend/services/context_service.py`) and used in three ways: retrieval context for the critics,
critic personas/rubrics, and the scoring rubric. No embeddings or vector database are involved;
retrieval is a pure-Python TF-IDF index.

```
knowledge/
  standards/            Markdown standards with YAML front matter (retrieval chunks)
  control_mappings/     YAML control-to-architecture mappings (retrieval chunks + rule links)
  agent_rubrics/        One YAML per critic + scoring.yaml
```

**The ACME-* documents are fictional sample standards** shipped so findings can cite an
"organisational" control. Replace or extend them with your own policies; they are not real
regulations or an endorsement of any organisation.

## `standards/*.md`

Each file starts with front matter:

```yaml
---
title: Acme Cloud Security Standard (sample)
origin: ORGANIZATIONAL_STANDARD     # or GENERAL_PRACTICE
version: "2.1"
domains: [security, compliance]     # agents that should prefer this document
---
```

| Key | Use |
| --- | --- |
| `title` | Chunk title (`KnowledgeChunk.title`); defaults to the file stem |
| `origin` | `ORGANIZATIONAL_STANDARD` (mandatory organisational control) or `GENERAL_PRACTICE` (default). Determines how a finding's `principle_origin` can be labelled |
| `version` | Informational; not used by code |
| `domains` | Agent ids; matching chunks rank normally, non-matching chunks are down-weighted x0.6 for that agent |

The body is split at Markdown headings (`#` to `####`) and long sections into chunks of at most 900
characters (`chunk_markdown`). Control identifiers are second-level headings of the form
`## ACME-SEC-001 Network exposure`; `BaseAgent._classify_principle` accepts a cited
`ACME-<AREA>-<nnn>` id as `ORGANIZATIONAL_STANDARD` only when that id appears in a chunk with that
origin.

| File | Origin | Control ids |
| --- | --- | --- |
| `acme-security-standard.md` | ORGANIZATIONAL_STANDARD | ACME-SEC-001 .. 006 |
| `acme-resilience-standard.md` | ORGANIZATIONAL_STANDARD | ACME-RES-001 .. 006 |
| `acme-finops-and-performance-guidelines.md` | ORGANIZATIONAL_STANDARD | ACME-FIN-001 .. 005, ACME-PERF-001 .. 003 |
| `acme-operability-and-data-protection.md` | ORGANIZATIONAL_STANDARD | ACME-OPS-001 .. 004, ACME-DATA-001 .. 003 |
| `general-architecture-principles.md` | GENERAL_PRACTICE | (unnumbered principles) |

Users can also upload documents with role `STANDARD` (origin `ORGANIZATIONAL_STANDARD`) or `ADR` /
`INCIDENT` (origin `CONTEXT_DOCUMENT`); these are indexed per review by
`ContextService.chunks_from_documents`.

## `control_mappings/*.yaml`

Architecture-level mappings, written in the authors' own words; not legal advice and not a
statement of compliance.

```yaml
framework: GDPR
version: "2025.1"
origin: CONTROL_MAPPING
disclaimer: >
  ...text appended to every generated chunk...
controls:
  - id: "Art.32"                       # control identifier
    topic: Security of processing
    architecture_relevance: Encryption, confidentiality, resilience ...
    rule_ids: [SEC-ENC-001, RES-BAK-001]   # built-in rules that provide evidence
    evidence_needed: [encryption configuration, backup and restore evidence]
```

| File | Framework | Controls |
| --- | --- | --- |
| `gdpr.yaml` | GDPR | 5 |
| `iso27001.yaml` | ISO/IEC 27001:2022 Annex A | 8 |
| `pci_dss.yaml` | PCI DSS v4.0 (requirement headlines) | 5 |

Each control becomes a retrieval chunk with `origin = CONTROL_MAPPING` and domains
`compliance, security`. `ContextService.controls_for_rules(rule_ids)` reverses the mapping (rule ->
controls) and is used by the compliance agent's domain brief, so `rule_ids` must be real ids from
`backend/tools` (see its README).

## `agent_rubrics/<agent>.yaml`

One file per critic (`security`, `resilience`, `finops`, `compliance`, `performance`,
`operability`), loaded by `ContextService.rubric(agent_id)` and rendered into the system prompt by
`BaseAgent.system_prompt`.

| Key | Used for |
| --- | --- |
| `agent_id`, `version` | Identification (not read by code) |
| `display_name` | Name in the prompt |
| `persona` | Role description |
| `mission` | One-line objective |
| `focus_areas` | Bullet list of topics |
| `evidence_expectations` | Rules about citing evidence |
| `severity_guidance` | Map `CRITICAL..INFORMATIONAL` -> meaning |
| `anti_patterns` | Behaviours to avoid |
| `domain_prompt_addendum` | Extra domain instructions |

## `agent_rubrics/scoring.yaml`

Read by `ContextService.scoring_rubric()` and applied by `ScoringService.scorecard`:

| Key | Values |
| --- | --- |
| `severity_weights` | CRITICAL 25, HIGH 15, MEDIUM 7, LOW 3, INFORMATIONAL 0 |
| `evidence_multipliers` | CONFIRMED 1.0, INFERRED 0.6, NEEDS_VALIDATION 0.25 |
| `status_treatment` | Fraction of the penalty still counted per status: OPEN/ACCEPTED/DISPUTED/REBUTTED/DEFERRED 1.0, REMEDIATION_PROPOSED 0.5, RESOLVED/VERIFIED/REJECTED 0.0 |
| `bands` | Strong >= 85, Adequate >= 70, Needs attention >= 50, At risk >= 0 |
| `overall_policy`, `description`, `version` | Documentation only |

Score per domain = `100 - sum(severity_weight x evidence_multiplier x status_factor)`, clamped to
0-100 and rounded to the nearest 5. Findings that need validation are tracked separately as
unverified concerns; missing information lowers `coverage` (FULL / PARTIAL / NONE), not the score.
A domain with no coverage is reported as "Not assessed". The overall statement reports the weakest
assessed band and the number of unresolved Critical/High findings; it is never an approval.

## Inputs, outputs, dependencies

* Input: files in this folder (UTF-8). Parsed with `yaml.safe_load`.
* Output: `KnowledgeChunk` lists, rubric dictionaries, `Scorecard` weights.
* Path: `Settings.knowledge_dir` (`<repo>/knowledge`).

## Role in application execution

Loaded lazily by `ContextService` (cached per process): `retrieve_context` and each critic's
`retrieve_evidence` step search the index; `BaseAgent.system_prompt` reads rubrics;
`GET /reviews/{id}/score` applies `scoring.yaml`. Chunks cited by a finding appear as `KB:<chunk_id>`
evidence.

## Extension points

* Add a standard: drop a `*.md` file with front matter into `standards/`; include `## <ID> <Title>`
  headings. Restart the backend (results are cached).
* Add a framework: add a YAML file to `control_mappings/` following the schema above.
* Tune a critic: edit its rubric YAML; add a new critic by creating `<agent_id>.yaml` (and see
  `backend/agents/README.md`).
* Tune scoring: edit `scoring.yaml`; no code change needed.

## Limitations

* Retrieval is lexical (TF-IDF with a crude suffix stemmer); synonyms are not matched.
* Caches use `functools.cache`, so edits require a backend restart.
* The `ACME-[A-Z]+-\d{3}` pattern is hard-coded in `base_agent.py`; organisations using another id
  scheme are only classified as organisational standards when the finding cites a retrieved
  organisational chunk and claims that origin.
* Control mappings are summaries by the authors, not the text of the regulations.
* `iso27001.yaml`/`pci_dss.yaml` reference rules by id; renaming a rule requires updating them.
