# Architecture Red Team

**Submission folder:** `Rahul_Verma_694836`

## Team Details
*Individual submission; team details not applicable.*

- **Team Name:** - Rahul Verma
- **Team Members:** - Rahul Verma
- **Primary Contact:** Rahul Verma (rahul.verma2@soprasteria.com)
- **Business / Technical Area:** Aeroline

## 1. Problem Statement
Architecture reviews are slow, inconsistent and depend on which experts happen to be available. One reviewer rarely
covers security, resilience, cost, compliance, performance and operability equally well. Findings are often
opinions without traceable evidence, disagreements between the architect and the reviewers are settled informally,
and the reasoning behind accepted or deferred risks is lost. When the design changes, the whole review is usually
repeated, or worse, skipped.

## 2. Proposed Agent
Architecture Red Team is a multi-agent **adversarial review board**. Six specialist critic agents examine an
architecture pack (design documents, diagrams, infrastructure as code, incident notes) in parallel. A moderator agent
validates and de-duplicates their findings. A **human architect stays in control**: they accept, dispute or defer
each finding. A disputed finding goes back to the critic that raised it, which must answer with evidence, and the
moderator decides the outcome by fixed rules. When the architect submits a revised design, only the critics affected by
the change run again. The result is a scorecard, evidence-linked findings, draft Architecture Decision Records (ADRs)
and a PDF/JSON report. The AI never approves an architecture; it only informs the human decision.

## 3. Key Capabilities
- **Six specialist critics in parallel:** Security, Resilience, FinOps, Compliance, Performance and Operability, each
  with its own rubric, focus areas and deterministic rule set (38 rules over Terraform, CloudFormation and Kubernetes,
  plus a dependency-graph analyser).
- **Evidence-backed findings:** every finding must cite evidence the system supplied (file, line, resource or diagram node).
  The moderator rejects unsupported or invented claims and flags inferences that need validation.
- **Human in the loop:** Accept / Dispute / Defer, with input validation, a limit on disputes per finding, and
  critic rebuttals decided by a deterministic moderator. Disputes are treated as untrusted input, which guards against
  prompt injection.
- **Change-aware re-review:** impact analysis of a revised architecture re-runs only the affected critics and marks
  fixed findings as RESOLVED, keeping their history.
- **Decision records and reports:** scorecard per domain, ADR drafts for deferred and accepted risks, PDF and JSON reports
  with full traceability (document -> component -> agent -> finding -> moderator -> decision -> ADR).
- **Multiple model providers:** OpenAI, Anthropic, AWS Bedrock (API key or AWS credentials), and an offline deterministic
  mock for demos without credentials.

## 4. Target Users
- Solution and enterprise architects preparing for or running design reviews
- Architecture review boards and design authorities
- Security, cloud platform, SRE and FinOps practitioners who contribute to reviews
- Delivery leads who need a documented record of accepted and deferred risks

## 5. How It Works
1. **Ingest:** upload documents, diagrams (Draw.io, PlantUML, Mermaid, SVG, images), Terraform/CloudFormation/Kubernetes,
   PDFs, Word or PowerPoint files. Each format is parsed by a dedicated parser; uploaded code is never executed.
2. **Model:** a structured architecture model is built (components, relationships, requirements, source references).
3. **Critique:** a LangGraph workflow fans out to six critics in parallel. Each critic combines deterministic rule results
   with LLM reasoning and returns structured findings.
4. **Moderate:** the moderator validates evidence, merges duplicate findings, rejects unsupported claims and ranks the rest.
5. **Human review:** the workflow pauses at a real LangGraph `interrupt()`. The architect accepts, disputes or defers findings.
6. **Rebut:** each disputed finding goes back to its critic for an evidence-based answer; the moderator decides the outcome.
7. **Revise and re-review:** a revised architecture triggers impact analysis and a targeted re-review (up to the round limit).
8. **Finalize:** scorecard, ADR drafts and PDF/JSON reports. Open Critical/High risks are reported, never approved away.

