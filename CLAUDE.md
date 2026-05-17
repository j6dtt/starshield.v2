# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project structure

```
starshield.v2/
├── docker-compose.yml           — sequential pipeline (app.py)
├── docker-compose.master.yml    — parallel pipeline (master.py + workers)
├── docker-compose.editor.yml    — config editor UI
├── .gitignore
└── data/
    ├── app.py               — sequential pipeline entry point (fallback)
    ├── master.py            — parallel pipeline orchestrator
    ├── worker.py            — ephemeral single-account worker
    ├── starshield_data.py   — get_starshield_data pipeline
    ├── config_editor.py     — Flask web UI for account management
    ├── config.json          — account and feature configuration
    ├── .env                 — secrets (not committed)
    └── tls/                 — TLS certs for config editor (not committed)
```

## Running the app

**Sequential pipeline (fallback):**
```bash
docker compose up -d
```

**Parallel pipeline (recommended):**
```bash
docker compose -f docker-compose.master.yml up -d
```

**Config editor UI (separate, always-on):**
```bash
docker compose -f docker-compose.editor.yml up -d
```

**Local (sequential):**
```bash
export CLIENT_SECRET=your_secret_here
cd data
python3 app.py
```

Output is written to `./data/_1.allterms.json`.

## Environment variables

| Variable          | Source                      | Description                                      |
|-------------------|-----------------------------|--------------------------------------------------|
| `CLIENT_SECRET`   | `.env`                      | OAuth client secret for Starlink API             |
| `UI_USER`         | `.env`                      | Config editor login username                     |
| `UI_PASS`         | `.env`                      | Config editor login password                     |
| `HOST_DATA_PATH`  | `docker-compose.master.yml` | Host absolute path to `./data` for worker mounts |

`HOST_DATA_PATH` is set directly in `docker-compose.master.yml` (not `.env`) since it is deployment-specific, not a secret.

## Architecture

Two pipeline modes share the same `starshield_data.py` and `config.json`.

### Parallel pipeline — `master.py` (primary)

Master container spawns one ephemeral worker container per account simultaneously each cycle via the Docker SDK. Workers output terminal data as JSON to stdout; master aggregates, converts to CEF, sends UDP.

**Execution flow:**
1. Startup: reads `HOST_DATA_PATH`, connects to Docker daemon (`unix://var/run/docker.sock`, pool=100), pings — exits if unreachable
2. Each cycle (`load_config()` fresh every iteration):
   - Deduplicates and filters `mode=skip` accounts
   - `ThreadPoolExecutor(max_workers=len(accounts))` — submits all accounts simultaneously
   - Each thread: `docker_client.containers.run(name='starshield.v2.<account_num>', detach=True)` → `container.wait(timeout=120)` → `container.logs()` → `container.remove(force=True)`
   - Results collected via `as_completed()` — best-effort (failed accounts logged, not fatal)
   - Last-known-good merge: loads existing `_1.allterms.json`, preserves terminals from failed accounts, replaces terminals from successful accounts (handles deactivated terminals correctly)
   - Writes merged dataset, CEF log, UDP send
   - `sleep_for = max(0, 60 - elapsed)` — wall-clock accurate 60s interval
3. Typical cycle time: 7–10s for 37 accounts (vs sequential ~several minutes)

**Worker (`worker.py`):**
- Receives `ACCOUNT_JSON`, `CLIENT_SECRET`, `GRANT_TYPE`, `REQUEST_TIMEOUT` via env vars
- Mounts `HOST_DATA_PATH:/app:ro` to access `starshield_data.py`
- Logging → stderr (stdout reserved for JSON payload)
- `print(json.dumps(terms))` → captured by master
- `sys.exit(0)` success / `sys.exit(1)` failure

### Sequential pipeline — `app.py` (fallback)

Same logic as master but single-threaded. Applies identical best-effort error handling and last-known-good merge. Use `docker compose up -d` (no `-f` flag).

### Last-known-good merge strategy (both pipelines)

- Load existing `_1.allterms.json` at end of each cycle
- Terminals whose `accountNumber` is in the failed set → preserved from existing file (stale but better than missing)
- Terminals from successful accounts → replaced entirely with fresh API data
- Deactivated terminals (no longer returned by API for a successful account) → naturally removed
- Merged dataset feeds CEF conversion and UDP send

### Config editor — `config_editor.py`

Flask app served via gunicorn + TLS on port 5001. Manages `config.json` accounts (add/edit/delete). Streams live logs from `starshield.v2` container via Docker socket SSE. Auth via `UI_USER`/`UI_PASS` in `.env`. Run with `docker-compose.editor.yml`.

### `get_starshield_data` pipeline (in order)
- `/account` — fetches `accountNumber` and `accountName`
- `/contacts` — derives `component` from a contact email prefix (`__COMPONENT@...`)
- `/user-terminals` — paginated fetch; mode controls the query:
  - `full` — fetches all terminals
  - `include` — filters by `service_lines` list (must be non-empty)
  - `skip` — skips the account
- Dataset initialization — builds terminal dicts with zeroed/empty fields
- `/data-usage/query` — paginated POST; enriches terminals with plan and usage data
- `/telemetry/query` — single POST; enriches terminals with live metrics, status, alerts, IP, and H3 geo

### `config.json` structure
```
config.authentication.accounts[]
  account_num       — used in log messages and worker container names
  client_id         — OAuth client ID
  client_secret     — placeholder ("_secret_"), real secret via CLIENT_SECRET env var
  accountquery
    mode            — "full" | "include" | "skip"
    service_lines[] — required (non-empty) when mode is "include"
config.authentication.grant_type  — always "client_credentials"
config.request_timeout            — per-request timeout in seconds (required, currently 15)
config.cef                        — CEF message formatting (enable flag)
config.remote_server              — remote syslog destination (enable flag)
```

### Error handling strategy
- Per-account errors → logged, account added to `failed` list, cycle continues (best-effort)
- Master cycle-level errors → logged, sleep, retry next cycle
- Worker exit code != 0 → master treats as failed account, preserves stale terminals
- `request_timeout` is required in `config.json` — missing key raises `KeyError`
- Do NOT commit: `.env`, `data/tls/`, `data/config.json`
