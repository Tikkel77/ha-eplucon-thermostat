# AGENTS.md

## Scope and Repos
- This repo (`Eplucon/`) is the **library/CLI/webapp** workspace.
- The Home Assistant custom integration lives in a separate repo at `C:\Users\bart\.config\opencode\ha-eplucon-thermostat`.
- Scripts in this repo (`upload_to_ha.py`, `deploy_dashboard.py`, `create_schedule_helpers.py`, `create_automation.py`) operate on that HA repo and a live HA instance.

## High-Value Entry Points
- `eplucon/client.py`: source of truth for Eplucon read/write behavior.
- `eplucon/cli.py`: authoritative command surface (`python -m eplucon.cli ...`).
- `eplucon/webapp.py`: test UI + debounce + schedule editing flow.
- `eplucon/settings.py`: config resolution order and env override logic.
- `deploy_dashboard.py`: generates and deploys Lovelace dashboard JSON via HA WebSocket API.
- `create_schedule_helpers.py`: provisions all required `input_*` helpers in HA.

## Config and Secrets Handling
- Config lookup order is enforced in code (`eplucon/settings.py`):
  1. `--config` path
  2. `EPLUCON_CONFIG`
  3. `./eplucon.toml`
  4. `~/.eplucon.toml`
- Env vars override TOML values (`env_or_config`).
- Never commit real credentials/tokens/passwords/host secrets in docs, scripts, or config examples.

## Verified Developer Commands
- Install deps: `pip install -r requirements.txt`
- Run webapp: `python -m eplucon.webapp`
- CLI help: `python -m eplucon.cli --help`
- List zones: `python -m eplucon.cli list-zones`
- Set temporary temperature: `python -m eplucon.cli set-time-limit --zone-api-id <id> --temp <c> --hours <h> --minutes <m>`
- Deploy integration files to HA: `python upload_to_ha.py`
- Deploy dashboard config to HA: `python deploy_dashboard.py`
- Provision schedule helper entities in HA: `python create_schedule_helpers.py`

## Runtime/Behavior Quirks That Matter
- Writes are intentionally serialized/slow; avoid firing concurrent write sequences.
- Portal propagation is delayed; UI/HA verification should allow for delayed confirmation.
- Dashboard deploy script depends on HA custom cards/services (`button-card`, `mini-graph-card`, `layout-card`, `browser_mod`). If those are missing/unloaded, the dashboard appears broken even when JSON deploy succeeds.
- `mini-graph-card` can show stale per-device history due to browser-side cache; prefer explicit cache disabling in card config when troubleshooting data mismatch between clients.

## HA Operations Notes
- `upload_to_ha.py` uses SSH + base64 streaming and clears `__pycache__` before upload; this is the expected deploy path.
- If HA addons fail to start with Docker `input/output error` or recorder reports sqlite corruption, treat as host filesystem/storage issue first (not integration code regression).

## Documentation Reliability
- `README.md` is useful for command discovery, but some docs in `docs/` are historical.
- Prefer executable truth in `eplucon/*.py` and deployment scripts over prose when they conflict.
