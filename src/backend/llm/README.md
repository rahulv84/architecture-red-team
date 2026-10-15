# backend/llm

Provider-neutral LLM access. Agents and services depend only on `LLMProvider`; SDK details,
credentials, retries, concurrency limits and structured-output validation live here.

## Purpose and responsibility

* Define the provider interface and the structured-generation pipeline (`generate_structured`).
* Provide adapters for Anthropic, OpenAI, Google Gemini and AWS Bedrock.
* Provide `MockProvider`, the deterministic offline provider that powers SIMULATED DEMO MODE and
  the test suite.

## Modules

| File | Contents |
| --- | --- |
| `base_provider.py` | `LLMProvider` (ABC), `StructuredRequest`, `StructuredResponse`, `RawCompletion`, `ProviderCapabilities`, `ProviderValidation`, `ConcurrencyLimiter`, module-level `LIMITER`, `extract_json()` |
| `contracts.py` | Output contracts: `RebuttalOutput`, `ModeratorReviewOutput` / `ModeratorAssessment`, `ExtractionOutput` (+ `ExtractedComponent`, `ExtractedRelationship`, `ExtractedNfr`), `VisionDiagramOutput` |
| `provider_factory.py` | `create_provider(name, settings)`, `provider_catalog(settings)`, `_PROVIDERS` map |
| `anthropic_provider.py` | `AnthropicProvider` (Messages API; structured output via the `submit_result` tool with `tool_choice: auto` - current Claude models reject forced tool choice; `max_tokens` truncation raises `ProviderError`) |
| `openai_provider.py` | `OpenAIProvider` (Chat Completions with `response_format=json_object`, `max_completion_tokens`) |
| `gemini_provider.py` | `GeminiProvider` (`google-genai` `generate_content`; structured output via `response_mime_type=application/json` + schema in the system instruction; SDK retries disabled; `MAX_TOKENS` truncation, safety blocks and daily/zero quotas (e.g. free tier) raise a non-retryable `ProviderError`; thinking tokens counted as output; defaults `gemini-3.8-flash` (mid tier), `gemini-3.1-pro-preview` is the top tier) |
| `bedrock_provider.py` | `BedrockProvider` (Converse API; forced tool use; auth via Bedrock API key `AWS_BEARER_TOKEN_BEDROCK` - direct HTTPS call to the Converse REST endpoint, independent of the boto3 version - or else boto3 session/profile; temperature 0.1 - for models that reject forced `toolChoice` or `temperature` (e.g. Sonnet/Opus 5.5) it retries once without them and remembers that per model id) |
| `mock_provider.py` | `MockProvider` (fixture-driven) |

## `LLMProvider` pipeline

Subclasses implement `model_id`, `capabilities()`, `validate_configuration()` (no network),
`_complete(system, user, *, max_tokens, schema, schema_name) -> RawCompletion` and optionally
`_complete_image` and `check_connectivity()`.

`generate_structured(request)`:
1. Appends the response model's JSON Schema to the system prompt.
2. Calls `_complete` through `_call_with_retries`: takes the global `LIMITER` semaphore; on
   `ProviderRateLimitError` / `ProviderTimeoutError` retries up to `settings.llm_max_retries` with
   exponential backoff (`min(30, 2**n)` s plus jitter).
3. Parses with `extract_json` (tolerates fences and prose) and validates against the Pydantic model.
   On failure it makes **one** repair attempt that includes the validation error, then raises
   `StructuredOutputError`.
4. Returns `StructuredResponse(parsed, raw_text, usage: UsageRecord)`.

`analyze_text` and `analyze_image` are the free-text / vision primitives (`analyze_image` is used
for diagram interpretation and requires `capabilities().vision`).

## `MockProvider` fixture format

Location: `demo/seeded_findings/<scenario_id>.json` (selected by `Review.scenario_id`).
Top-level keys: `scenario_id`, `title`, `disclaimer`, `information_gaps` (agent id -> list of
strings), `conflicts` (list), `flaws` (list). Each flaw:

