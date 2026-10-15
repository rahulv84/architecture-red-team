# Limitations

This is a local proof of concept. The points below are intentional boundaries or known gaps.

## Analysis quality
* **LLM inference can be wrong.** Findings carry confidence and evidence levels; INFERRED and NEEDS_VALIDATION findings are hypotheses. The moderator reduces but cannot eliminate hallucinations (it checks references, tool support and known components, not semantic truth).
* **Absence of documentation is not evidence of absence.** Critics are instructed to use NEEDS_VALIDATION, and rules that depend on information that may live elsewhere (audit trail, alarms, flow logs, caches) report NEEDS_VALIDATION.
* **Coverage depends on what is supplied.** Unknown information lowers *coverage*, listed as information gaps; it is not scored as failure.
* No security testing or pentesting is performed; STRIDE analysis is reasoning over the supplied design.

## Diagram and document understanding
* Deterministic structure extraction exists for Draw.io XML (plain and compressed), Mermaid flowcharts/sequence diagrams (common syntax only), PlantUML component/deployment syntax (common constructs) and SVG text labels (no connectors).
* Raster images and scanned PDF pages need a vision-capable live provider; OCR is not built in. In demo mode images are always reported as not interpreted.
* Vision-extracted components/relationships are flagged INFERRED with reduced confidence and should be verified.
* Narrative text extraction uses a lexicon and patterns (components, NFRs, regions, controls); unusual wording is missed. LLM-assisted extraction (live mode) adds inferred items but is also fallible.
* Merging diagram nodes with IaC resources uses name/token/type heuristics and may mismatch similar names.
* Kubernetes ConfigMaps shared via `envFrom` by several workloads are not attributed to individual workloads (reported as an extraction limitation).

## Deterministic checks
* 38 rules covering selected Terraform (AWS-centric), CloudFormation and Kubernetes constructs; Azure/GCP resources are recognised as components but have almost no rules. Variables, modules, `count/for_each` and data sources are not evaluated.
* Terraform is parsed with python-hcl2: expressions are kept as strings and never executed; unsupported syntax makes the file `FAILED` with an explanation.
* Checkov integration runs only if Checkov is installed and is limited to Terraform/CloudFormation/Kubernetes files; results are normalised but not de-duplicated against built-in rules except by moderation.
* Rules are point-in-time and static: runtime state, drift and effective permissions are not known.

## Offline demo
* Findings in demo mode come from fixtures (`src/demo/seeded_findings/*.json`) whose conditions are matched against the **real** extracted model/tool output. They demonstrate the workflow and moderation; they are not evidence of model capability. The UI and reports label them SIMULATED.
* Seeded fixtures intentionally include one unsupported claim and one hallucinated claim per scenario to exercise moderation.
* The evaluation harness reports observed precision/recall on those fixtures only. No target percentages are claimed; 15-20 labelled architectures are a future extension (add folders under `src/demo/`).

## Compliance and FinOps
* Compliance results map architecture evidence to control **themes** (paraphrased GDPR / ISO 27001 / PCI DSS headlines). They are possible control gaps, **not** certifications, audits or legal determinations. Proprietary standard text is not reproduced.
* FinOps assessments are qualitative cost-risk statements. No pricing source (AWS Pricing API, Infracost) is used and no prices are invented; cost fields show N/A.

## Scoring
* Scores are indicative, rounded to 5, and depend on the rubric in `src/knowledge/agent_rubrics/scoring.yaml`. They are not an approval and do not replace architect judgement.

## Security and operations of the POC
* No authentication/authorization, TLS or multi-user separation; loopback only. Do not expose it publicly.
* Uploaded content is parsed with safe loaders but a determined malformed file can still cause a parse failure; decompression/alias/entity limits are heuristics.
* Prompt-injection resistance relies on delimiting untrusted evidence, citation-by-ID and schema validation; it is not a formal guarantee.
* Live mode sends relevant document content to the selected provider; there is no redaction beyond not including secret values in the extracted model.
* Gemini structured output uses JSON mode plus the schema in the prompt (not Gemini's native schema enforcement), so validity depends on the repair loop as with OpenAI. The top-tier default suggestion (`gemini-3.1-pro-preview`) is a preview model and may change or be retired by Google.

## Persistence and execution
* The application DB is volatile; durability relies on debounced JSON snapshots (a crash within the debounce window can lose the last second of writes; decisions are also kept in checkpoints once submitted).
* Background tasks are in-process; a restart does not auto-resume running reviews.
* Reconstructed threads (missing checkpoint) restore findings and decisions, not the original graph history.

## Deferred beyond the POC
Windows executable packaging, richer graph visualisation, per-tenant settings, user authentication, embedding-based retrieval,
cost estimation integrations, additional IaC frameworks (Bicep, Pulumi, Helm templating), diff views for architecture models,
and a larger labelled evaluation corpus.
