# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project structure

```
starshield.v2/
├── docker-compose.yml
├── .gitignore
└── data/
    ├── app.py               — entry point
    ├── starshield_data.py   — get_starshield_data pipeline
    ├── config.json          — account and feature configuration
    └── .env                 — secrets (not committed)
```

## Running the app

**Local:**
```bash
export CLIENT_SECRET=your_secret_here
cd data
python3 app.py
```

**Docker:**
```bash
docker compose up
```

Output is written to `./data/_1.allterms.json`.

## Environment variables

| Variable        | Source  | Description          |
|-----------------|---------|----------------------|
| `CLIENT_SECRET` | `.env`  | OAuth client secret  |

Docker Compose injects variables from `./data/.env` automatically. For local runs, export them in the shell — `python-dotenv` is not used.

## Architecture

`app.py` is a data collection pipeline that queries the Starlink API for multiple accounts and aggregates terminal telemetry into a single dataset. `get_starshield_data` lives in `starshield_data.py`.

### Execution flow (`__main__`)
1. `load_config()` — reads `config.json`, returns the `config` object
2. Deduplicates accounts by `account_num`; duplicates are logged as warnings and skipped
3. Iterates over deduplicated accounts
4. `get_auth_headers(account, grant_type)` — authenticates against `api.starlink.com` and returns bearer token headers
5. `get_starshield_data(headers, account, timeout)` — runs the full data collection pipeline for that account and returns a list of enriched terminal dicts
6. All account results are merged into `all_terms` and written to `_1.allterms.json`
7. Optionally converts to CEF format and forwards to remote syslog
8. Loop repeats every 60 seconds; `KeyboardInterrupt` exits cleanly

### `get_starshield_data` pipeline (in order)
- `/account` — fetches `accountNumber` and `accountName`
- `/contacts` — derives `component` from a contact email prefix (`__COMPONENT@...`)
- `/user-terminals` — paginated fetch; mode controls the query:
  - `full` — fetches all terminals
  - `include` — filters by `service_lines` list (must be non-empty)
  - `skip` — skips the account with a warning
- Dataset initialization — builds terminal dicts with zeroed/empty fields
- `/data-usage/query` — paginated POST; enriches terminals with plan and usage data
- `/telemetry/query` — single POST; enriches terminals with live metrics, status, alerts, IP, and H3 geo

### `config.json` structure
```
config.authentication.accounts[]
  account_num       — used in log messages
  client_id         — OAuth client ID
  accountquery
    mode            — "full" | "include" | "skip"
    service_lines[] — required (non-empty) when mode is "include"
config.authentication.grant_type  — always "client_credentials"
config.request_timeout            — per-request timeout in seconds (required, currently 15)
config.cef                        — CEF message formatting (enable flag)
config.remote_server              — remote syslog destination (enable flag)
```

### Error handling strategy
- Any error anywhere in `get_starshield_data` (including `requests.Timeout`) → logged and re-raised, aborts entire account iteration, cycle restarts after 60s sleep
- Any per-account error in `__main__` → logged and re-raised, exits account loop, outer handler sleeps and restarts
- `request_timeout` is required in `config.json` — missing key raises `KeyError` at startup
