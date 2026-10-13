# Architecture Red Team

**Adversarial architecture review with six specialist AI critics, a moderator and a human architect in control.**

Upload architecture documents, diagrams and infrastructure definitions. The application builds a structured
model of the solution, runs six specialist critics **in parallel** (Security, Resilience, FinOps, Compliance,
Performance, Operability) supported by deterministic checks, validates and de-duplicates their findings with a
moderator, and lets the architect **accept, dispute or defer** each finding. Disputes get an evidence-backed
rebuttal from the originating critic; revised architectures trigger a *targeted* re-review of only the affected
critics. The result is a scorecard, findings with evidence, ADR drafts and a PDF/JSON report.

> **AI By Design Agentathon submission:** agent idea, capabilities and value are in [Agent.md](Agent.md).

> Local proof of concept. It binds to `127.0.0.1`, has no authentication and must not be exposed publicly.
> The offline demo clearly shows **SIMULATED DEMO MODE**: fixture-generated results are never presented as AI-discovered.

## Main features

* One command launcher: `python src/run.py` (FastAPI + Streamlit, browser opens automatically, Ctrl+C stops both).
* LangGraph workflow with real fan-out/fan-in, `interrupt()`-based human review, resume, rebuttal, revision and re-review loops.
* 38 deterministic rules over Terraform, CloudFormation and Kubernetes plus a dependency-graph analyzer and an optional Checkov adapter.
* Document intake: PDF, DOCX, PPTX, Markdown/TXT, PNG/JPG/WEBP, SVG, Draw.io, Mermaid, PlantUML, Terraform, YAML/JSON (CloudFormation, Kubernetes, OpenAPI, Compose).
* Evidence traceability: document -> component -> agent -> finding -> moderator -> human decision -> ADR/report.
* Providers: Anthropic, AWS Bedrock, OpenAI, and a deterministic offline mock.
* Durable local state: uploads, reports, snapshots and LangGraph checkpoints survive restarts; the application DB is in-memory by design.
* Three offline sample architectures with seeded flaws, expected results and a local evaluation harness.

## Prerequisites

* Python 3.11 or later (tested with 3.11)
* Internet access only for installing packages and for live LLM analysis
* For live analysis: an API key or account for **one** of OpenAI, Anthropic or AWS Bedrock (not needed for the offline demo)

## Technology / Framework Requirements

LangGraph (workflow, checkpoints, human-in-the-loop), FastAPI (REST API), Streamlit (UI), Pydantic v2, SQLite,
ReportLab (PDF), plus parsers for Terraform, YAML/JSON, XML diagrams, PDF, DOCX and PPTX. Everything runs locally:
no Docker, databases, queues or cloud services are needed.

## Installation

```bash
python -m venv .venv
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env     # optional; the offline demo needs no keys
```

## Start

```bash
python src/run.py
```

## Required APIs / MCP servers

| Integration | Needed for | Configure |
| --- | --- | --- |
| OpenAI API | live analysis (one provider is enough) | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Anthropic API | live analysis | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| AWS Bedrock (Converse) | live analysis | `AWS_BEARER_TOKEN_BEDROCK` or `AWS_PROFILE`, `AWS_REGION`, `BEDROCK_MODEL_ID` |
| MCP servers | not used | - |





* Application: <http://127.0.0.1:8501>
* API: <http://127.0.0.1:8000/api/v1> - Swagger UI: <http://127.0.0.1:8000/docs>
* `python src/run.py --no-browser` skips opening the browser.
* Ports and data directory are configurable in `.env` (`FASTAPI_PORT`, `STREAMLIT_PORT`, `LOCAL_DATA_DIR`).

## Run the offline demo (no credentials)

1. Open **New Review**, keep *Offline demo mode* on and pick **ShopSphere - AWS e-commerce platform**.
2. Press **Load this scenario**, then **Start Review**, and watch the six critics and the moderator on **Review Execution**.
3. In **Findings Explorer** inspect a Security and a Resilience finding, **Accept** one, **Dispute** another,
   **Defer** a concern (an ADR draft is created), then press **Submit decisions** and read the critic's rebuttal.