| Key | Meaning |
| --- | --- |
| `flaw_id` | Unique id; must match `[A-Z0-9-]+` because it is embedded in `reasoning_summary` as `fixture <id>` and parsed back for rebuttals and moderator verdicts |
| `agent_id` | Specialist that emits it |
| `severity`, `evidence_level`, `confidence`, `category` | Copied into the `FindingDraft` |
| `title`, `description`, `scenario`, `impact`, `recommendation` | Text; `{components}` is replaced with affected component names (use `{{`/`}}` for literal braces) |
| `principle`, `principle_origin`, `validation_method`, `assumptions`, `reasoning` | Optional metadata |
| `affected_components` | Optional explicit override of computed affected components |
| `applies_when` | Condition (see below); absent means `{"always": true}` |
| `evidence_hints` | Substrings; matching `DOC:` catalog items are cited (max 2) |
| `knowledge_hints` | Substrings; first matching `KB:` item is cited |
| `seed_type` | `unsupported_claim` (cites no evidence) or `hallucination` (describes something not in the model) - exercise moderator validation/rejection |
| `moderator_verdict`, `moderator_note` | `KEEP` (default), `DOWNGRADE`, `NEEDS_VALIDATION`, `REJECT` returned by the mock moderator pass |
| `rebuttal` | `stance` (default `MAINTAIN`), `argument`, `concede_if_justification_contains` (keywords), `concede_argument`, `revised_severity`, `open_questions` |

`applies_when` keys (all that are present must hold):

| Key | Condition |
| --- | --- |
| `tool_rule_ids` | At least one `VIOLATION` tool result with one of these rule ids; its components and TOOL ids become evidence |
| `tool_component_contains` | With `tool_rule_ids`: restrict to tool results whose component id contains the string |
| `components` | List of matchers; each needs a component with `type`, `name_contains`, `config_equals` (dict), `config_missing` (keys), `config_present` (keys) |
| `region_any` | Any listed region is in `model.deployment_regions` |
| `model_empty` | Listed model fields (for example `auth_mechanisms`) are empty |
| `model_nonempty` | Listed model fields are non-empty |
| `nfr_category` | An NFR with this category exists |
| `always` | Unconditional (this is also the behaviour when `applies_when` is missing; unrecognised keys are silently ignored) |

`MockProvider` handles tasks `agent_review`, `rebuttal`, `moderator_review`; any other task raises
`ProviderError`. `capabilities()` reports `simulated=True`, `vision=False`. Responses sleep
`mock_latency_seconds` (scaled by agent) to make the UI progress visible; usage `status` is
`SIMULATED`. A flaw is only emitted when its condition matches the *current* model, so fixed
architectures stop producing it.

## Inputs and outputs

* Input: `StructuredRequest(task, system_prompt, user_prompt, response_model, agent_id, context)`.
  `context` carries `scenario_id`, the model dump, tool results and evidence catalog for the mock
  only; live providers see just the prompts.
* Output: validated Pydantic object plus `UsageRecord` (tokens, latency, retries).

## Dependencies

`core.config` (`Settings`), `core.exceptions`, `models.domain` (`UsageRecord`, `FindingDraft`,
`AgentReviewOutput`, `Severity`). SDKs (`anthropic`, `openai`, `google-genai`, `boto3`) are imported lazily inside
adapter methods.

## Role in application execution

`AppContainer.provider_for(review)` picks the provider: `mock` for `mode == "demo"`, otherwise
`create_provider(review.provider, settings)` with the review's model id override. Used by
`ArchitectureService` (vision, augmentation), `BaseAgent.analyze`, `ModeratorAgent._llm_pass`,
`request_rebuttal`, and `/providers` endpoints. `LIMITER.resize` is called from the factory and
from `PUT /settings`.

## Extension points

Adding a provider:
1. Subclass `LLMProvider` in `<name>_provider.py`; set `name`; implement the abstract members and
   translate SDK errors into `ProviderAuthError` / `ProviderRateLimitError` /
   `ProviderTimeoutError` / `ProviderError`.
2. Register it in `provider_factory._PROVIDERS` and add the name to
   `core.config.SUPPORTED_PROVIDERS`.
3. Add settings fields (and to `EDITABLE_FIELDS`/`SettingsUpdate` as needed) and the model-id
   mapping in `AppContainer.provider_for` and `ReviewService.create_review`.

Adding a scenario fixture: see `demo/README.md`.

## Limitations

* `MockProvider` output is canned; it never reasons over documents and has no vision.
* OpenAI structured output uses `json_object` mode plus schema-in-prompt (not strict JSON-schema
  mode); validity is enforced by the repair loop. Gemini does the same with JSON mode: the native
  `response_json_schema` is not used because Gemini rejects parts of Pydantic's JSON Schema output.
* Vision capability of Anthropic/OpenAI/Gemini/Bedrock is assumed, not probed.
* No token accounting beyond provider-reported usage; `UsageRecord.estimated_cost_usd` is never set.
* Retries cover rate limits and timeouts only; auth/config errors fail immediately.
