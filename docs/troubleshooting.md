# Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Port 8000 (FastAPI) is already in use` | another process uses the port | stop it or set `FASTAPI_PORT` / `STREAMLIT_PORT` in `.env` |
| `Missing Python packages: ...` | dependencies not installed in the interpreter running `src/run.py` | activate the venv and `pip install -r requirements.txt` |
| `FastAPI backend did not become ready` | import error or slow first start | read `data/logs/backend.out.log`; run `python -m uvicorn --app-dir src backend.main:app` directly to see the traceback |
| Streamlit shows "Backend not reachable" | backend not running or `ART_BACKEND_URL` wrong | use `python src/run.py`; for manual starts set `ART_BACKEND_URL=http://127.0.0.1:8000/api/v1` |
| Review `FAILED` with `provider_not_configured` | live mode without a key / model access | add `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` etc. in Settings or `.env`, use **Validate** on Settings; the app never falls back to simulated results |
| Review `FAILED`: `All specialist agents failed` | provider auth/rate-limit/timeouts | see the first lines of the error and `GET /reviews/{id}/events`; lower `MAX_CONCURRENT_AGENTS` for rate limits |
| One agent `FAILED`, others fine | transient provider error or malformed output after one repair attempt | review is usable (partial analysis flagged); re-run with a new review |
| Review `FAILED`: "backend restarted while this review was running" | the backend stopped mid-run | press **Start** to continue from the checkpoint (or restart if none exists) |
| Review stuck `AWAITING_HUMAN` but resume does nothing | decisions not submitted | use **Submit decisions**; `GET /reviews/{id}/status` shows `pending_decisions` |
| `409 Decisions can only be recorded while AWAITING_HUMAN` | review is running or finished | wait for the interrupt; check `GET /status` |
| Image/scanned PDF produces no components | no vision-capable provider (always true in demo mode) | use a live provider with image support; the file stays available for manual inspection |
| Terraform file fails to parse | unsupported HCL syntax | check `parse_error` in the documents table; split or simplify the file |
| Checkov shows `NOT_EXECUTED` | Checkov not installed | `pip install checkov` or ignore: built-in checks still run |
| `Snapshot save failed` in the log | disk/permissions | check write access to `data/snapshots` |
| Browser did not open | `--no-browser` or no default browser | open <http://127.0.0.1:8501> manually |
| PDF is missing characters | standard PDF fonts only cover Latin-1 | non-Latin text is replaced with `?` in the PDF; the JSON report is lossless |

## Useful commands
```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/reviews/<id>/events | python -m json.tool
python -m pytest tests/workflow -q          # verifies the orchestration locally
```

Logs: `data/logs/backend.log` (rotating, secrets redacted), `data/logs/backend.out.log`, `data/logs/streamlit.out.log`.
