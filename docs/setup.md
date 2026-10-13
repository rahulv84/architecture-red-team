# Setup

## Requirements
* Python 3.11+ (3.11 is what the suite was developed and tested on)
* A working `pip` with access to PyPI for the one-time dependency installation
* No Docker, databases, brokers or cloud services are needed

## One-time installation
```bash
git clone <repo> architecture-red-team   # or unpack the folder
cd architecture-red-team
python -m venv .venv
.\.venv\Scripts\Activate.ps1             # Windows PowerShell
# source .venv/bin/activate              # macOS / Linux
pip install -r requirements.txt
```
Optional extras:
* `pip install checkov` enables the Checkov adapter (otherwise reported as `NOT_EXECUTED`).

## Configuration
Copy `.env.example` to `.env` and edit. Every variable is optional for the offline demo.

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `mock` | `anthropic`, `openai`, `bedrock`, `mock` |
| `DEMO_MODE` | `true` | default state of the "Offline demo mode" toggle |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | - / `claude-sonnet-5-5` | model IDs are editable defaults; check availability for your account |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | - / `gpt-4o` | |
| `AWS_REGION` / `AWS_PROFILE` / `BEDROCK_MODEL_ID` | `us-east-1` / - / `anthropic.claude-3-5-sonnet-20241022-v2:0` | uses the standard AWS credential chain |
| `AWS_BEARER_TOKEN_BEDROCK` | - | Bedrock API key (bearer token); takes precedence over the credential chain; write-only, never persisted |
| `MAX_CONCURRENT_AGENTS` | `6` | parallel critics and provider calls |
| `MAX_REVIEW_ROUNDS` | `3` | targeted re-review limit |
| `MAX_INPUT_TOKENS` / `MAX_OUTPUT_TOKENS` | `60000` / `16000` | prompt/response budgets |
| `LOCAL_DATA_DIR` | `data` | durable state location |
| `FASTAPI_PORT` / `STREAMLIT_PORT` | `8000` / `8501` | |
| `MOCK_LATENCY_SECONDS` | `1.0` | simulated per-agent delay in demo mode so progress is visible |

Settings can also be changed at runtime on the **Settings** page; non-secret overrides are saved to
`data/settings.json`, API keys stay in memory only.

## Verify the installation
```bash
python -m pytest        # ~1 minute, offline
python src/run.py           # opens http://127.0.0.1:8501
```
Health check: `curl http://127.0.0.1:8000/api/v1/health`.

## Stopping
Press **Ctrl+C** in the terminal running `src/run.py`; both child processes are terminated.

## Optional: Windows executable
Not part of the POC. A standalone `.exe` can be produced by wrapping `src/run.py` with PyInstaller
(`pyinstaller --onedir run.py` plus data files for `src/backend/`, `src/frontend/`, `src/knowledge/`, `src/demo/`); this is untested here.
