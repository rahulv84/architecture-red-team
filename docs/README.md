# docs

Project documentation for Architecture Red Team. Start with `setup.md` to run the application and
`architecture.md` to understand how it is built. Folder-level READMEs next to the code (for example
`src/backend/graph/README.md`) describe individual modules; the documents below describe the system.

| Document | Contents |
| --- | --- |
| [setup.md](setup.md) | Prerequisites, installation, configuration (`.env`), running with `python src/run.py`, running tests, enabling live providers and Checkov |
| [architecture.md](architecture.md) | System components, layers, runtime view (processes, container, threads), dependency direction and key design decisions |
| [agent-workflows.md](agent-workflows.md) | The LangGraph review workflow: nodes, specialist subgraph, moderation, human interrupt, rebuttals, revisions and re-review rules |
| [api-reference.md](api-reference.md) | REST endpoints under `/api/v1`, request/response shapes, status codes and error format |
| [data-model.md](data-model.md) | Domain entities and enums (reviews, findings, decisions, ADRs, architecture model), evidence IDs and lifecycle states |
| [persistence.md](persistence.md) | In-memory SQLite, JSON snapshots, LangGraph checkpoints, file layout under `data/`, restart and recovery behaviour |
| [demo-guide.md](demo-guide.md) | Walkthrough of the three offline demo scenarios, what to click, what to expect and how to add a scenario |
| [troubleshooting.md](troubleshooting.md) | Common start-up, upload, provider and workflow problems with causes and fixes |
| [limitations.md](limitations.md) | Known limitations, unsupported inputs, security posture of the POC and what is intentionally not implemented |

## Related READMEs

* `src/backend/README.md` - backend overview and sub-package index.
* `data/README.md`, `src/knowledge/README.md`, `src/demo/README.md`, `tests/README.md` - runtime data,
  knowledge base, demo fixtures and test suite.