4. Open **Revise architecture**, tick *Apply predefined demo revision*, review the impact analysis and press
   **Run targeted re-review**: unaffected critics are not executed.
5. Open **Reports** for the scorecard, ADRs and the PDF/JSON downloads.

The complete script is in [docs/demo-guide.md](docs/demo-guide.md).

## Analyse a real architecture with an LLM

1. Configure a provider in `.env` (or the **Settings** page; keys entered there stay in memory only):

   | Variable | Purpose |
   | --- | --- |
   | `LLM_PROVIDER` | `anthropic`, `openai`, `bedrock` (or `mock`) |
   | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Anthropic Claude |
   | `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI |
   | `AWS_REGION`, `AWS_PROFILE`, `BEDROCK_MODEL_ID` | AWS Bedrock (standard AWS credential chain) |
   | `AWS_BEARER_TOKEN_BEDROCK` | Bedrock API key; when set it replaces the AWS profile / credential chain |
   | `DEMO_MODE` | default for the "offline demo" toggle |
   | `MAX_CONCURRENT_AGENTS`, `MAX_REVIEW_ROUNDS` | parallelism and round limit |
   | `MAX_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS` | prompt/response budgets |
   | `LOCAL_DATA_DIR`, `FASTAPI_PORT`, `STREAMLIT_PORT` | runtime locations and ports |

   Example for OpenAI (placeholders only; never commit real keys):

   ```dotenv
   LLM_PROVIDER=openai
   OPENAI_API_KEY=<your-openai-api-key>
   OPENAI_MODEL=gpt-4o
   ```

   Model IDs are editable defaults and are not guaranteed to be current; set the ones available to your account.
2. In **New Review** switch *Offline demo mode* off, choose the provider, upload files and assign each a role
   (primary design, diagram, infrastructure, standard, ADR, incident, supporting).
3. Press **Start Review**. **Live analysis sends relevant document content to the selected model provider.**

Raster diagrams (PNG/JPG/WEBP, scanned PDF pages) are only interpreted when the chosen provider/model supports
image input; otherwise they are preserved and reported as *not interpreted* rather than guessed.

## Sample input

The bundled **ShopSphere** scenario (`src/demo/fixtures/aws-ecommerce/`) is a typical review pack:

| File | Role |
| --- | --- |
| `architecture.md` | primary design document (requirements: 99.95% availability, RPO 5 min, ...) |
| `diagram.puml` | PlantUML component diagram |
| `infrastructure.tf` | Terraform for ALB, EC2 Auto Scaling, RDS PostgreSQL, S3, SQS, NAT gateway |
| `ops-incident-notes.md` | operational incident history |
| `revision-1/infrastructure.tf` | hardened revision used for the re-review round |

The flaws in it are planted on purpose: a public database, a hard-coded (fake) password, wildcard IAM, a single AZ,
disabled backups and more. Two more scenarios cover Kubernetes and a hybrid on-premises setup.

## Sample output

A live run of ShopSphere (OpenAI `gpt-4o`) produced 26 findings from 6 critics in round 1. After the revised Terraform
was uploaded, the targeted re-review marked 12 findings RESOLVED. The review finished as `COMPLETED_WITH_OPEN_RISKS`
with a PDF and JSON report. Counts vary between runs. One finding from the JSON report (abridged):

```json
{
  "title": "Encryption Disabled for Database Storage",
  "severity": "CRITICAL",
  "domain": "security",
  "status": "OPEN",
  "evidence_level": "NEEDS_VALIDATION",
  "moderator_status": "REQUIRES_VALIDATION",
  "affected_components": ["aws_db_instance.orders"],
  "recommendation": "Enable encryption for the database storage using AWS KMS-managed keys."
}
```

The report also contains `executive_summary`, `scorecard` (per-domain score, band and coverage), `decisions`, `adrs`,
`review_history`, `traceability`, `limitations` and `disclaimers`. ADR drafts are written as Markdown next to the report.

## Interpreting findings

| Field | Meaning |
| --- | --- |
| Severity | CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL |
| Evidence level | **CONFIRMED** - a tool/configuration fact directly shows it; **INFERRED** - reasoned from evidence; **NEEDS_VALIDATION** - documentation is silent, not proof of absence |
| Moderator status | VALIDATED, MERGED (several critics), REQUIRES_VALIDATION, REJECTED (unsupported, kept with explanation) |
| Principle origin | Organizational standard vs control mapping vs general practice - clearly labelled |
| Status | OPEN, ACCEPTED (valid, not remediated), DISPUTED, REBUTTED, REJECTED, DEFERRED (risk acknowledged, not resolved), REMEDIATION_PROPOSED, RESOLVED, VERIFIED |

Scores are indicative (rounded to 5), separate confirmed from unverified concerns and treat missing information as
lower *coverage*, not failure. A review ends `COMPLETED` only when no Critical/High finding remains open; otherwise
`COMPLETED_WITH_OPEN_RISKS`. The AI never approves an architecture. Compliance observations are possible control
gaps, not certifications or legal determinations.

## Where things are saved

| Artifact | Location |
| --- | --- |
| Uploaded files | `data/uploads/<project>/<artifact>/` |
| PDF / JSON reports | `data/reports/<review>/` |
| ADR drafts (Markdown) | `data/reports/<review>/adrs/` |
| Snapshots | `data/snapshots/` |
| LangGraph checkpoints | `data/checkpoints/langgraph.sqlite` |
| Logs | `data/logs/` |

## Tests

```bash
python -m pytest            # whole suite, offline, no API keys
python -m ruff check src tests
python src/scripts/demo_reset.py --checks-only --connectivity   # fixtures parse + live provider reachable
```

Tests that call real providers are marked `live` and are deselected by default (`pytest -m live` with keys set).

## Repository map

```
Agent.md              Agentathon submission form: problem, capabilities, value, security
README.md             this file: setup and usage
src/
  run.py              one-command launcher
  backend/            FastAPI app, services, parsers, LLM layer, agents, LangGraph graph, tools, reporting
  frontend/           Streamlit multipage UI (calls the REST API only)
  knowledge/          sample standards, control mappings, agent rubrics, scoring rubric
  demo/               offline scenarios, seeded findings, expected results
  scripts/            demo reset / pre-demo checks, submission packaging
