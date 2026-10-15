# scripts

Operator tooling that runs outside the app. Scripts reuse backend modules (settings, parsers, providers) and contain no
business logic of their own.

| Script | Purpose |
| --- | --- |
| `demo_reset.py` | Reset local state before a demo and run pre-demo checks |
| `package_submission.py` | Build the AI By Design Agentathon upload folder (`dist/<Firstname>_<Lastname>_<EmpID>/` + zip) with no secrets or local state |

## demo_reset.py

Run it from the repository root **with the app stopped**:

```bash
.venv\Scripts\python src/scripts/demo_reset.py                  # preview, confirm, clean, check
.venv\Scripts\python src/scripts/demo_reset.py --connectivity   # also send one small request to the live provider
.venv\Scripts\python src/scripts/demo_reset.py --dry-run        # report only
```

| Option | Effect |
| --- | --- |
| `--yes`, `-y` | do not ask for confirmation (required when not run from a terminal) |
| `--dry-run` | list what would be removed; delete nothing |
| `--keep-settings` | keep `data/settings.json` (UI overrides that take precedence over `.env`) |
| `--checks-only` / `--skip-checks` | run only the checks / only the clean-up |
| `--connectivity` | test the configured live provider end to end (one tiny request) |

**Clean-up** removes everything under `uploads/`, `reports/`, `snapshots/`, `checkpoints/` and `logs/` in the data directory
(`LOCAL_DATA_DIR`), plus `settings.json` unless `--keep-settings` is given. It keeps the `.gitkeep` placeholders and
`data/README.md`, and never touches `.env`, the repository or `demo/`. It refuses to run when:
* the app is still running (the FastAPI or Streamlit port answers), because deleting the open SQLite checkpoint would corrupt it, or
* the data directory resolves to the repository root, a parent of it, the home directory, a filesystem root, or a folder
  with none of the expected subfolders.

**Checks** (each reported as `OK`, `WARN` or `FAIL`):
| Check | Catches |
| --- | --- |
| `python-hcl2` | `bc-python-hcl2` (installed by checkov) that can overwrite the Terraform parser (WARN) |
| `fixtures/<scenario>` | any scenario or `revision-1` file that no longer parses (FAIL) |
| `fixtures/<scenario> extra files` | files in a fixture folder that `scenario.json` doesn't list (WARN) |
| `settings.json` | saved UI settings that override `.env` (only with `--keep-settings`) (WARN) |
| `provider` | missing or invalid provider configuration; with `--connectivity`, expired credentials or no model access (FAIL); `mock` (WARN) |

Exit code: `0` ready, `1` a check failed, `2` the clean-up was refused or aborted.
Tests: `tests/unit/test_demo_reset.py` (always uses temporary directories).

## package_submission.py

```bash
.venv\Scripts\python src/scripts/package_submission.py                  # dist/Rahul_Verma_694836/ + .zip
.venv\Scripts\python src/scripts/package_submission.py --name A_B_123   # another <Firstname>_<Lastname>_<EmpID>
```

Copies `Agent.md`, `README.md`, `src/`, `tests/`, `docs/`, `requirements.txt`, `pyproject.toml`, `.env.example`,
`.gitignore` and `CLAUDE.md`. Files ignored by git are left out, and `.env`, `data/`, `dist/`, `.venv`, `.git`, caches,
SQLite files and `settings.json` are always excluded. Before writing anything it scans every text file:

| Finding | Effect |
| --- | --- |
| a value of a `*KEY*` / `*TOKEN*` / `*SECRET*` / `*PASSWORD*` variable from your real `.env` | **blocks** (the value is never printed) |
| AWS access key id, Anthropic / OpenAI / Google (Gemini) / Bedrock API key, private key | **blocks**, except the fixed placeholders used by the test suite |
| hard-coded `password = "..."` | warning only (the demo fixtures contain planted fake passwords) |

Exit code: `0` packaged, `1` possible secrets found (nothing written), `2` invalid name or missing required files.
Tests: `tests/unit/test_package_submission.py`.
