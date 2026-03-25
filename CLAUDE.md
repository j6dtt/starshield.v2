# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python app.py
```

Output is written to `./_allterms.json`.

## Architecture

`app.py` is a data collection pipeline that queries the Starlink API for multiple accounts and aggregates terminal telemetry into a single dataset.

### Execution flow (`__main__`)
1. `load_config()` — reads `config.json`, returns the `config` object
2. Iterates over each account in `config.authentication.accounts`
3. `get_auth_headers(account, grant_type)` — authenticates against `api.starlink.com` and returns bearer token headers
4. `get_starshield_data(headers, account)` — runs the full data collection pipeline for that account and returns a list of enriched terminal dicts
5. All account results are merged into `all_terms` and written to `_allterms.json`

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
  client_secret     — OAuth secret
  accountquery
    mode            — "full" | "include" | "skip"
    service_lines[] — required (non-empty) when mode is "include"
config.authentication.grant_type  — always "client_credentials"
config.cef                        — CEF message formatting (enable flag)
config.remote_server              — remote syslog destination (enable flag)
```

### Error handling strategy
- `/account` or `/contacts` failure → return `[]` for that account (fatal, can't build dataset)
- `/user-terminals` failure → return `[]` for that account (fatal, nothing to enrich)
- `/data-usage` or `/telemetry` failure → log error, continue (terminals returned without that data)
- Per-account exceptions in `__main__` → logged and skipped, loop continues