tests/                unit, workflow and integration tests
docs/                 system design, setup, workflows, API, data model, persistence, demo scripts, troubleshooting, limitations
data/                 durable local state (uploads, reports, snapshots, checkpoints, logs); not part of the submission
```

See [system-design.md](docs/system-design.md) for the design and [docs/limitations.md](docs/limitations.md) for what this POC does not do.

## Deployment

This is a local proof of concept: run it on a workstation with `python src/run.py`. It binds to `127.0.0.1` and has no
authentication, so it must not be deployed to a shared or public host as it is.

## Packaging for the AI By Design Agent Library

```bash
python src/scripts/package_submission.py
```

This creates `dist/Rahul_Verma_694836/` (and a `.zip`) containing `Agent.md`, `README.md`, `src/`, `tests/`, `docs/` and
the setup files. It leaves out `.env`, `data/`, `.venv`, `.git` and caches, and refuses to package anything that looks
like a real secret. Upload the folder's contents to your assigned SharePoint folder.

## Known limitations (summary)

* LLM-based inference can be wrong; diagram understanding is deterministic only for structured formats.
* Deterministic checks cover the implemented Terraform/CloudFormation/Kubernetes constructs only.
* Offline findings are fixture-driven and demonstrate the workflow, not model capability.
* In-process background execution: runs are lost if the backend stops (checkpoints allow continuing).
* No authentication; local use only. A Windows executable is an optional future extension (e.g. PyInstaller around `src/run.py`), not part of this POC.