## 6. Technology / Framework
Python 3.11+, **LangGraph** (orchestration, checkpoints, human-in-the-loop interrupts), FastAPI (REST API), Streamlit
(UI), Pydantic v2 (all contracts), SQLite (in-memory application database, file-based LangGraph checkpoints), ReportLab
(PDF), python-hcl2 / PyYAML / defusedxml / pypdf / python-docx / python-pptx (parsers). It runs locally only, with no
external infrastructure.

## 7. Tools / Integrations
- **LLM providers:** OpenAI API, Anthropic API, AWS Bedrock (Converse API) - one is required for live analysis
- **Deterministic tools:** built-in IaC rule engine and dependency-graph analyser; optional Checkov adapter
- **Knowledge base:** sample organisational standards, control mappings and per-agent rubrics (YAML/Markdown in `src/knowledge/`)
- **No MCP servers** are required

## 8. Expected Business Value
- **Productivity / time savings:** a first-pass, multi-domain review is ready in minutes rather than waiting for several
  experts to become available, so human reviewers can spend their time on judgement rather than discovery.
- **Quality improvement:** consistent coverage of six domains, evidence-linked findings, and explicit handling of
  missing information (treated as lower coverage, not as a pass).
- **Reduced manual effort:** automatic de-duplication, targeted re-review after changes, and generated ADR drafts and reports.
- **Governance and auditability:** every accepted, disputed and deferred risk is recorded with its justification, owner
  and review date.
- **Cost awareness:** FinOps findings flag over-provisioning and missing cost controls in qualitative terms, without
  making up prices.

## 9. Innovation / Differentiator
- **Adversarial by design:** critics *argue* with the architect under moderator rules, instead of producing a one-shot report.
- **Evidence or it doesn't count:** findings must cite evidence IDs from a catalogue the system supplies, so the model cannot invent sources.
- **Deterministic guardrails around the AI:** rule-based tools, fixed moderator outcomes, dispute limits, round limits,
  input validation and prompt-injection isolation.
- **Change-aware:** impact analysis decides which specialists need to run again.
- **Human authority preserved:** the system can report open risks but can never approve an architecture.

## 10. Architecture / Flow Diagram
See [docs/system-design.md](docs/system-design.md) (component, workflow and sequence diagrams),
[docs/agent-workflows.md](docs/agent-workflows.md) (LangGraph nodes, routing, rule table) and
[docs/architecture.md](docs/architecture.md) (summary).

## 11. Effort Spent in Hrs
~30 hours. (with AI help)

## 12. Expected Output
- Findings per domain with severity, evidence level, source references, affected components and recommendations
- Moderator diagnostics (merged, validated, rejected and needs-validation findings)
- Decision and rebuttal history per finding
- Scorecard per domain with coverage
- ADR drafts (Markdown) for deferred and significant accepted risks
- PDF and JSON review reports; final status `COMPLETED` or `COMPLETED_WITH_OPEN_RISKS`

## 13. Data / Security Considerations
- **Local only:** the service binds to `127.0.0.1` and is unauthenticated by design; it must not be exposed publicly.
- **Data sent to providers:** in live mode, relevant document content is sent to the chosen LLM provider. Use an approved
  provider and account for confidential architectures; offline demo mode sends nothing.
- **Secrets:** API keys come from `.env` or the Settings page (held in memory only). They are never persisted, logged,
  returned by the API or included in reports or snapshots. No secrets are included in this submission; see `.env.example`.
- **Uploads are parsed, never executed**, file names are sanitised, and XML/YAML parsing is hardened.
- **Prompt injection:** document and dispute text is wrapped as untrusted data; critics are told not to follow instructions inside it.
- **Demo data:** the sample Terraform deliberately contains a fake password (`Sup3rS3cret!2025`) and other planted flaws for
  the critics to find. It is not a real credential.
